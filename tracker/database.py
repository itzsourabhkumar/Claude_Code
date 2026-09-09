"""SQLite index over the JSONL usage files.

Why an index at all? The JSONL tree is the source of truth and stays fully
readable on its own, but answering "sum tokens per project for the last 30 days"
by walking thousands of files is linear in total history. SQLite gives the
dashboard indexed date/project/model lookups and keeps memory flat regardless of
how much history accumulates.

The index is disposable: ``python -m tracker.cli reindex`` rebuilds it from the
JSONL files at any time, so losing or deleting ``data/index.sqlite3`` costs
nothing but the rebuild.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .storage import PROMPTS_FILENAME, iter_day_dirs, iter_records
from .utils import iter_jsonl, parse_timestamp

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id                          TEXT PRIMARY KEY,
    revision                    INTEGER NOT NULL DEFAULT 0,
    timestamp                   TEXT,
    ts_epoch                    REAL,
    date                        TEXT,
    year                        INTEGER,
    month                       INTEGER,
    day                         INTEGER,
    project                     TEXT,
    working_directory           TEXT,
    git_repository              TEXT,
    git_branch                  TEXT,
    session_id                  TEXT,
    prompt_id                   TEXT,
    model                       TEXT,
    models                      TEXT,
    origin                      TEXT,
    prompt_source               TEXT,
    prompt                      TEXT,
    prompt_length               INTEGER,
    prompt_hash                 TEXT,
    input_tokens                INTEGER,
    output_tokens               INTEGER,
    cache_read_input_tokens     INTEGER,
    cache_creation_input_tokens INTEGER,
    total_tokens                INTEGER,
    assistant_messages          INTEGER,
    claude_code_version         TEXT,
    entrypoint                  TEXT,
    raw                         TEXT
);
CREATE INDEX IF NOT EXISTS idx_date    ON interactions(date);
CREATE INDEX IF NOT EXISTS idx_project ON interactions(project);
CREATE INDEX IF NOT EXISTS idx_model   ON interactions(model);
CREATE INDEX IF NOT EXISTS idx_epoch   ON interactions(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_session ON interactions(session_id);

CREATE TABLE IF NOT EXISTS sources (
    path     TEXT PRIMARY KEY,
    size     INTEGER,
    mtime    REAL,
    scanned  REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_COLUMNS = (
    "id", "revision", "timestamp", "ts_epoch", "date", "year", "month", "day",
    "project", "working_directory", "git_repository", "git_branch",
    "session_id", "prompt_id", "model", "models", "origin", "prompt_source",
    "prompt", "prompt_length", "prompt_hash",
    "input_tokens", "output_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "total_tokens", "assistant_messages",
    "claude_code_version", "entrypoint", "raw",
)

#: Sort keys the dashboard may request, mapped to safe SQL expressions.
SORTABLE = {
    "timestamp": "ts_epoch",
    "project": "project",
    "model": "model",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_tokens": "(COALESCE(cache_read_input_tokens,0)+COALESCE(cache_creation_input_tokens,0))",
    "total_tokens": "total_tokens",
}

_TOTALS_SQL = """
    COUNT(*)                                        AS prompts,
    SUM(CASE WHEN origin='human' THEN 1 ELSE 0 END) AS human_prompts,
    SUM(input_tokens)                               AS input_tokens,
    SUM(output_tokens)                              AS output_tokens,
    SUM(cache_read_input_tokens)                    AS cache_read_input_tokens,
    SUM(cache_creation_input_tokens)                AS cache_creation_input_tokens,
    SUM(COALESCE(cache_read_input_tokens,0)
        + COALESCE(cache_creation_input_tokens,0))  AS cache_tokens,
    SUM(total_tokens)                               AS total_tokens
"""


def record_to_row(record: Dict[str, Any]) -> Optional[Tuple]:
    """Flatten a stored interaction into a row tuple, or None if malformed."""
    if not isinstance(record, dict) or not record.get("id"):
        return None
    usage = record.get("usage") or {}
    moment = parse_timestamp(record.get("timestamp"))
    models = record.get("models")
    return (
        record.get("id"),
        int(record.get("revision") or 0),
        record.get("timestamp"),
        moment.timestamp() if moment else None,
        record.get("date"),
        record.get("year"),
        record.get("month"),
        record.get("day"),
        record.get("project"),
        record.get("working_directory"),
        record.get("git_repository"),
        record.get("git_branch"),
        record.get("session_id"),
        record.get("prompt_id"),
        record.get("model"),
        json.dumps(models, ensure_ascii=False) if models else None,
        record.get("origin"),
        record.get("prompt_source"),
        record.get("prompt"),
        record.get("prompt_length"),
        record.get("prompt_hash"),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("cache_creation_input_tokens"),
        usage.get("total_tokens"),
        record.get("assistant_messages"),
        record.get("claude_code_version"),
        record.get("entrypoint"),
        json.dumps(record, ensure_ascii=False),
    )


class Database:
    """Thin wrapper around the SQLite index."""

    def __init__(self, path: Path, check_same_thread: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.path), timeout=15.0, check_same_thread=check_same_thread
        )
        self.conn.row_factory = sqlite3.Row
        # WAL lets the dashboard read while a hook writes.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        self.conn.executescript(_SCHEMA)
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    # -- writes ------------------------------------------------------------
    def upsert(self, records: Iterable[Dict[str, Any]]) -> int:
        """Insert records, keeping the highest revision for any given id."""
        rows = [row for row in (record_to_row(r) for r in records) if row]
        if not rows:
            return 0
        placeholders = ",".join("?" * len(_COLUMNS))
        sql = (
            "INSERT INTO interactions (%s) VALUES (%s) "
            "ON CONFLICT(id) DO UPDATE SET %s WHERE excluded.revision >= interactions.revision"
            % (
                ",".join(_COLUMNS),
                placeholders,
                ",".join("%s=excluded.%s" % (c, c) for c in _COLUMNS if c != "id"),
            )
        )
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def has_id(self, interaction_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM interactions WHERE id=? LIMIT 1", (interaction_id,)
        )
        return cur.fetchone() is not None

    def revision_of(self, interaction_id: str) -> Optional[int]:
        cur = self.conn.execute(
            "SELECT revision FROM interactions WHERE id=?", (interaction_id,)
        )
        row = cur.fetchone()
        return int(row["revision"]) if row else None

    def existing_ids(self, ids: Sequence[str]) -> Dict[str, int]:
        """Map of ``id -> revision`` for ids already indexed."""
        found: Dict[str, int] = {}
        for start in range(0, len(ids), 400):
            batch = ids[start : start + 400]
            marks = ",".join("?" * len(batch))
            cur = self.conn.execute(
                "SELECT id, revision FROM interactions WHERE id IN (%s)" % marks, batch
            )
            for row in cur.fetchall():
                found[row["id"]] = int(row["revision"])
        return found

    # -- rebuild -----------------------------------------------------------
    def reindex(self, usage_dir: Path, full: bool = False) -> Dict[str, int]:
        """Rebuild the index from the JSONL tree.

        Unchanged files (same size and mtime as last scan) are skipped unless
        ``full`` is set, which makes routine refreshes cheap.
        """
        usage_dir = Path(usage_dir)
        if full:
            self.conn.execute("DELETE FROM interactions")
            self.conn.execute("DELETE FROM sources")
            self.conn.commit()

        known = {
            row["path"]: (row["size"], row["mtime"])
            for row in self.conn.execute("SELECT path,size,mtime FROM sources")
        }

        files_scanned = 0
        records_indexed = 0
        for _date_str, date_dir in iter_day_dirs(usage_dir):
            for proj_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
                target = proj_dir / PROMPTS_FILENAME
                if not target.is_file():
                    continue
                try:
                    stat = target.stat()
                except OSError:
                    continue
                key = str(target)
                if not full and known.get(key) == (stat.st_size, stat.st_mtime):
                    continue
                records_indexed += self.upsert(iter_jsonl(target))
                files_scanned += 1
                self.conn.execute(
                    "INSERT OR REPLACE INTO sources(path,size,mtime,scanned) VALUES(?,?,?,?)",
                    (key, stat.st_size, stat.st_mtime, stat.st_mtime),
                )
        self.conn.commit()
        return {"files": files_scanned, "records": records_indexed}

    def is_empty(self) -> bool:
        cur = self.conn.execute("SELECT 1 FROM interactions LIMIT 1")
        return cur.fetchone() is None

    # -- queries -----------------------------------------------------------
    @staticmethod
    def _where(filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []

        if filters.get("date_from"):
            clauses.append("date >= ?")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            clauses.append("date <= ?")
            params.append(filters["date_to"])
        if filters.get("year"):
            clauses.append("year = ?")
            params.append(int(filters["year"]))
        if filters.get("month"):
            clauses.append("month = ?")
            params.append(int(filters["month"]))
        if filters.get("project"):
            clauses.append("project = ?")
            params.append(filters["project"])
        if filters.get("model"):
            clauses.append("model = ?")
            params.append(filters["model"])
        if filters.get("session_id"):
            clauses.append("session_id = ?")
            params.append(filters["session_id"])
        if filters.get("origin"):
            clauses.append("origin = ?")
            params.append(filters["origin"])
        if filters.get("search"):
            # Case-insensitive substring match over the stored prompt text.
            clauses.append("instr(lower(COALESCE(prompt,'')), lower(?)) > 0")
            params.append(str(filters["search"]))

        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def summary(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        where, params = self._where(filters)
        row = self.conn.execute(
            "SELECT %s, COUNT(DISTINCT project) AS projects, "
            "COUNT(DISTINCT model) AS models, COUNT(DISTINCT session_id) AS sessions, "
            "MIN(date) AS first_date, MAX(date) AS last_date "
            "FROM interactions%s" % (_TOTALS_SQL, where),
            params,
        ).fetchone()
        return dict(row) if row else {}

    def by_project(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        cur = self.conn.execute(
            "SELECT project, %s FROM interactions%s GROUP BY project "
            "ORDER BY total_tokens DESC" % (_TOTALS_SQL, where),
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    def by_date(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        cur = self.conn.execute(
            "SELECT date, %s FROM interactions%s GROUP BY date ORDER BY date"
            % (_TOTALS_SQL, where),
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    def by_model(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        where, params = self._where(filters)
        cur = self.conn.execute(
            "SELECT model, %s FROM interactions%s GROUP BY model "
            "ORDER BY total_tokens DESC" % (_TOTALS_SQL, where),
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    # -- per-model breakdowns (for costing) --------------------------------
    #
    # Cost cannot be derived from a blended total: a selection spanning Opus and
    # Haiku has no single rate. These group by model *as well as* the dimension
    # asked for, so each model's tokens can be priced at its own rates and only
    # the resulting money summed. See tracker/pricing.py.
    def totals_by_model(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One row per model, for costing a whole filtered selection."""
        return self.by_model(filters)

    def by_project_model(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One row per (project, model)."""
        where, params = self._where(filters)
        cur = self.conn.execute(
            "SELECT project, model, %s FROM interactions%s "
            "GROUP BY project, model ORDER BY project" % (_TOTALS_SQL, where),
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    def by_date_model(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One row per (date, model)."""
        where, params = self._where(filters)
        cur = self.conn.execute(
            "SELECT date, model, %s FROM interactions%s "
            "GROUP BY date, model ORDER BY date" % (_TOTALS_SQL, where),
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    def prompts(
        self,
        filters: Dict[str, Any],
        sort: str = "timestamp",
        direction: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        where, params = self._where(filters)
        total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM interactions" + where, params
        ).fetchone()["n"]

        order_expr = SORTABLE.get(sort, SORTABLE["timestamp"])
        order_dir = "ASC" if str(direction).lower() == "asc" else "DESC"
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))

        cur = self.conn.execute(
            "SELECT id, timestamp, date, project, git_branch, model, models, origin, "
            "prompt, prompt_length, prompt_hash, session_id, "
            "input_tokens, output_tokens, cache_read_input_tokens, "
            "cache_creation_input_tokens, total_tokens, assistant_messages "
            "FROM interactions%s ORDER BY %s %s, id %s LIMIT ? OFFSET ?"
            % (where, order_expr, order_dir, order_dir),
            params + [limit, offset],
        )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            read = item.get("cache_read_input_tokens")
            created = item.get("cache_creation_input_tokens")
            item["cache_tokens"] = (
                None if read is None and created is None else (read or 0) + (created or 0)
            )
            rows.append(item)
        return {"total": total, "rows": rows, "limit": limit, "offset": offset}

    def all_matching(self, filters: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        """Stream full records matching a filter, for export."""
        where, params = self._where(filters)
        cur = self.conn.execute(
            "SELECT raw FROM interactions%s ORDER BY ts_epoch DESC" % where, params
        )
        while True:
            batch = cur.fetchmany(500)
            if not batch:
                return
            for row in batch:
                try:
                    yield json.loads(row["raw"])
                except (ValueError, TypeError):
                    continue

    def distinct(self, column: str) -> List[str]:
        if column not in ("project", "model", "git_branch", "origin"):
            return []
        cur = self.conn.execute(
            "SELECT DISTINCT %s AS v FROM interactions WHERE %s IS NOT NULL "
            "AND %s <> '' ORDER BY v" % (column, column, column)
        )
        return [row["v"] for row in cur.fetchall()]

    def available_years(self) -> List[int]:
        cur = self.conn.execute(
            "SELECT DISTINCT year AS y FROM interactions WHERE year IS NOT NULL ORDER BY y DESC"
        )
        return [int(row["y"]) for row in cur.fetchall()]

    def last_updated(self) -> Optional[str]:
        row = self.conn.execute(
            "SELECT timestamp FROM interactions ORDER BY ts_epoch DESC LIMIT 1"
        ).fetchone()
        return row["timestamp"] if row else None


def open_database(config) -> Database:
    return Database(config.index_path)


def rebuild_from_jsonl(config, full: bool = True) -> Dict[str, int]:
    """Convenience wrapper used by the CLI and the tests."""
    with open_database(config) as db:
        return db.reindex(config.usage_dir, full=full)


def load_records(config, **filters) -> List[Dict[str, Any]]:
    """Read records straight from JSONL, bypassing the index entirely.

    Used by ``reindex --verify`` and by tests that must prove the JSONL tree
    stands on its own.
    """
    from .storage import dedupe_records

    return dedupe_records(
        iter_records(
            config.usage_dir,
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
            projects=filters.get("projects"),
        )
    )
