"""Persistence for the drift detector's verified live facts.

One upsert row per fact key: the last verified value + when, plus the previous
value + when it changed — enough to render "was X, live = Y since DATE" without
a history table. Non-sensitive (model path, ctx size) so no encryption.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH = sqlite_data_file("drift_facts.db")


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row)


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verified_facts (
                key TEXT PRIMARY KEY,
                value TEXT,
                verified_at TEXT DEFAULT CURRENT_TIMESTAMP,
                previous_value TEXT,
                changed_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


init_db()


def get_fact(key: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM verified_facts WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def all_facts() -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM verified_facts ORDER BY key").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_fact(key: str, value: str | None) -> dict[str, Any]:
    """Record the current value. Returns {key, changed, previous, current}.

    ``changed`` is True only when a *previously recorded* value differs from the
    new one — a first observation is not a drift.
    """
    conn = _conn()
    try:
        row = conn.execute("SELECT value FROM verified_facts WHERE key = ?", (key,)).fetchone()
        prev = row["value"] if row else None
        changed = row is not None and prev != value
        if row is None:
            conn.execute(
                "INSERT INTO verified_facts(key, value, verified_at) VALUES(?, ?, CURRENT_TIMESTAMP)",
                (key, value),
            )
        elif changed:
            conn.execute(
                """
                UPDATE verified_facts
                SET value = ?, verified_at = CURRENT_TIMESTAMP,
                    previous_value = ?, changed_at = CURRENT_TIMESTAMP
                WHERE key = ?
                """,
                (value, prev, key),
            )
        else:
            conn.execute(
                "UPDATE verified_facts SET verified_at = CURRENT_TIMESTAMP WHERE key = ?",
                (key,),
            )
        conn.commit()
        return {"key": key, "changed": changed, "previous": prev, "current": value}
    finally:
        conn.close()
