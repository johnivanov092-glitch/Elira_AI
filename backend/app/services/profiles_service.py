from __future__ import annotations

from typing import Any

from app.core.persona_defaults import DEFAULT_PROFILE, PROFILE_MODE_OVERLAYS, PROFILE_UI
from app.services.persona_service import build_persona_prompt


def get_profiles() -> dict[str, Any]:
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
