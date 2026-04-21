from __future__ import annotations

import json
import logging
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.core.persona_defaults import (
    DEFAULT_MODEL_CALIBRATION,
    DEFAULT_PROFILE,
    ELIRA_PERSONA_BASE_PAYLOAD,
    PROFILE_UI,
)
from app.infrastructure.db.connection import connect_sqlite
from app.services.elira_memory_sqlite import DB_PATH, init_db as init_state_db


logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def json_loads(value: Any, fallback: Any) -> Any:
    if not value:
        return deepcopy(fallback)
    try:
        return json.loads(value)
    except Exception:
        return deepcopy(fallback)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def audit(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    version: int | None = None,
    trait_key: str | None = None,
    payload: Any = None,
) -> None:
    payload_json = json_dumps(payload or {})
    conn.execute(
        """
        INSERT INTO persona_audit_log(event_type, version, trait_key, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_type, version, trait_key, payload_json, utc_now()),
    )
    logger.info(
        "persona event=%s version=%s trait=%s payload=%s",
        event_type,
        version,
        trait_key,
        payload_json,
    )


def ensure_tables() -> None:
    init_state_db()
    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS persona_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL,
                parent_version INTEGER,
                created_at TEXT NOT NULL,
                promoted_at TEXT,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                diff_summary TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS persona_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trait_key TEXT NOT NULL,
                layer TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                confidence_avg REAL NOT NULL DEFAULT 0,
                contradiction_score REAL NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'quarantine',
                promoted_version INTEGER
            );
            CREATE TABLE IF NOT EXISTS persona_learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dialog_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                model TEXT NOT NULL,
                extracted_json TEXT NOT NULL,
                persona_score REAL NOT NULL DEFAULT 0,
                outcome_score REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS persona_model_calibrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                version_id INTEGER NOT NULL,
                calibration_json TEXT NOT NULL,
                consistency_score REAL NOT NULL DEFAULT 1.0,
                updated_at TEXT NOT NULL,
                UNIQUE(model, version_id)
            );
            CREATE TABLE IF NOT EXISTS persona_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                version INTEGER,
                trait_key TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def bootstrap_if_needed() -> None:
    ensure_tables()
    conn = connect()
    try:
        row = conn.execute(
            "SELECT version FROM persona_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row:
            return

        payload = deepcopy(ELIRA_PERSONA_BASE_PAYLOAD)
        source = {"bootstrap": "persona_v1", "profile": DEFAULT_PROFILE}
        conn.execute(
            """
            INSERT INTO persona_versions(version, status, parent_version, created_at, promoted_at, source, payload_json, diff_summary)
            VALUES (1, 'active', NULL, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                utc_now(),
                json_dumps(source),
                json_dumps(payload),
                "Bootstrap persona v1 from universal Elira core.",
            ),
        )
        conn.commit()
        audit(conn, "persona_bootstrapped", version=1, payload=source)
        conn.commit()
    finally:
        conn.close()


def row_to_version(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["payload"] = json_loads(data.pop("payload_json", "{}"), {})
    data["source"] = json_loads(data.get("source"), {})
    return data


def get_persona_version(version: int | None = None) -> dict[str, Any]:
    bootstrap_if_needed()
    conn = connect()
    try:
        if version is None:
            row = conn.execute(
                "SELECT * FROM persona_versions WHERE status = 'active' ORDER BY version DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM persona_versions WHERE version = ? LIMIT 1",
                (int(version),),
            ).fetchone()
    finally:
        conn.close()
    return row_to_version(row) or {}


def get_previous_version(conn: sqlite3.Connection, active_version: int) -> int | None:
    row = conn.execute(
        "SELECT version FROM persona_versions WHERE version < ? ORDER BY version DESC LIMIT 1",
        (active_version,),
    ).fetchone()
    return int(row["version"]) if row else None


def get_model_calibration(
    model_name: str,
    version_id: int | None = None,
) -> dict[str, Any]:
    bootstrap_if_needed()
    active = get_persona_version() if version_id is None else None
    version_value = version_id or int(active.get("version", 1) or 1)
    model_key = (model_name or "default").strip() or "default"
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT * FROM persona_model_calibrations
            WHERE model = ? AND version_id = ?
            LIMIT 1
            """,
            (model_key, version_value),
        ).fetchone()
        if row:
            data = dict(row)
            data["calibration"] = json_loads(
                data.pop("calibration_json", "{}"),
                DEFAULT_MODEL_CALIBRATION,
            )
            return data

        payload = deepcopy(DEFAULT_MODEL_CALIBRATION)
        now = utc_now()
        conn.execute(
            """
            INSERT INTO persona_model_calibrations(model, version_id, calibration_json, consistency_score, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (model_key, version_value, json_dumps(payload), 1.0, now),
        )
        conn.commit()
        audit(
            conn,
            "model_calibration_updated",
            version=version_value,
            trait_key=model_key,
            payload=payload,
        )
        conn.commit()
        return {
            "model": model_key,
            "version_id": version_value,
            "calibration": payload,
            "consistency_score": 1.0,
            "updated_at": now,
        }
    finally:
        conn.close()


def list_persona_candidates(limit: int = 20) -> list[dict[str, Any]]:
    bootstrap_if_needed()
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM persona_candidates
            WHERE status = 'quarantine'
            ORDER BY confidence_avg DESC, evidence_count DESC, last_seen DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["candidate"] = json_loads(item.pop("candidate_json", "{}"), {})
        items.append(item)
    return items


def get_persona_status() -> dict[str, Any]:
    bootstrap_if_needed()
    active = get_persona_version()
    conn = connect()
    try:
        quarantine_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM persona_candidates WHERE status = 'quarantine'"
            ).fetchone()[0]
        )
        previous_version = get_previous_version(
            conn,
            int(active.get("version", 1) or 1),
        )
        promoted = conn.execute(
            """
            SELECT trait_key, candidate_json, promoted_version, last_seen
            FROM persona_candidates
            WHERE status = 'promoted'
            ORDER BY COALESCE(promoted_version, 0) DESC, last_seen DESC
            LIMIT 5
            """
        ).fetchall()
        calibrations = conn.execute(
            """
            SELECT model, version_id, calibration_json, consistency_score, updated_at
            FROM persona_model_calibrations
            WHERE version_id = ?
            ORDER BY updated_at DESC
            """,
            (int(active.get("version", 1) or 1),),
        ).fetchall()
    finally:
        conn.close()

    latest_traits: list[dict[str, Any]] = []
    for row in promoted:
        data = json_loads(row["candidate_json"], {})
        latest_traits.append(
            {
                "trait_key": row["trait_key"],
                "summary": data.get("summary", row["trait_key"]),
                "promoted_version": row["promoted_version"],
                "last_seen": row["last_seen"],
            }
        )

    model_consistency: list[dict[str, Any]] = []
    for row in calibrations:
        model_consistency.append(
            {
                "model": row["model"],
                "version_id": row["version_id"],
                "consistency_score": row["consistency_score"],
                "updated_at": row["updated_at"],
                "calibration": json_loads(
                    row["calibration_json"],
                    DEFAULT_MODEL_CALIBRATION,
                ),
            }
        )

    return {
        "ok": True,
        "persona_name": active.get("payload", {}).get("identity", {}).get("name", "Elira"),
        "active_version": int(active.get("version", 1) or 1),
        "status": active.get("status", "active"),
        "last_evolution_at": active.get("promoted_at") or active.get("created_at"),
        "quarantine_candidates": quarantine_count,
        "previous_version": previous_version,
        "latest_traits": latest_traits,
        "model_consistency": model_consistency,
        "profiles": PROFILE_UI,
    }


def init_persona_store() -> dict[str, Any]:
    bootstrap_if_needed()
    return get_persona_status()

