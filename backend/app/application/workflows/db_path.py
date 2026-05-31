from __future__ import annotations

from pathlib import Path

from app.core.data_files import sqlite_data_file


_WORKFLOW_DB_PATH: Path = sqlite_data_file("workflow_engine.db")


def get_workflow_db_path() -> Path:
    return _WORKFLOW_DB_PATH


def set_workflow_db_path(path: str | Path) -> Path:
    global _WORKFLOW_DB_PATH
    _WORKFLOW_DB_PATH = Path(path)
    return _WORKFLOW_DB_PATH
