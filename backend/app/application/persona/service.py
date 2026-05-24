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
from app.application.elira_memory.service import DB_PATH


get_persona_version = persona_store.get_persona_version
get_model_calibration = persona_store.get_model_calibration
list_persona_candidates = persona_store.list_persona_candidates
get_persona_status = persona_store.get_persona_status
init_persona_store = persona_store.init_persona_store
observe_dialogue = persona_evolution.observe_dialogue
rollback_persona = persona_evolution.rollback_persona


def _short_profile_line(profile_key: str) -> str:
    """One-line profile mode reduced from the multi-sentence overlay."""
    overlay = PROFILE_MODE_OVERLAYS.get(profile_key, "")
    # Original overlays are 2-3 sentences. Take only the first one ("Режим работы: X.")
    # and the next clause if it fits in ~80 chars.
    first = overlay.split(".", 1)[0].strip()
    return first if first else "Режим работы: универсальный"


def _calibration_pragma(payload: dict) -> str:
    return (
        f"[tone:{payload.get('verbosity', 'balanced')} | "
        f"format:{payload.get('formatting', 'structured')} | "
        f"lists:{payload.get('list_bias', 'moderate')}]"
    )


def _top_behavior_rules(payload: dict, limit: int = 3) -> list[str]:
    """Pick the most actionable behaviour rules.

    persona_evolution stores accumulated traits in the payload; favour those if
    present (they have implicit priority by recency), otherwise fall back to the
    first N rules from the base payload.
    """
    rules = payload.get("behavior_rules") or []
    if not rules:
        rules = ELIRA_PERSONA_BASE_PAYLOAD["behavior_rules"]
    return rules[:limit]


def build_persona_prompt(
    profile_name: str,
    model_name: str = "",
    task_context: str = "",
) -> str:
    """Compact persona prompt designed for small (2B-7B) local models.

    Keeps persona_evolution intact: the active snapshot from `get_persona_version()`
    is still read, so traits accumulated via `observe_dialogue` are reflected here.
    But the per-call output is short enough that small models can actually
    attend to all of it (target: < 600 chars / ~150 tokens).
    """
    snapshot = get_persona_version()
    payload = deepcopy(snapshot.get("payload") or ELIRA_PERSONA_BASE_PAYLOAD)

    profile_key = (
        profile_name if profile_name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE
    )

    calibration_record = get_model_calibration(
        model_name,
        version_id=int(snapshot.get("version", 1) or 1),
    )
    calibration_payload = calibration_record.get("calibration") or deepcopy(
        DEFAULT_MODEL_CALIBRATION
    )

    rules = _top_behavior_rules(payload, limit=3)
    rules_block = "\n".join(f"- {item}" for item in rules)

    lines = [
        "Ты — Elira, AI-ассистентка пользователя в Elira AI.",
        "Миссия: помогать честно, ясно и практически. Не выдумывать факты.",
        _short_profile_line(profile_key) + ".",
        f"Правила:\n{rules_block}",
        f"Идентичность: ты Elira, никогда не называй себя именем модели или языковой моделью.",
        _calibration_pragma(calibration_payload),
    ]

    if task_context.strip():
        lines.append(task_context.strip())

    return "\n".join(lines)


persona_store.bootstrap_if_needed()
