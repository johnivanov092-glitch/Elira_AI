from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field

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


logger = logging.getLogger(__name__)

PERSONA_PROMPT_CHAR_BUDGET = 1300
_PROMOTED_TRAIT_CHAR_BUDGET = 96
_PROMOTED_TRAIT_LAYERS = (
    "behavior_rules",
    "preferences",
    "voice",
    "values",
    "tool_style",
)


def _bounded_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return text[: limit - 1].rstrip() + "…"


@dataclass
class PersonaPostTurnObservation:
    """Trusted user-chat observation collected from the public SSE stream."""

    dialog_id: str
    session_id: str
    profile_name: str
    model_name: str
    user_input: str
    answer_text: str = ""
    completed: bool = False
    _persisted: bool = field(default=False, init=False, repr=False)

    def record_event(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "final_response":
            self.answer_text = str(event.get("text") or "").strip()
        elif event_type == "done":
            self.completed = bool(event.get("ok")) and (
                str(event.get("stop_reason") or "") == "answer"
            )

    def persist(self) -> None:
        if self._persisted or not self.completed or not self.answer_text:
            return
        self._persisted = True
        try:
            observe_dialogue(
                dialog_id=self.dialog_id,
                session_id=self.session_id,
                profile_name=self.profile_name,
                model_name=self.model_name,
                user_input=self.user_input,
                answer_text=self.answer_text,
                route=self.profile_name,
                outcome_ok=True,
            )
        except Exception:
            logger.warning(
                "persona observation failed for user dialogue %s",
                self.dialog_id,
                exc_info=True,
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
    """'readonly' (narrows the offered tools to read-only) or 'full'. All modes
    are 'full' by default now; 'readonly' stays available as a posture but no
    built-in mode ships with it (personal mode was un-narrowed on purpose)."""
    return PERSONA_MODES[to_mode(name)]["tools"]


get_persona_version = persona_store.get_persona_version
get_model_calibration = persona_store.get_model_calibration
list_persona_candidates = persona_store.list_persona_candidates
get_persona_status = persona_store.get_persona_status
init_persona_store = persona_store.init_persona_store
observe_dialogue = persona_evolution.observe_dialogue
rollback_persona = persona_evolution.rollback_persona


def _short_profile_line(profile_key: str) -> str:
    """Packed first-sentence instruction for the selected persona mode."""
    overlay = PROFILE_MODE_OVERLAYS.get(profile_key, "")
    # Mode overlays deliberately keep the full load-bearing instruction before
    # the first period; any explanatory tail stays out of the live prompt.
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


def _promoted_traits(payload: dict) -> list[str]:
    """Return at most one bounded promoted trait from every supported layer.

    Persona evolution appends accepted traits to their semantic layer. Compare
    each layer with the immutable base payload so promoted ``preferences``,
    ``voice`` and ``values`` cannot be starved by earlier-layer promotions.
    """
    promoted: list[str] = []
    for layer in _PROMOTED_TRAIT_LAYERS:
        base_items = {
            str(item).strip()
            for item in ELIRA_PERSONA_BASE_PAYLOAD.get(layer, [])
            if str(item).strip()
        }
        current_items = payload.get(layer) or []
        if not isinstance(current_items, list):
            continue
        for item in reversed(current_items):
            text = str(item).strip()
            if text and text not in base_items:
                bounded = _bounded_text(text, _PROMOTED_TRAIT_CHAR_BUDGET)
                if bounded and bounded not in promoted:
                    promoted.append(bounded)
                break
    return promoted


def _core_rules(limit: int = 3) -> list[str]:
    core_rules = [
        str(item).strip()
        for item in ELIRA_PERSONA_BASE_PAYLOAD["behavior_rules"]
        if str(item).strip()
    ]
    return core_rules[:limit]


def _fit_persona_prompt(body_lines: list[str], tail_lines: list[str]) -> str:
    """Keep identity/calibration and fit lower-priority prose into the budget."""
    tail = "\n".join(line for line in tail_lines if line)
    body = "\n".join(line for line in body_lines if line)
    separator = "\n" if body and tail else ""
    available = PERSONA_PROMPT_CHAR_BUDGET - len(tail) - len(separator)
    if available <= 0:
        return _bounded_text(tail, PERSONA_PROMPT_CHAR_BUDGET)
    return _bounded_text(body, available) + separator + tail


def build_persona_prompt(
    profile_name: str,
    model_name: str = "",
    task_context: str = "",
) -> str:
    """Bounded persona prompt for the current local model.

    Keeps persona_evolution intact: the active snapshot from `get_persona_version()`
    is still read, so traits accumulated via `observe_dialogue` are reflected here.
    The per-call output stays below the tested 1300-character budget.
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

    rules = _core_rules(limit=3)
    # Always surface the honesty boundary: it lives in `boundaries` (not
    # behavior_rules), so the limit=3 cut above would otherwise drop it. For an
    # agent that acts on the real filesystem this is the most load-bearing rule.
    honesty = _honesty_boundary(payload)
    if honesty:
        rules = [*rules, honesty]
    rules_block = "\n".join(f"- {item}" for item in rules)

    body_lines = [
        "Ты — Elira, AI-ассистентка пользователя в Elira AI.",
        "Миссия: помогать честно, ясно и практически. Не выдумывать факты и не выдавать намерение за результат.",
        _short_profile_line(profile_key) + ".",
    ]

    # Step B: mood — a single voice-coloring line (transient, decays). Fail-safe:
    # never let mood reading break prompt building.
    try:
        from app.application.persona.mood import mood_overlay_line

        body_lines.append(mood_overlay_line())
    except Exception:
        pass

    body_lines.append(
        f"Правила:\n{rules_block}",
    )

    if task_context.strip():
        body_lines.append(task_context.strip())

    promoted = _promoted_traits(payload)
    tail_lines = []
    if promoted:
        tail_lines.append(
            "Развившиеся черты:\n" + "\n".join(f"- {item}" for item in promoted)
        )
    tail_lines.extend(
        [
            "Идентичность: ты Elira, никогда не называй себя именем модели или языковой моделью.",
            _calibration_pragma(calibration_payload),
        ]
    )
    return _fit_persona_prompt(body_lines, tail_lines)


persona_store.bootstrap_if_needed()
