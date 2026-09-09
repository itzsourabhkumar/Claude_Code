"""Local dashboard server for the Claude Code token tracker.

Serves the static dashboard plus a small read-only JSON API over the SQLite
index. It binds to 127.0.0.1 only and is never reachable from the network.

    python server.py            # http://127.0.0.1:8765   (python3 on Linux/macOS)
    python server.py --port 9000
    python server.py --no-browser

The convenience launchers (``start_dashboard.bat`` / ``start_dashboard.ps1`` on
Windows, ``start_dashboard.sh`` on Linux and macOS) do nothing but run this file
with the project's interpreter, so behaviour is identical on all three.

Endpoints
---------
    GET /api/summary    totals for the current filter (summary cards)
    GET /api/projects   per-project aggregates, with percentage of usage
    GET /api/usage      per-day aggregates (daily chart)
    GET /api/models     per-model aggregates
    GET /api/prompts    paginated interaction history
    GET /api/filters    values for the dropdowns (projects, models, years)
    GET /api/pricing    the price table, exchange rate and assumptions in use
    GET /api/meta       last-updated timestamp and tracker settings
    GET /api/export.csv, /api/export.json   current filter, all matching rows
    GET /api/refresh    re-scan the JSONL tree into the index

All endpoints accept: from, to, range, year, month, project, model, search,
origin, session_id.

Every aggregate endpoint also returns cost, split into input / cached input /
output and totalled in INR (and USD). Costs are derived at request time from the
stored token counts - see tracker/pricing.py - so no stored data changes and the
existing token fields are returned exactly as before.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracker import TRACKER_VERSION  # noqa: E402
from tracker.config import InvalidConfig, load_config  # noqa: E402
from tracker.database import Database  # noqa: E402
from tracker.platform_utils import (  # noqa: E402
    describe_platform,
    is_loopback_host,
    port_is_free,
    python_command_name,
)
from tracker.pricing import PRICING_AS_OF, PriceBook  # noqa: E402
from tracker.query import (  # noqa: E402
    build_filters,
    cost_summary,
    normalise_totals,
    with_date_costs,
    with_model_costs,
    with_percentages,
    with_project_costs,
    with_row_costs,
)
from tracker.utils import get_logger, now_local  # noqa: E402

#: Fallback bind address. Any configured or requested host is checked against
#: :func:`is_loopback_host` before it is used - the dashboard exposes prompt text
#: and has no authentication, so it must never be reachable from the network.
DEFAULT_BIND_HOST = "127.0.0.1"

DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"

#: Pre-existing export columns. Cost columns are appended after these, never
#: inserted among them, so a script reading the old CSV keeps working.
CSV_TOKEN_COLUMNS = [
    "timestamp", "date", "project", "git_branch", "model", "session_id",
    "origin", "prompt", "prompt_length",
    "input_tokens", "output_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "total_tokens",
]

CSV_COST_COLUMNS = [
    "input_cost_inr", "cached_input_cost_inr", "output_cost_inr",
    "total_cost_inr", "total_cost_usd",
]

CSV_COLUMNS = CSV_TOKEN_COLUMNS + CSV_COST_COLUMNS


class _State:
    """Shared, thread-safe access to the index."""

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.db = Database(config.index_path, check_same_thread=False)
        self.log = get_logger("server", config.logs_dir)
        # Prices never change during a run, so resolve them once.
        self.prices = PriceBook.from_config(config)

    def close(self) -> None:
        with self.lock:
            self.db.close()


STATE: _State | None = None


def _flatten_record(record: Dict[str, Any], prices: Any = None) -> Dict[str, Any]:
    """Flatten a stored record into the flat shape used for CSV export."""
    usage = record.get("usage") or {}
    flat = {key: record.get(key) for key in CSV_COLUMNS if key in record}
    flat.update({key: usage.get(key) for key in (
        "input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens", "total_tokens")})
    for key in CSV_COLUMNS:
        flat.setdefault(key, record.get(key))
    if flat.get("prompt") is None:
        # Prompt storage may be disabled; the hash still identifies the prompt.
        flat["prompt"] = record.get("prompt_hash") or ""
    if prices is not None:
        cost = prices.cost_for(usage, record.get("model"))
        for column in CSV_COST_COLUMNS:
            flat[column] = cost.get(column)
    return flat


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeCodeUsageTracker/" + TRACKER_VERSION
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if STATE:
            STATE.log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str, extra: Dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # A purely local tool: keep the browser from caching stale usage.
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message, "status": status}, status)

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            route = unquote(parsed.path)
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            if route.startswith("/api/"):
                self._api(route, params)
            else:
                self._static(route)
        except BrokenPipeError:
            pass
        except Exception as exc:  # a dashboard bug must not kill the server
            if STATE:
                STATE.log.exception("request failed: %s", exc)
            try:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            except Exception:
                pass

    do_HEAD = do_GET

    # -- static files ------------------------------------------------------
    def _static(self, route: str) -> None:
        if route in ("/", "", "/index.html"):
            target = DASHBOARD_DIR / "index.html"
        else:
            # Resolve inside the dashboard directory only - no traversal out.
            candidate = (DASHBOARD_DIR / route.lstrip("/")).resolve()
            try:
                candidate.relative_to(DASHBOARD_DIR.resolve())
            except ValueError:
                self._error(HTTPStatus.FORBIDDEN, "forbidden")
                return
            target = candidate

        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript", "application/json"
        ):
            content_type += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    # -- API ---------------------------------------------------------------
    def _api(self, route: str, params: Dict[str, str]) -> None:
        assert STATE is not None
        filters = build_filters(params)

        if route == "/api/summary":
            with STATE.lock:
                summary = normalise_totals(STATE.db.summary(filters))
                # Priced per model, then summed - a selection spanning two
                # models has no single rate.
                summary["cost"] = cost_summary(STATE.db, filters, STATE.prices)
            summary["filters"] = filters
            summary["generated_at"] = now_local().isoformat(timespec="seconds")
            self._json(summary)

        elif route == "/api/projects":
            with STATE.lock:
                rows = [normalise_totals(r) for r in STATE.db.by_project(filters)]
                rows = with_project_costs(rows, STATE.db, filters, STATE.prices)
            self._json({"rows": with_percentages(rows)})

        elif route == "/api/usage":
            with STATE.lock:
                rows = [normalise_totals(r) for r in STATE.db.by_date(filters)]
                rows = with_date_costs(rows, STATE.db, filters, STATE.prices)
            self._json({"rows": rows})

        elif route == "/api/models":
            with STATE.lock:
                rows = [normalise_totals(r) for r in STATE.db.by_model(filters)]
                rows = with_model_costs(rows, STATE.prices)
            self._json({"rows": with_percentages(rows)})

        elif route == "/api/prompts":
            page = max(1, _int(params.get("page"), 1))
            per_page = _int(params.get("per_page"), 25)
            with STATE.lock:
                result = STATE.db.prompts(
                    filters,
                    sort=params.get("sort", "timestamp"),
                    direction=params.get("dir", "desc"),
                    limit=per_page,
                    offset=(page - 1) * per_page,
                )
            # Each history row is one interaction from one model, so it can be
            # priced directly rather than grouped first.
            result["rows"] = with_row_costs(result["rows"], STATE.prices)
            result["page"] = page
            result["pages"] = max(1, -(-result["total"] // result["limit"]))
            self._json(result)

        elif route == "/api/filters":
            with STATE.lock:
                payload = {
                    "projects": STATE.db.distinct("project"),
                    "models": STATE.db.distinct("model"),
                    "branches": STATE.db.distinct("git_branch"),
                    "origins": STATE.db.distinct("origin"),
                    "years": STATE.db.available_years(),
                }
            self._json(payload)

        elif route == "/api/meta":
            with STATE.lock:
                last = STATE.db.last_updated()
                summary = STATE.db.summary({})
            self._json({
                "tracker_version": TRACKER_VERSION,
                "last_interaction": last,
                "generated_at": now_local().isoformat(timespec="seconds"),
                "store_prompt_text": STATE.config.store_prompt_text,
                "usage_dir": str(STATE.config.usage_dir),
                "total_interactions": summary.get("prompts") or 0,
                "first_date": summary.get("first_date"),
                "last_date": summary.get("last_date"),
                # Defaults the dashboard adopts on first load; the page's own
                # controls still win for the rest of the visit.
                "auto_refresh_seconds": STATE.config.auto_refresh_seconds,
                "page_size": STATE.config.page_size,
                "timezone": STATE.config.timezone,
                "platform": describe_platform()["os"],
                "pricing_enabled": STATE.prices.enabled,
                "usd_to_inr": STATE.prices.usd_to_inr,
                "pricing_as_of": PRICING_AS_OF,
            })

        elif route == "/api/pricing":
            # Everything needed to audit a figure shown on the dashboard: the
            # rates used, the exchange rate, the cache-TTL assumption, and how
            # current the table is.
            prices = STATE.prices
            self._json({
                "enabled": prices.enabled,
                "currency": "INR",
                "usd_to_inr": prices.usd_to_inr,
                "cache_write_ttl": prices.cache_write_ttl,
                "pricing_as_of": PRICING_AS_OF,
                "models": {name: prices.prices[name] for name in prices.known_models()},
                "note": (
                    "Costs are derived from stored token counts at request time; "
                    "no cost is written to disk. Prices are USD per million tokens."
                ),
            })

        elif route == "/api/refresh":
            with STATE.lock:
                stats = STATE.db.reindex(STATE.config.usage_dir, full=False)
            self._json({"reindexed": stats, "at": now_local().isoformat(timespec="seconds")})

        elif route in ("/api/export.json", "/api/export.csv"):
            self._export(route, filters)

        else:
            self._error(HTTPStatus.NOT_FOUND, "unknown endpoint " + route)

    def _export(self, route: str, filters: Dict[str, Any]) -> None:
        assert STATE is not None
        stamp = now_local().strftime("%Y%m%d-%H%M%S")
        with STATE.lock:
            records: List[Dict[str, Any]] = list(STATE.db.all_matching(filters))

        if route.endswith(".json"):
            with STATE.lock:
                totals = cost_summary(STATE.db, filters, STATE.prices)
            body = json.dumps(
                {"exported_at": now_local().isoformat(timespec="seconds"),
                 "filters": filters, "count": len(records), "cost": totals,
                 "interactions": records},
                ensure_ascii=False, indent=2,
            ).encode("utf-8")
            self._send(
                HTTPStatus.OK, body, "application/json; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="claude-usage-%s.json"' % stamp},
            )
            return

        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(_flatten_record(record, STATE.prices))
        body = buffer.getvalue().encode("utf-8-sig")  # BOM so Excel reads UTF-8
        self._send(
            HTTPStatus.OK, body, "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="claude-usage-%s.csv"' % stamp},
        )


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_bind_host(requested: str | None, config) -> str:
    """The address to bind, refusing anything reachable from the network."""
    if requested:
        if not is_loopback_host(requested):
            raise InvalidConfig(
                "--host %s is not a loopback address. The dashboard serves your "
                "prompt text with no authentication, so it may only bind to "
                "127.0.0.1, ::1 or localhost." % requested
            )
        return requested
    try:
        return config.host
    except InvalidConfig as exc:
        raise InvalidConfig(str(exc)) from None


def main(argv: List[str] | None = None) -> int:
    global STATE

    config = load_config()
    parser = argparse.ArgumentParser(
        description="Claude Code usage dashboard (localhost only)"
    )
    parser.add_argument("--port", type=int, default=None,
                        help="port to listen on (default: config.json server.port)")
    parser.add_argument("--host", default=None,
                        help="loopback address to bind (default: config.json server.host)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--reindex", action="store_true", help="rebuild the index before serving")
    args = parser.parse_args(argv)

    try:
        host = resolve_bind_host(args.host, config)
    except InvalidConfig as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2
    port = args.port if args.port is not None else config.port

    config.ensure_directories()
    STATE = _State(config)
    if args.reindex or STATE.db.is_empty():
        stats = STATE.db.reindex(config.usage_dir, full=args.reindex)
        print("Indexed %d record(s) from %d file(s)." % (stats["records"], stats["files"]))

    # Checked up front so a busy port produces one clear line rather than a
    # traceback - the most common first-run problem on every platform.
    if not port_is_free(host, port):
        print(
            "Port %d on %s is already in use.\n"
            "  Another dashboard may already be running - open http://%s:%d\n"
            "  Otherwise start this one on a free port:  %s server.py --port %d"
            % (port, host, host, port, python_command_name(), port + 1),
            file=sys.stderr,
        )
        STATE.close()
        return 1

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print("Could not start the dashboard on %s:%d - %s" % (host, port, exc),
              file=sys.stderr)
        STATE.close()
        return 1
    httpd.daemon_threads = True
    url = "http://%s:%d" % (host, port)

    print("Claude Code Token Usage dashboard")
    print("  serving : " + url)
    print("  data    : " + str(config.usage_dir))
    print("  index   : " + str(config.index_path))
    print("  press Ctrl+C to stop")

    open_browser = config.open_browser and not args.no_browser
    if open_browser and os.environ.get("CCTRACKER_NO_BROWSER") != "1":
        # webbrowser picks the right mechanism per platform (start / open /
        # xdg-open) and simply does nothing on a headless box.
        threading.Timer(0.6, lambda: _open_browser_quietly(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()
        STATE.close()
    return 0


def _open_browser_quietly(url: str) -> None:
    """Open a browser, ignoring the failure headless machines produce."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
