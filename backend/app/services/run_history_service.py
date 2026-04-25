from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.application.run_history import store as run_history_store
from app.application.run_history.runtime import RunHistoryService as RunHistoryRuntime
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
    return run_history_store.load_legacy_runs(path)


def _rotate(conn: sqlite3.Connection) -> None:
    run_history_store.rotate_runs(conn=conn, max_runs=_MAX_RUNS)


def _init_db() -> None:
    run_history_store.init_db(
        connect_func=_connect,
        legacy_json_paths=LEGACY_JSON_PATHS,
        max_runs=_MAX_RUNS,
        load_legacy_runs_func=_load_legacy_runs,
        rotate_func=_rotate,
    )


_init_db()


class RunHistoryService(RunHistoryRuntime):
    def __init__(self) -> None:
        super().__init__(
            connect_func=_connect,
            rotate_func=_rotate,
            now_func=lambda: datetime.utcnow().isoformat(),
        )
