from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from app.infrastructure.db.connection import connect_sqlite


TimestampFactory = Callable[[], str]

SETTINGS_DEFAULTS = {
    "active_mem_profile": "default",
    "model": "qwen3:8b",
}


def load_settings(*, settings_path: str | Path) -> dict[str, Any]:
    try:
        path = Path(settings_path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return {**SETTINGS_DEFAULTS, **data}
    except Exception:
        pass
    return dict(SETTINGS_DEFAULTS)


def save_settings(*, settings_path: str | Path, settings: dict[str, Any]) -> None:
    try:
        Path(settings_path).write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def init_db(*, db_path: str | Path, now_iso_func: TimestampFactory) -> None:
    with connect_sqlite(db_path, row_factory=None, journal_mode=None) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                content      TEXT    NOT NULL,
                source       TEXT,
                created_at   TEXT    NOT NULL,
                pinned       INTEGER DEFAULT 0,
                memory_type  TEXT    DEFAULT 'general',
                profile_name TEXT    DEFAULT '',
                content_hash TEXT    DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mem_profiles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                emoji      TEXT DEFAULT '👤',
                created_at TEXT NOT NULL
            )
            """
        )
        for sql in [
            "ALTER TABLE memories ADD COLUMN pinned INTEGER DEFAULT 0",
            "ALTER TABLE memories ADD COLUMN memory_type TEXT DEFAULT 'general'",
            "ALTER TABLE memories ADD COLUMN profile_name TEXT DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN content_hash TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass
        conn.execute(
            "INSERT OR IGNORE INTO mem_profiles (name, emoji, created_at) VALUES (?, ?, ?)",
            ("default", "👤", now_iso_func()),
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_usage (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name    TEXT NOT NULL,
                task_hint    TEXT DEFAULT '',
                ok           INTEGER DEFAULT 1,
                score        REAL DEFAULT 1.0,
                notes        TEXT DEFAULT '',
                created_at   TEXT NOT NULL,
                profile_name TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT DEFAULT '',
                url          TEXT DEFAULT '',
                content      TEXT NOT NULL,
                source       TEXT DEFAULT '',
                chunk_type   TEXT DEFAULT 'note',
                created_at   TEXT NOT NULL,
                profile_name TEXT DEFAULT '',
                content_hash TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                task_text TEXT,
                route_mode TEXT,
                graph_used TEXT,
                final_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                task_text TEXT,
                answer_text TEXT,
                answered INTEGER,
                grounded INTEGER,
                complete INTEGER,
                actionable INTEGER,
                safe INTEGER,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS working_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                profile_name TEXT,
                step_name TEXT,
                fact_type TEXT,
                content TEXT,
                score REAL DEFAULT 1.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS self_improve_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                task_text TEXT,
                iteration INTEGER,
                answer_text TEXT,
                critique_json TEXT,
                reflection_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS v8_strategy_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                route_mode TEXT DEFAULT '',
                task_hint TEXT DEFAULT '',
                ok INTEGER DEFAULT 1,
                score REAL DEFAULT 1.0,
                latency REAL DEFAULT 0.0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                profile_name TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_learning_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT DEFAULT '',
                url TEXT DEFAULT '',
                title TEXT DEFAULT '',
                source_kind TEXT DEFAULT 'web',
                ok INTEGER DEFAULT 1,
                saved_kb INTEGER DEFAULT 0,
                saved_memory INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                profile_name TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_compaction_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                source_count INTEGER DEFAULT 0,
                summary_count INTEGER DEFAULT 0,
                deleted_count INTEGER DEFAULT 0,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
