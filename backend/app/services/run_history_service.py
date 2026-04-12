from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.data_files import json_data_file, sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite


DB_PATH = sqlite_data_file("run_history.db", key_tables=("run_history",))
LEGACY_JSON_PATHS = [
    json_data_file("run_history.json"),
    Path(__file__).resolve().parents[2] / "data" / "run_history.json",
]
_MAX_RUNS = 200


def _connect() -> sqlite3.Connection:
    return connect_sqlite(
        DB_PATH,
        row_factory=sqlite3.Row,
        journal_mode=None,
    )


def _load_legacy_runs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [item for item in payload.values() if isinstance(item, dict)]
    return []


def _rotate(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
    overflow = total - _MAX_RUNS
    if overflow <= 0:
        return
    conn.execute(
        """
        DELETE FROM run_history
        WHERE id IN (
            SELECT id
            FROM run_history
            ORDER BY finished_at ASC, id ASC
            LIMIT ?
        )
        """,
        (overflow,),
    )


def _init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                user_input TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                ok INTEGER NOT NULL DEFAULT 0,
                route TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                answer_len INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )

        total = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
        if total == 0:
            for path in LEGACY_JSON_PATHS:
                legacy_runs = _load_legacy_runs(path)
                if not legacy_runs:
                    continue
                for index, item in enumerate(legacy_runs[-_MAX_RUNS:]):
                    run_id = str(item.get("run_id") or f"legacy-{index}")
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO run_history (
                            run_id,
                            user_input,
                            started_at,
                            finished_at,
                            ok,
                            route,
                            model,
                            answer_len,
                            error
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            str(item.get("user_input", "")),
                            str(item.get("started_at") or item.get("finished_at") or datetime.utcnow().isoformat()),
                            str(item.get("finished_at") or item.get("started_at") or datetime.utcnow().isoformat()),
                            1 if item.get("ok") else 0,
                            str(item.get("route", "")),
                            str(item.get("model", "")),
                            int(item.get("answer_len") or 0),
                            str(item.get("error", "")),
                        ),
                    )
                break

        _rotate(conn)
        conn.commit()
    finally:
        conn.close()


_init_db()


class RunHistoryService:
    def __init__(self) -> None:
        self._active_runs: dict[str, dict[str, Any]] = {}

    def start_run(self, user_input: str) -> dict[str, Any]:
        run = {
            "run_id": str(uuid.uuid4())[:8],
            "user_input": user_input or "",
            "started_at": datetime.utcnow().isoformat(),
            "events": [],
        }
        self._active_runs[run["run_id"]] = dict(run)
        return run

    def add_event(self, run_id: str, event_type: str, data: Any) -> None:
        active = self._active_runs.get(run_id)
        if active is not None:
            active.setdefault("events", []).append(
                {
                    "event_type": event_type,
                    "data": data,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    def finish_run(self, run_id: str, result: dict[str, Any]) -> None:
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        active = self._active_runs.pop(run_id, {})
        entry = {
            "run_id": run_id,
            "user_input": str(result.get("user_input") or active.get("user_input", "")) if isinstance(result, dict) else str(active.get("user_input", "")),
            "started_at": str(result.get("started_at") or active.get("started_at") or datetime.utcnow().isoformat()) if isinstance(result, dict) else str(active.get("started_at") or datetime.utcnow().isoformat()),
            "finished_at": datetime.utcnow().isoformat(),
            "ok": 1 if result.get("ok", False) else 0,
            "route": str(meta.get("route", "")),
            "model": str(meta.get("model_name", meta.get("model", ""))),
            "answer_len": len(str(result.get("answer", ""))),
            "error": str(result.get("error", "")),
        }

        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO run_history (
                    run_id,
                    user_input,
                    started_at,
                    finished_at,
                    ok,
                    route,
                    model,
                    answer_len,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    user_input = excluded.user_input,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    ok = excluded.ok,
                    route = excluded.route,
                    model = excluded.model,
                    answer_len = excluded.answer_len,
                    error = excluded.error
                """,
                (
                    entry["run_id"],
                    entry["user_input"],
                    entry["started_at"],
                    entry["finished_at"],
                    entry["ok"],
                    entry["route"],
                    entry["model"],
                    entry["answer_len"],
                    entry["error"],
                ),
            )
            _rotate(conn)
            conn.commit()
        finally:
            conn.close()

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT run_id, user_input, started_at, finished_at, ok, route, model, answer_len, error
                FROM run_history
                ORDER BY finished_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(row) for row in rows]
        items.reverse()
        return items
