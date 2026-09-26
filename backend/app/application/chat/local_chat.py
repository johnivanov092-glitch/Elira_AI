from __future__ import annotations

from app.core.persona_defaults import (
    AUTO_PROFILE,
    DEFAULT_PROFILE,
    LEGACY_PROFILE_TO_MODE,
    PERSONA_MODES,
)


def normalize_profile(name: str) -> str:
    """Resolve a stored/requested name to a real persona mode.

    Accepts a current mode, a legacy profile name ("Программист" -> "Инженерный"),
    or "default"/empty -> DEFAULT_PROFILE. "Авто" is NOT resolved here (it needs
    the message text — see resolve_persona_mode)."""
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    if name in PERSONA_MODES:
        return name
    return LEGACY_PROFILE_TO_MODE.get(name, DEFAULT_PROFILE)


def resolve_profile_name(name: str | None) -> str:
    """Pick the effective persona mode for a request WITHOUT message context.

    Used by background paths (autopipeline) that have no live user message to
    classify. An explicit mode is honored; "default"/empty falls back to the
    saved `agent_profile`; "Авто" (no text to classify here) resolves to the
    neutral DEFAULT_PROFILE so background runs stay deterministic.
    """
    if name and name.lower() != "default":
        if name == AUTO_PROFILE:
            return DEFAULT_PROFILE
        return normalize_profile(name)
    # Lazy import: settings pulls in storage and would create an import cycle
    # if loaded at module top alongside the persona machinery.
    from app.application.elira_memory.settings import get_settings

    stored = ""
    try:
        stored = str(get_settings().get("agent_profile") or "")
    except Exception:
        stored = ""
    if stored == AUTO_PROFILE:
        return DEFAULT_PROFILE
    return normalize_profile(stored)


def resolve_persona_mode(
    requested: str | None,
    user_input: str | None,
    conversation_history: list[dict[str, object]] | None = None,
) -> str:
    """One live persona; legacy names remain valid wire and journal data.

    Task evidence routing is separate and never selects personality/sampling.
    """
    return DEFAULT_PROFILE
