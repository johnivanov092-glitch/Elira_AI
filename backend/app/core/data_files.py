from __future__ import annotations

import os
from pathlib import Path

from app.core.config import DATA_DIR as CONFIG_DATA_DIR


DATA_DIR = Path(os.getenv("ELIRA_DATA_DIR", str(CONFIG_DATA_DIR))).resolve()


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def data_file(name: str) -> Path:
    _ensure_data_dir()
    return DATA_DIR / name


def data_subdir(name: str) -> Path:
    _ensure_data_dir()
    path = DATA_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def sqlite_data_file(name: str, key_tables=None) -> Path:
    return data_file(name)


def json_data_file(name: str) -> Path:
    return data_file(name)
