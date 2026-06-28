from __future__ import annotations

from typing import Any

from app.core.persona_defaults import AUTO_PROFILE, PROFILE_MODE_OVERLAYS, PROFILE_UI
from app.application.persona.service import build_persona_prompt


def get_profiles() -> dict[str, Any]:
    # "Авто" is the default selection: not a mode itself, Elira picks the mode
    # per message (Личный/Баланс/Инженерный). Picking a concrete mode locks it.
    profiles = [
        {
            "name": AUTO_PROFILE,
            "is_default": True,
            "icon": "✶",
            "tags": ["авто", "гибрид"],
            "short": "Elira сама выбирает режим под сообщение.",
            "mode_overlay_preview": "Авто: режим выбирается по содержанию запроса; можно зафиксировать вручную.",
            "system_prompt_preview": "",
        }
    ]
    for name, overlay in PROFILE_MODE_OVERLAYS.items():
        meta = PROFILE_UI.get(name, {})
        profiles.append(
            {
                "name": name,
                "is_default": False,
                "icon": meta.get("icon", ""),
                "tags": meta.get("tags", []),
                "short": meta.get("short", ""),
                "mode_overlay_preview": overlay[:180],
                "system_prompt_preview": build_persona_prompt(name)[:180],
            }
        )

    return {
        "ok": True,
        "default_profile": AUTO_PROFILE,
        "profiles": profiles,
        "count": len(profiles),
    }
