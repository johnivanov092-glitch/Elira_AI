from __future__ import annotations

import sqlite3
from copy import deepcopy
from typing import Any

from app.application.persona import store as persona_store
from app.core.persona_defaults import (
    DEFAULT_MODEL_CALIBRATION,
    DEFAULT_PROFILE,
    ELIRA_PERSONA_BASE_PAYLOAD,
    PERSONA_PROMOTION_RULES,
    PROFILE_MODE_OVERLAYS,
)


def extract_signals(
    profile_name: str,
    user_input: str,
    answer_text: str,
) -> dict[str, list[dict[str, Any]]]:
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


def contradiction_score(snapshot: dict[str, Any], summary: str) -> float:
    blocked = [item.lower() for item in snapshot.get("disallowed_drift", [])]
    summary_lower = (summary or "").lower()
    return 1.0 if any(item in summary_lower for item in blocked) else 0.0


def candidate_event_stats(
    conn: sqlite3.Connection,
    trait_key: str,
) -> tuple[int, int]:
    rows = conn.execute(
        "SELECT dialog_id, session_id, extracted_json FROM persona_learning_events ORDER BY id DESC LIMIT 500"
    ).fetchall()
    dialog_ids: set[str] = set()
    session_ids: set[str] = set()
    for row in rows:
        data = persona_store.json_loads(row["extracted_json"], {})
        for item in data.get("persona", []):
            if item.get("trait_key") == trait_key:
                dialog_ids.add(str(row["dialog_id"]))
                session_ids.add(str(row["session_id"]))
    return len(dialog_ids), len(session_ids)


def append_trait(payload: dict[str, Any], layer: str, summary: str) -> bool:
    current = list(payload.get(layer, []))
    if summary in current:
        return False
    current.append(summary)
    payload[layer] = current
    return True


def promote_candidate(
    conn: sqlite3.Connection,
    candidate_row: sqlite3.Row,
) -> int | None:
    active = persona_store.row_to_version(
        conn.execute(
            "SELECT * FROM persona_versions WHERE status = 'active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
    )
    if not active:
        return None

    candidate = persona_store.json_loads(candidate_row["candidate_json"], {})
    snapshot = deepcopy(active.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD)
    if not append_trait(
        snapshot,
        candidate_row["layer"],
        candidate.get("summary", candidate_row["trait_key"]),
    ):
        return None

    next_version = int(active["version"]) + 1
    now = persona_store.utc_now()
    conn.execute(
        "UPDATE persona_versions SET status = 'archived' WHERE version = ?",
        (active["version"],),
    )
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
            persona_store.json_dumps(
                {
                    "source": "candidate_promotion",
                    "trait_key": candidate_row["trait_key"],
                }
            ),
            persona_store.json_dumps(snapshot),
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
    persona_store.audit(
        conn,
        "persona_promoted",
        version=next_version,
        trait_key=candidate_row["trait_key"],
        payload=candidate,
    )
    return next_version


def maybe_promote_candidates(conn: sqlite3.Connection) -> int | None:
    rows = conn.execute(
        "SELECT * FROM persona_candidates WHERE status = 'quarantine' ORDER BY confidence_avg DESC, evidence_count DESC"
    ).fetchall()
    active = persona_store.row_to_version(
        conn.execute(
            "SELECT * FROM persona_versions WHERE status = 'active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
    ) or {}
    payload = active.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD
    for row in rows:
        dialogs, sessions = candidate_event_stats(conn, row["trait_key"])
        if dialogs < PERSONA_PROMOTION_RULES["min_dialogs"]:
            continue
        if sessions < PERSONA_PROMOTION_RULES["min_sessions"]:
            continue
        if float(row["confidence_avg"]) < PERSONA_PROMOTION_RULES["min_confidence_avg"]:
            continue
        if float(row["contradiction_score"]) > PERSONA_PROMOTION_RULES["max_contradiction_score"]:
            conn.execute(
                "UPDATE persona_candidates SET status = 'rejected', last_seen = ? WHERE id = ?",
                (persona_store.utc_now(), row["id"]),
            )
            persona_store.audit(
                conn,
                "candidate_rejected",
                version=active.get("version"),
                trait_key=row["trait_key"],
                payload={"reason": "contradiction"},
            )
            continue
        if contradiction_score(
            payload,
            persona_store.json_loads(row["candidate_json"], {}).get("summary", ""),
        ) > PERSONA_PROMOTION_RULES["max_contradiction_score"]:
            continue
        promoted = promote_candidate(conn, row)
        if promoted:
            return promoted
    return None


def update_model_calibration(
    conn: sqlite3.Connection,
    model_name: str,
    version_id: int,
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    model_key = model_name or "default"
    row = conn.execute(
        """
        SELECT calibration_json, consistency_score, updated_at
        FROM persona_model_calibrations
        WHERE model = ? AND version_id = ?
        LIMIT 1
        """,
        (model_key, version_id),
    ).fetchone()
    calibration = (
        persona_store.json_loads(row["calibration_json"], DEFAULT_MODEL_CALIBRATION)
        if row
        else deepcopy(DEFAULT_MODEL_CALIBRATION)
    )
    changed = False
    for item in signals:
        patch = item.get("patch") or {}
        for key, value in patch.items():
            if calibration.get(key) != value:
                calibration[key] = value
                changed = True
    consistency = round(max(0.55, 1.0 - 0.05 * len(signals)), 2)
    now = persona_store.utc_now()
    conn.execute(
        """
        INSERT INTO persona_model_calibrations(model, version_id, calibration_json, consistency_score, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(model, version_id)
        DO UPDATE SET calibration_json = excluded.calibration_json,
                      consistency_score = excluded.consistency_score,
                      updated_at = excluded.updated_at
        """,
        (
            model_key,
            version_id,
            persona_store.json_dumps(calibration),
            consistency,
            now,
        ),
    )
    if changed:
        persona_store.audit(
            conn,
            "model_calibration_updated",
            version=version_id,
            trait_key=model_key,
            payload=calibration,
        )
    return {
        "model": model_key,
        "version_id": version_id,
        "calibration": calibration,
        "consistency_score": consistency,
        "updated_at": now,
    }


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
    persona_store.bootstrap_if_needed()
    profile_key = (
        profile_name
        if profile_name in PROFILE_MODE_OVERLAYS
        else DEFAULT_PROFILE
    )
    active = persona_store.get_persona_version()
    version_id = int(active.get("version", 1) or 1)
    extracted = extract_signals(profile_key, user_input, answer_text)
    if reflection:
        extracted["ephemeral"].append({"reflection": reflection})
    if route:
        extracted["ephemeral"].append({"route": route})

    persona_items = extracted.get("persona", [])
    persona_score = round(
        sum(float(item.get("confidence", 0.0)) for item in persona_items),
        3,
    )
    outcome_score = 1.0 if outcome_ok else 0.25

    conn = persona_store.connect()
    try:
        conn.execute(
            """
            INSERT INTO persona_learning_events(dialog_id, session_id, profile, model, extracted_json, persona_score, outcome_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(dialog_id),
                str(session_id),
                profile_key,
                model_name or "default",
                persona_store.json_dumps(extracted),
                persona_score,
                outcome_score,
                persona_store.utc_now(),
            ),
        )

        for item in persona_items:
            summary = item.get("summary", item.get("trait_key", ""))
            contradiction = contradiction_score(
                active.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD,
                summary,
            )
            row = conn.execute(
                """
                SELECT * FROM persona_candidates
                WHERE trait_key = ? AND layer = ? AND status = 'quarantine'
                LIMIT 1
                """,
                (item["trait_key"], item["layer"]),
            ).fetchone()
            now = persona_store.utc_now()
            if row:
                evidence_count = int(row["evidence_count"]) + 1
                confidence_avg = round(
                    (
                        (float(row["confidence_avg"]) * int(row["evidence_count"]))
                        + float(item["confidence"])
                    )
                    / evidence_count,
                    3,
                )
                candidate_payload = persona_store.json_loads(
                    row["candidate_json"],
                    {},
                )
                candidate_payload.update(
                    {"summary": summary, "last_profile": profile_key}
                )
                conn.execute(
                    """
                    UPDATE persona_candidates
                    SET candidate_json = ?, evidence_count = ?, confidence_avg = ?, contradiction_score = ?, last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        persona_store.json_dumps(candidate_payload),
                        evidence_count,
                        confidence_avg,
                        contradiction,
                        now,
                        row["id"],
                    ),
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
                        persona_store.json_dumps(
                            {
                                "summary": summary,
                                "last_profile": profile_key,
                            }
                        ),
                        1,
                        float(item["confidence"]),
                        contradiction,
                        now,
                        now,
                    ),
                )
                persona_store.audit(
                    conn,
                    "candidate_created",
                    version=version_id,
                    trait_key=item["trait_key"],
                    payload=item,
                )

        calibration = update_model_calibration(
            conn,
            model_name,
            version_id,
            extracted.get("model_calibration", []),
        )
        promoted_version = maybe_promote_candidates(conn)
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
    persona_store.bootstrap_if_needed()
    conn = persona_store.connect()
    try:
        target = conn.execute(
            "SELECT * FROM persona_versions WHERE version = ? LIMIT 1",
            (int(version),),
        ).fetchone()
        if not target:
            raise ValueError(f"Persona version {version} not found")
        conn.execute(
            "UPDATE persona_versions SET status = 'archived' WHERE status = 'active'"
        )
        conn.execute(
            "UPDATE persona_versions SET status = 'active', promoted_at = ? WHERE version = ?",
            (persona_store.utc_now(), int(version)),
        )
        persona_store.audit(
            conn,
            "rollback_applied",
            version=int(version),
            payload={"rolled_back_to": int(version)},
        )
        conn.commit()
    finally:
        conn.close()
    return persona_store.get_persona_status()
