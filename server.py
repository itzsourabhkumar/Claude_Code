"""Local dashboard server for the Claude Code token tracker.

Serves the static dashboard plus a small read-only JSON API over the SQLite
index. It binds to 127.0.0.1 only and is never reachable from the network.

    python server.py            # http://127.0.0.1:8765
    python server.py --port 9000
    python server.py --no-browser

Endpoints
---------
    GET /api/summary    totals for the current filter (summary cards)
    GET /api/projects   per-project aggregates, with percentage of usage
    GET /api/usage      per-day aggregates (daily chart)
    GET /api/models     per-model aggregates
    GET /api/prompts    paginated interaction history
    GET /api/filters    values for the dropdowns (projects, models, years)
    GET /api/meta       last-updated timestamp and tracker settings
    GET /api/export.csv, /api/export.json   current filter, all matching rows
    GET /api/refresh    re-scan the JSONL tree into the index

All endpoints accept: from, to, range, year, month, project, model, search,
origin, session_id.
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
from tracker.config import load_config  # noqa: E402
from tracker.database import Database  # noqa: E402
from tracker.query import build_filters, normalise_totals, with_percentages  # noqa: E402
from tracker.utils import get_logger, now_local  # noqa: E402

#: Never widen this. The dashboard is a local tool and exposes prompt text.
BIND_HOST = "127.0.0.1"

DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"

CSV_COLUMNS = [
    "timestamp", "date", "project", "git_branch", "model", "session_id",
    "origin", "prompt", "prompt_length",
    "input_tokens", "output_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "total_tokens",
]


class _State:
    """Shared, thread-safe access to the index."""

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.db = Database(config.index_path, check_same_thread=False)
        self.log = get_logger("server", config.logs_dir)

    def close(self) -> None:
        with self.lock:
            self.db.close()


STATE: _State | None = None


def _flatten_record(record: Dict[str, Any]) -> Dict[str, Any]:
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
            summary["filters"] = filters
            summary["generated_at"] = now_local().isoformat(timespec="seconds")
            self._json(summary)

        elif route == "/api/projects":
            with STATE.lock:
                rows = [normalise_totals(r) for r in STATE.db.by_project(filters)]
            self._json({"rows": with_percentages(rows)})

        elif route == "/api/usage":
            with STATE.lock:
                rows = [normalise_totals(r) for r in STATE.db.by_date(filters)]
            self._json({"rows": rows})

        elif route == "/api/models":
            with STATE.lock:
                rows = [normalise_totals(r) for r in STATE.db.by_model(filters)]
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
            body = json.dumps(
                {"exported_at": now_local().isoformat(timespec="seconds"),
                 "filters": filters, "count": len(records), "interactions": records},
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
            writer.writerow(_flatten_record(record))
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


def main(argv: List[str] | None = None) -> int:
    global STATE

    config = load_config()
    parser = argparse.ArgumentParser(description="Claude Code usage dashboard (localhost only)")
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--reindex", action="store_true", help="rebuild the index before serving")
    args = parser.parse_args(argv)

    STATE = _State(config)
    if args.reindex or STATE.db.is_empty():
        stats = STATE.db.reindex(config.usage_dir, full=args.reindex)
        print("Indexed %d record(s) from %d file(s)." % (stats["records"], stats["files"]))

    httpd = ThreadingHTTPServer((BIND_HOST, args.port), Handler)
    httpd.daemon_threads = True
    url = "http://%s:%d" % (BIND_HOST, args.port)

    print("Claude Code Token Usage dashboard")
    print("  serving : " + url)
    print("  data    : " + str(config.usage_dir))
    print("  index   : " + str(config.index_path))
    print("  press Ctrl+C to stop")

    if not args.no_browser and os.environ.get("CCTRACKER_NO_BROWSER") != "1":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()
        STATE.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
