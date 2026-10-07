from __future__ import annotations

from app.core.persona_defaults import DEFAULT_PROFILE, LEGACY_PROFILE_TO_MODE, PERSONA_MODES


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


def resolve_persona_mode(
    requested: str | None,
    user_input: str | None,
    conversation_history: list[dict[str, object]] | None = None,
) -> str:
    """One live persona; legacy names remain valid wire and journal data.

    Task evidence routing is separate and never selects personality/sampling.
    """
    return DEFAULT_PROFILE
