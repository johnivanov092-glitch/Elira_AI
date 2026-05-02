# -*- coding: utf-8 -*-
"""Application-layer runtime for the persona service.

Owns ``build_persona_prompt`` — assembles the LLM system prompt from the
active persona snapshot, profile overlay, and model calibration.  All
other persona functions are direct aliases to ``application/persona``
sub-packages.

Note: the original ``services/persona_service.py`` had corrupted
Cyrillic in several f-string literals (vowels `a/e/и/т/ч/…` replaced by
Latin-supplement codepoints due to a CP1252 round-trip).  The prompt
strings below are the corrected valid-UTF-8 versions.
"""
from __future__ import annotations

from copy import deepcopy

from app.application.persona import evolution as persona_evolution
from app.application.persona import store as persona_store
from app.core.persona_defaults import (
    DEFAULT_MODEL_CALIBRATION,
    DEFAULT_PROFILE,
    ELIRA_PERSONA_BASE_PAYLOAD,
    PROFILE_MODE_OVERLAYS,
)
from app.application.elira_memory_sqlite.runtime import DB_PATH  # noqa: F401  (re-exported for callers)


# ── direct aliases to application/persona sub-packages ───────────────────────

get_persona_version = persona_store.get_persona_version
get_model_calibration = persona_store.get_model_calibration
list_persona_candidates = persona_store.list_persona_candidates
get_persona_status = persona_store.get_persona_status
init_persona_store = persona_store.init_persona_store
observe_dialogue = persona_evolution.observe_dialogue
rollback_persona = persona_evolution.rollback_persona


# ── public API ────────────────────────────────────────────────────────────────

def build_persona_prompt(
    profile_name: str,
    model_name: str = "",
    task_context: str = "",
) -> str:
    """Assemble the full LLM system prompt for the active persona snapshot.

    Merges the versioned persona payload with the profile overlay and model
    calibration into a multi-section prompt string.
    """
    snapshot = get_persona_version()
    payload = deepcopy(snapshot.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD)
    profile_key = (
        profile_name
        if profile_name in PROFILE_MODE_OVERLAYS
        else DEFAULT_PROFILE
    )
    overlay = PROFILE_MODE_OVERLAYS[profile_key]
    calibration = get_model_calibration(
        model_name,
        version_id=int(snapshot.get("version", 1) or 1),
    )
    calibration_payload = calibration.get("calibration") or deepcopy(
        DEFAULT_MODEL_CALIBRATION
    )

    values = "\n".join(f"- {item}" for item in payload.get("values", []))
    voice = "\n".join(f"- {item}" for item in payload.get("voice", []))
    rules = "\n".join(f"- {item}" for item in payload.get("behavior_rules", []))
    preferences = "\n".join(f"- {item}" for item in payload.get("preferences", []))
    boundaries = "\n".join(f"- {item}" for item in payload.get("boundaries", []))
    tool_style = "\n".join(f"- {item}" for item in payload.get("tool_style", []))
    disallowed = "\n".join(
        f"- {item}" for item in payload.get("disallowed_drift", [])
    )

    # Runtime identity constraints (corrected from mojibake in original file)
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


# ── module-level bootstrap (mirrors original service) ────────────────────────

persona_store.bootstrap_if_needed()
