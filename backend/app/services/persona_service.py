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
    PERSONA_PROMOTION_RULES,
    PROFILE_MODE_OVERLAYS,
    PROFILE_UI,
)
from app.infrastructure.db.connection import connect_sqlite
from app.services.elira_memory_sqlite import DB_PATH, init_db as init_state_db


logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _json_loads(value: Any, fallback: Any) -> Any:
    if not value:
        return deepcopy(fallback)
    try:
        return json.loads(value)
    except Exception:
        return deepcopy(fallback)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _audit(conn: sqlite3.Connection, event_type: str, *, version: int | None = None, trait_key: str | None = None, payload: Any = None) -> None:
    payload_json = _json_dumps(payload or {})
    conn.execute(
        """
        INSERT INTO persona_audit_log(event_type, version, trait_key, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_type, version, trait_key, payload_json, _utc_now()),
    )
    logger.info("persona event=%s version=%s trait=%s payload=%s", event_type, version, trait_key, payload_json)


def _ensure_tables() -> None:
    init_state_db()
    conn = _connect()
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


def _bootstrap_if_needed() -> None:
    _ensure_tables()
    conn = _connect()
    try:
        row = conn.execute("SELECT version FROM persona_versions ORDER BY version DESC LIMIT 1").fetchone()
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
                _utc_now(),
                _utc_now(),
                _json_dumps(source),
                _json_dumps(payload),
                "Bootstrap persona v1 from universal Elira core.",
            ),
        )
        conn.commit()
        _audit(conn, "persona_bootstrapped", version=1, payload=source)
        conn.commit()
    finally:
        conn.close()


def _row_to_version(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["payload"] = _json_loads(data.pop("payload_json", "{}"), {})
    data["source"] = _json_loads(data.get("source"), {})
    return data


def get_persona_version(version: int | None = None) -> dict[str, Any]:
    _bootstrap_if_needed()
    conn = _connect()
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
    return _row_to_version(row) or {}


def _get_previous_version(conn: sqlite3.Connection, active_version: int) -> int | None:
    row = conn.execute(
        "SELECT version FROM persona_versions WHERE version < ? ORDER BY version DESC LIMIT 1",
        (active_version,),
    ).fetchone()
    return int(row["version"]) if row else None


def get_model_calibration(model_name: str, version_id: int | None = None) -> dict[str, Any]:
    _bootstrap_if_needed()
    active = get_persona_version() if version_id is None else None
    version_value = version_id or int(active.get("version", 1) or 1)
    model_key = (model_name or "default").strip() or "default"
    conn = _connect()
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
            data["calibration"] = _json_loads(data.pop("calibration_json", "{}"), DEFAULT_MODEL_CALIBRATION)
            return data

        payload = deepcopy(DEFAULT_MODEL_CALIBRATION)
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO persona_model_calibrations(model, version_id, calibration_json, consistency_score, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (model_key, version_value, _json_dumps(payload), 1.0, now),
        )
        conn.commit()
        _audit(conn, "model_calibration_updated", version=version_value, trait_key=model_key, payload=payload)
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


def build_persona_prompt(profile_name: str, model_name: str = "", task_context: str = "") -> str:
    snapshot = get_persona_version()
    payload = deepcopy(snapshot.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD)
    profile_key = profile_name if profile_name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE
    overlay = PROFILE_MODE_OVERLAYS[profile_key]
    calibration = get_model_calibration(model_name, version_id=int(snapshot.get("version", 1) or 1))
    calibration_payload = calibration.get("calibration") or deepcopy(DEFAULT_MODEL_CALIBRATION)

    values = "\n".join(f"- {item}" for item in payload.get("values", []))
    voice = "\n".join(f"- {item}" for item in payload.get("voice", []))
    rules = "\n".join(f"- {item}" for item in payload.get("behavior_rules", []))
    preferences = "\n".join(f"- {item}" for item in payload.get("preferences", []))
    boundaries = "\n".join(f"- {item}" for item in payload.get("boundaries", []))
    tool_style = "\n".join(f"- {item}" for item in payload.get("tool_style", []))
    disallowed = "\n".join(f"- {item}" for item in payload.get("disallowed_drift", []))
    runtime = [
        "Факты, RAG и память расширяют знания, но не меняют личность Elira.",
        "Профили — это режимы поведения одной Elira, а не отдельные персонажи.",
        "Особенности модели могут менять форму ответа, но не должны ломать голос Elira.",
        "Ты всегда представляешься как Elira.",
        "В обычном чате не называй себя именем модели и не описывай себя как LLM или языковую модель.",
        "Если пользователь спрашивает, кто ты или как тебя зовут, отвечай только как Elira.",
    ]
    if task_context.strip():
        runtime.append(task_context.strip())

    return "\n\n".join(
        [
            f"Ты — Elira. Активная версия личности: v{snapshot.get('version', 1)}.",
            f"Идентичность: {payload.get('identity', {}).get('continuity', '')}",
            f"Миссия: {payload.get('identity', {}).get('mission', '')}",
            f"Голос:\n{voice}",
            f"Ценности:\n{values}",
            f"Правила поведения:\n{rules}",
            f"Предпочтения ответа:\n{preferences}",
            f"Стиль работы с инструментами:\n{tool_style}",
            f"Границы:\n{boundaries}",
            f"Недопустимый дрейф:\n{disallowed}",
            f"Режим профиля ({profile_key}): {overlay}",
            "Калибровка модели:\n"
            f"- verbosity: {calibration_payload.get('verbosity', 'balanced')}\n"
            f"- formatting: {calibration_payload.get('formatting', 'structured')}\n"
            f"- list_bias: {calibration_payload.get('list_bias', 'moderate')}",
            "Runtime constraints:\n" + "\n".join(f"- {item}" for item in runtime),
        ]
    )


def list_persona_candidates(limit: int = 20) -> list[dict[str, Any]]:
    _bootstrap_if_needed()
    conn = _connect()
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
    items = []
    for row in rows:
        item = dict(row)
        item["candidate"] = _json_loads(item.pop("candidate_json", "{}"), {})
        items.append(item)
    return items


def _extract_signals(profile_name: str, user_input: str, answer_text: str) -> dict[str, list[dict[str, Any]]]:
    user_lower = (user_input or "").lower()
    answer_lower = (answer_text or "").lower()
    combined = f"{user_lower}\n{answer_lower}"
    persona: list[dict[str, Any]] = []
    calibration: list[dict[str, Any]] = []

    if any(token in combined for token in ("помогу", "давай", "следующий шаг", "шаги")):
        persona.append({"trait_key": "supportive_guidance", "layer": "behavior_rules", "confidence": 0.82, "summary": "Поддержка и понятные следующие шаги."})
    if any(token in combined for token in ("структур", "1.", "2.", "итог", "вывод")):
        persona.append({"trait_key": "structured_clarity", "layer": "preferences", "confidence": 0.79, "summary": "Структурированный и ясный ответ."})
    if any(token in combined for token in ("не уверен", "не знаю", "недостаточно данных", "скажу прямо")):
        persona.append({"trait_key": "transparent_honesty", "layer": "values", "confidence": 0.88, "summary": "Честно обозначает неопределённость."})

    if profile_name == "Программист" and any(token in combined for token in ("```", "патч", "рефактор", "код")):
        persona.append({"trait_key": "code_first_precision", "layer": "preferences", "confidence": 0.74, "summary": "Ставит код и надёжность выше общих рассуждений."})
    if profile_name == "Аналитик" and any(token in combined for token in ("риск", "сравн", "альтернатив", "декомпози")):
        persona.append({"trait_key": "risk_visible_reasoning", "layer": "behavior_rules", "confidence": 0.74, "summary": "Показывает риски и варианты явно."})
    if profile_name == "Сократ" and answer_text.count("?") >= 2:
        persona.append({"trait_key": "guided_questions", "layer": "behavior_rules", "confidence": 0.73, "summary": "Ведёт через вопросы и уточнение мысли."})

    answer_len = len(answer_text or "")
    bullet_count = answer_text.count("\n- ") + answer_text.count("\n1.")
    if answer_len > 2200:
        calibration.append({"trait_key": "trim_verbosity", "confidence": 0.76, "patch": {"verbosity": "compact"}})
    if answer_len > 900 and bullet_count == 0:
        calibration.append({"trait_key": "increase_structure", "confidence": 0.74, "patch": {"formatting": "more_structured"}})
    if bullet_count > 14:
        calibration.append({"trait_key": "reduce_list_bias", "confidence": 0.77, "patch": {"list_bias": "low"}})

    return {
        "persona": persona,
        "knowledge": [],
        "user_preference": [],
        "model_calibration": calibration,
        "ephemeral": [],
    }


def _contradiction_score(snapshot: dict[str, Any], summary: str) -> float:
    blocked = [item.lower() for item in snapshot.get("disallowed_drift", [])]
    summary_lower = (summary or "").lower()
    return 1.0 if any(item in summary_lower for item in blocked) else 0.0


def _candidate_event_stats(conn: sqlite3.Connection, trait_key: str) -> tuple[int, int]:
    rows = conn.execute(
        "SELECT dialog_id, session_id, extracted_json FROM persona_learning_events ORDER BY id DESC LIMIT 500"
    ).fetchall()
    dialog_ids: set[str] = set()
    session_ids: set[str] = set()
    for row in rows:
        data = _json_loads(row["extracted_json"], {})
        for item in data.get("persona", []):
            if item.get("trait_key") == trait_key:
                dialog_ids.add(str(row["dialog_id"]))
                session_ids.add(str(row["session_id"]))
    return len(dialog_ids), len(session_ids)


def _append_trait(payload: dict[str, Any], layer: str, summary: str) -> bool:
    current = list(payload.get(layer, []))
    if summary in current:
        return False
    current.append(summary)
    payload[layer] = current
    return True


def _promote_candidate(conn: sqlite3.Connection, candidate_row: sqlite3.Row) -> int | None:
    active = _row_to_version(
        conn.execute(
            "SELECT * FROM persona_versions WHERE status = 'active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
    )
    if not active:
        return None
    candidate = _json_loads(candidate_row["candidate_json"], {})
    snapshot = deepcopy(active.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD)
    if not _append_trait(snapshot, candidate_row["layer"], candidate.get("summary", candidate_row["trait_key"])):
        return None

    next_version = int(active["version"]) + 1
    now = _utc_now()
    conn.execute("UPDATE persona_versions SET status = 'archived' WHERE version = ?", (active["version"],))
    conn.execute(
        """
        INSERT INTO persona_versions(version, status, parent_version, created_at, promoted_at, source, payload_json, diff_summary)
        VALUES (?, 'active', ?, ?, ?, ?, ?, ?)
        """,
        (
            next_version,
            active["version"],
            now,
            now,
            _json_dumps({"source": "candidate_promotion", "trait_key": candidate_row["trait_key"]}),
            _json_dumps(snapshot),
            f"Accepted trait {candidate_row['trait_key']}: {candidate.get('summary', '')}",
        ),
    )
    calibration_rows = conn.execute(
        """
        SELECT model, calibration_json, consistency_score
        FROM persona_model_calibrations
        WHERE version_id = ?
        """,
        (active["version"],),
    ).fetchall()
    for calibration_row in calibration_rows:
        conn.execute(
            """
            INSERT INTO persona_model_calibrations(model, version_id, calibration_json, consistency_score, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                calibration_row["model"],
                next_version,
                calibration_row["calibration_json"],
                calibration_row["consistency_score"],
                now,
            ),
        )
    conn.execute(
        """
        UPDATE persona_candidates
        SET status = 'promoted', promoted_version = ?, last_seen = ?
        WHERE id = ?
        """,
        (next_version, now, candidate_row["id"]),
    )
    _audit(conn, "persona_promoted", version=next_version, trait_key=candidate_row["trait_key"], payload=candidate)
    return next_version


def _maybe_promote_candidates(conn: sqlite3.Connection) -> int | None:
    rows = conn.execute(
        "SELECT * FROM persona_candidates WHERE status = 'quarantine' ORDER BY confidence_avg DESC, evidence_count DESC"
    ).fetchall()
    active = _row_to_version(
        conn.execute(
            "SELECT * FROM persona_versions WHERE status = 'active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
    ) or {}
    payload = active.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD
    for row in rows:
        dialogs, sessions = _candidate_event_stats(conn, row["trait_key"])
        if dialogs < PERSONA_PROMOTION_RULES["min_dialogs"]:
            continue
        if sessions < PERSONA_PROMOTION_RULES["min_sessions"]:
            continue
        if float(row["confidence_avg"]) < PERSONA_PROMOTION_RULES["min_confidence_avg"]:
            continue
        if float(row["contradiction_score"]) > PERSONA_PROMOTION_RULES["max_contradiction_score"]:
            conn.execute(
                "UPDATE persona_candidates SET status = 'rejected', last_seen = ? WHERE id = ?",
                (_utc_now(), row["id"]),
            )
            _audit(conn, "candidate_rejected", version=active.get("version"), trait_key=row["trait_key"], payload={"reason": "contradiction"})
            continue
        if _contradiction_score(payload, _json_loads(row["candidate_json"], {}).get("summary", "")) > PERSONA_PROMOTION_RULES["max_contradiction_score"]:
            continue
        promoted = _promote_candidate(conn, row)
        if promoted:
            return promoted
    return None


def _update_model_calibration(conn: sqlite3.Connection, model_name: str, version_id: int, signals: list[dict[str, Any]]) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT calibration_json, consistency_score, updated_at
        FROM persona_model_calibrations
        WHERE model = ? AND version_id = ?
        LIMIT 1
        """,
        (model_name or "default", version_id),
    ).fetchone()
    calibration = _json_loads(row["calibration_json"], DEFAULT_MODEL_CALIBRATION) if row else deepcopy(DEFAULT_MODEL_CALIBRATION)
    changed = False
    for item in signals:
        patch = item.get("patch") or {}
        for key, value in patch.items():
            if calibration.get(key) != value:
                calibration[key] = value
                changed = True
    consistency = round(max(0.55, 1.0 - 0.05 * len(signals)), 2)
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO persona_model_calibrations(model, version_id, calibration_json, consistency_score, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(model, version_id)
        DO UPDATE SET calibration_json = excluded.calibration_json,
                      consistency_score = excluded.consistency_score,
                      updated_at = excluded.updated_at
        """,
        (model_name or "default", version_id, _json_dumps(calibration), consistency, now),
    )
    if changed:
        _audit(conn, "model_calibration_updated", version=version_id, trait_key=model_name or "default", payload=calibration)
    return {"model": model_name or "default", "version_id": version_id, "calibration": calibration, "consistency_score": consistency, "updated_at": now}


def observe_dialogue(
    *,
    dialog_id: str,
    session_id: str,
    profile_name: str,
    model_name: str,
    user_input: str,
    answer_text: str,
    route: str = "",
    reflection: dict[str, Any] | None = None,
    outcome_ok: bool = True,
) -> dict[str, Any]:
    _bootstrap_if_needed()
    profile_name = profile_name if profile_name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE
    active = get_persona_version()
    version_id = int(active.get("version", 1) or 1)
    extracted = _extract_signals(profile_name, user_input, answer_text)
    if reflection:
        extracted["ephemeral"].append({"reflection": reflection})
    if route:
        extracted["ephemeral"].append({"route": route})

    persona_items = extracted.get("persona", [])
    persona_score = round(sum(float(item.get("confidence", 0.0)) for item in persona_items), 3)
    outcome_score = 1.0 if outcome_ok else 0.25

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO persona_learning_events(dialog_id, session_id, profile, model, extracted_json, persona_score, outcome_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(dialog_id),
                str(session_id),
                profile_name or DEFAULT_PROFILE,
                model_name or "default",
                _json_dumps(extracted),
                persona_score,
                outcome_score,
                _utc_now(),
            ),
        )

        for item in persona_items:
            summary = item.get("summary", item.get("trait_key", ""))
            contradiction = _contradiction_score(active.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD, summary)
            row = conn.execute(
                """
                SELECT * FROM persona_candidates
                WHERE trait_key = ? AND layer = ? AND status = 'quarantine'
                LIMIT 1
                """,
                (item["trait_key"], item["layer"]),
            ).fetchone()
            now = _utc_now()
            if row:
                evidence_count = int(row["evidence_count"]) + 1
                confidence_avg = round(
                    ((float(row["confidence_avg"]) * int(row["evidence_count"])) + float(item["confidence"])) / evidence_count,
                    3,
                )
                candidate_payload = _json_loads(row["candidate_json"], {})
                candidate_payload.update({"summary": summary, "last_profile": profile_name or DEFAULT_PROFILE})
                conn.execute(
                    """
                    UPDATE persona_candidates
                    SET candidate_json = ?, evidence_count = ?, confidence_avg = ?, contradiction_score = ?, last_seen = ?
                    WHERE id = ?
                    """,
                    (_json_dumps(candidate_payload), evidence_count, confidence_avg, contradiction, now, row["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO persona_candidates(trait_key, layer, candidate_json, evidence_count, confidence_avg, contradiction_score, first_seen, last_seen, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'quarantine')
                    """,
                    (
                        item["trait_key"],
                        item["layer"],
                        _json_dumps({"summary": summary, "last_profile": profile_name or DEFAULT_PROFILE}),
                        1,
                        float(item["confidence"]),
                        contradiction,
                        now,
                        now,
                    ),
                )
                _audit(conn, "candidate_created", version=version_id, trait_key=item["trait_key"], payload=item)

        calibration = _update_model_calibration(conn, model_name, version_id, extracted.get("model_calibration", []))
        promoted_version = _maybe_promote_candidates(conn)
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "active_version": promoted_version or version_id,
        "promoted_version": promoted_version,
        "persona_signals": len(persona_items),
        "model_calibration": calibration,
        "extracted": extracted,
    }


def rollback_persona(version: int) -> dict[str, Any]:
    _bootstrap_if_needed()
    conn = _connect()
    try:
        target = conn.execute(
            "SELECT * FROM persona_versions WHERE version = ? LIMIT 1",
            (int(version),),
        ).fetchone()
        if not target:
            raise ValueError(f"Persona version {version} not found")
        conn.execute("UPDATE persona_versions SET status = 'archived' WHERE status = 'active'")
        conn.execute(
            "UPDATE persona_versions SET status = 'active', promoted_at = ? WHERE version = ?",
            (_utc_now(), int(version)),
        )
        _audit(conn, "rollback_applied", version=int(version), payload={"rolled_back_to": int(version)})
        conn.commit()
    finally:
        conn.close()
    return get_persona_status()


def get_persona_status() -> dict[str, Any]:
    _bootstrap_if_needed()
    active = get_persona_version()
    conn = _connect()
    try:
        quarantine_count = int(
            conn.execute("SELECT COUNT(*) FROM persona_candidates WHERE status = 'quarantine'").fetchone()[0]
        )
        previous_version = _get_previous_version(conn, int(active.get("version", 1) or 1))
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

    latest_traits = []
    for row in promoted:
        data = _json_loads(row["candidate_json"], {})
        latest_traits.append(
            {
                "trait_key": row["trait_key"],
                "summary": data.get("summary", row["trait_key"]),
                "promoted_version": row["promoted_version"],
                "last_seen": row["last_seen"],
            }
        )

    model_consistency = []
    for row in calibrations:
        model_consistency.append(
            {
                "model": row["model"],
                "version_id": row["version_id"],
                "consistency_score": row["consistency_score"],
                "updated_at": row["updated_at"],
                "calibration": _json_loads(row["calibration_json"], DEFAULT_MODEL_CALIBRATION),
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
    _bootstrap_if_needed()
    return get_persona_status()


_bootstrap_if_needed()
