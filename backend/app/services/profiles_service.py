from __future__ import annotations

from typing import Any

from app.core.config import AGENT_PROFILES, DEFAULT_PROFILE


def get_profiles() -> dict[str, Any]:
    profiles = []
    for name, system_prompt in AGENT_PROFILES.items():
        profiles.append(
            {
                "name": name,
                "is_default": name == DEFAULT_PROFILE,
                "system_prompt_preview": system_prompt[:180],
            }
        )

    return {
        "ok": True,
        "default_profile": DEFAULT_PROFILE,
        "profiles": profiles,
        "count": len(profiles),
    }
