from __future__ import annotations

from app.core.persona_defaults import DEFAULT_PROFILE, PROFILE_MODE_OVERLAYS


def normalize_profile(name: str) -> str:
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    return name if name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE


def resolve_profile_name(name: str | None) -> str:
    """Pick the effective persona profile for a request.

    The frontend sends "default" (or nothing) when the user hasn't picked a
    per-message override, so an empty/"default" value means "use whatever the
    user saved in Settings". Resolve that to the stored `agent_profile` before
    normalizing; an explicit name is honored as-is. Shared by the chat routes
    and the autopipeline runner so background runs honor the saved profile too.
    """
    if name and name.lower() != "default":
        return normalize_profile(name)
    # Lazy import: settings pulls in storage and would create an import cycle
    # if loaded at module top alongside the persona machinery.
    from app.application.elira_memory.settings import get_settings

    stored = ""
    try:
        stored = str(get_settings().get("agent_profile") or "")
    except Exception:
        stored = ""
    return normalize_profile(stored)
