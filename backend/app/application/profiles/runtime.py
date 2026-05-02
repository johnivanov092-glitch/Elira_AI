# -*- coding: utf-8 -*-
"""Application-layer runtime for persona profile listing.

Builds the profile index from ``core/persona_defaults`` constants and the
active persona prompt builder.  Pure Python — no HTTP, no DB.
"""
from __future__ import annotations

from typing import Any

from app.core.persona_defaults import DEFAULT_PROFILE, PROFILE_MODE_OVERLAYS, PROFILE_UI


# ── public API ────────────────────────────────────────────────────────────────

def get_profiles() -> dict[str, Any]:
    """Return the list of all defined persona profiles.

    Returns:
      ok: bool
      default_profile: str
      profiles: list[dict]  — name, is_default, icon, tags, short,
                               mode_overlay_preview, system_prompt_preview
      count: int
    """
    from app.application.persona_service.runtime import build_persona_prompt

    profiles = []
    for name, overlay in PROFILE_MODE_OVERLAYS.items():
        meta = PROFILE_UI.get(name, {})
        profiles.append(
            {
                "name": name,
                "is_default": name == DEFAULT_PROFILE,
                "icon": meta.get("icon", ""),
                "tags": meta.get("tags", []),
                "short": meta.get("short", ""),
                "mode_overlay_preview": overlay[:180],
                "system_prompt_preview": build_persona_prompt(name)[:180],
            }
        )

    return {
        "ok": True,
        "default_profile": DEFAULT_PROFILE,
        "profiles": profiles,
        "count": len(profiles),
    }
