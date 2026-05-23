from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app.core.data_files import DATA_DIR
from app.core.web import get_web_engine_status
from app.infrastructure.db.elira_memory_sqlite import DB_PATH, count_chats, init_db


ROOT_DATA_DIR = DATA_DIR.resolve()
ACTIVE_DB_PATH = Path(DB_PATH).resolve()


def _active_persona_version() -> int:
    try:
        conn = sqlite3.connect(str(ACTIVE_DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT version FROM persona_versions WHERE status = 'active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return int(row["version"]) if row else 1
    except sqlite3.Error:
        return 1


def init_runtime_state() -> dict[str, Any]:
    init_db()
    return get_runtime_status()


def _storage_mode() -> str:
    if ACTIVE_DB_PATH.parent != ROOT_DATA_DIR:
        return "custom_data_dir"
    return "rooted_sqlite"


def _chat_count_for(path: Path) -> int:
    try:
        return count_chats(path)
    except sqlite3.Error:
        return 0


def get_runtime_status() -> dict[str, Any]:
    init_db()
    web_status = get_web_engine_status()
    active_chat_count = _chat_count_for(ACTIVE_DB_PATH)
    warnings = list(web_status.get("warnings", []))
    warning_text = " | ".join(warnings) if warnings else None

    return {
        "ok": True,
        "python_executable": sys.executable,
        "process_id": os.getpid(),
        "cwd": os.getcwd(),
        "data_dir": str(ROOT_DATA_DIR),
        "active_db_path": str(ACTIVE_DB_PATH),
        "active_chat_count": active_chat_count,
        "storage_mode": _storage_mode(),
        "persona_version": _active_persona_version(),
        "backend_origin": str((ROOT_DATA_DIR.parent / "backend").resolve()),
        "primary_engine": web_status.get("primary_engine"),
        "fallback_engines": web_status.get("fallback_engines", []),
        "available_engines": web_status.get("available_engines", []),
        "supported_engines": web_status.get("supported_engines", []),
        "api_keys_present": web_status.get("api_keys_present", {}),
        "degraded_mode": bool(web_status.get("degraded_mode")),
        "web_warnings": warnings,
        "warning": warning_text,
    }
