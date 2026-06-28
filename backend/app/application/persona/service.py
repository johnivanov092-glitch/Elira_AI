from __future__ import annotations

from copy import deepcopy

from app.application.persona import evolution as persona_evolution
from app.application.persona import store as persona_store
from app.core.persona_defaults import (
    DEFAULT_MODEL_CALIBRATION,
    DEFAULT_PROFILE,
    ELIRA_PERSONA_BASE_PAYLOAD,
    LEGACY_PROFILE_TO_MODE,
    PERSONA_MODES,
    PROFILE_MODE_OVERLAYS,
)


def to_mode(name: str) -> str:
    """Resolve any incoming profile/mode name to a real persona mode.

    Accepts a current mode, a legacy profile name, or junk — always returns one
    of PERSONA_MODES (DEFAULT_PROFILE as the safe fallback).
    """
    if name in PERSONA_MODES:
        return name
    return LEGACY_PROFILE_TO_MODE.get(name, DEFAULT_PROFILE)


def mode_temperature(name: str):
    """Sampling temperature for a mode, or None to keep the per-role default
    (None = Инженерный/code keeps its strict reproducible sampling)."""
    return PERSONA_MODES[to_mode(name)]["temperature"]


def mode_tool_posture(name: str) -> str:
    """'readonly' (Личный narrows the offered tools) or 'full'."""
    return PERSONA_MODES[to_mode(name)]["tools"]


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


def _honesty_boundary(payload: dict) -> str:
    """The 'don't fabricate facts/links/results' boundary, if present.

    It lives in `boundaries`, which `_top_behavior_rules` never reaches, so we
    pull it out explicitly and always keep it in the compact prompt.
    """
    boundaries = payload.get("boundaries") or ELIRA_PERSONA_BASE_PAYLOAD["boundaries"]
    for item in boundaries:
        if "выдумыв" in str(item).lower():
            return str(item)
    return ""


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

    profile_key = to_mode(profile_name)

    calibration_record = get_model_calibration(
        model_name,
        version_id=int(snapshot.get("version", 1) or 1),
    )
    calibration_payload = calibration_record.get("calibration") or deepcopy(
        DEFAULT_MODEL_CALIBRATION
    )

    rules = _top_behavior_rules(payload, limit=3)
    # Always surface the honesty boundary: it lives in `boundaries` (not
    # behavior_rules), so the limit=3 cut above would otherwise drop it. For an
    # agent that acts on the real filesystem this is the most load-bearing rule.
    honesty = _honesty_boundary(payload)
    if honesty:
        rules = [*rules, honesty]
    rules_block = "\n".join(f"- {item}" for item in rules)

    lines = [
        "Ты — Elira, AI-ассистентка пользователя в Elira AI.",
        "Миссия: помогать честно, ясно и практически. Не выдумывать факты и не выдавать намерение за результат.",
        _short_profile_line(profile_key) + ".",
    ]

    # Step B: mood — a single voice-coloring line (transient, decays). Fail-safe:
    # never let mood reading break prompt building.
    try:
        from app.application.persona.mood import mood_overlay_line

        lines.append(mood_overlay_line())
    except Exception:
        pass

    lines += [
        f"Правила:\n{rules_block}",
        f"Идентичность: ты Elira, никогда не называй себя именем модели или языковой моделью.",
        _calibration_pragma(calibration_payload),
    ]

    if task_context.strip():
        lines.append(task_context.strip())

    return "\n".join(lines)


persona_store.bootstrap_if_needed()
