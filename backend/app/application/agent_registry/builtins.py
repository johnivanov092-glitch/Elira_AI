from __future__ import annotations

from typing import Any


_ROLE_DEFS = [
    ("РЅРёРІРµСЂСЃР°Р»СЊРЅ", "general", "Universal", "builtin-universal"),
    ("СЃСЃР»РµРґРѕРІР°С‚РµР»", "researcher", "Researcher", "builtin-researcher"),
    ("СЂРѕРіСЂР°РјРјРёСЃС‚", "programmer", "Programmer", "builtin-programmer"),
    ("РЅР°Р»РёС‚РёРє", "analyst", "Analyst", "builtin-analyst"),
    ("РѕРєСЂР°С‚", "teacher", "Socrat", "builtin-socrat"),
]


def _match_role(name_ru: str) -> tuple[str, str, str]:
    lower = name_ru.lower()
    for substr, role, name_en, agent_id in _ROLE_DEFS:
        if substr in lower:
            return role, name_en, agent_id
    return "custom", name_ru, f"builtin-{name_ru.lower()[:20]}"


def iter_builtin_agent_defs() -> list[dict[str, Any]]:
    from app.core.config import AGENT_PROFILES, AGENT_PROFILE_UI

    builtins: list[dict[str, Any]] = []
    for name_ru, prompt in AGENT_PROFILES.items():
        role, name_en, agent_id = _match_role(name_ru)
        ui = AGENT_PROFILE_UI.get(name_ru, {})
        builtins.append(
            {
                "id": agent_id,
                "name": name_en,
                "name_ru": name_ru,
                "description": ui.get("short", ""),
                "description_ru": ui.get("short", ""),
                "role": role,
                "system_prompt": prompt,
                "tags": ui.get("tags", []),
                "config": {"icon": ui.get("icon", "")},
            }
        )

    builtins.extend(
        [
            {
                "id": "builtin-orchestrator",
                "name": "Orchestrator",
                "name_ru": "РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
                "description": "Plans and synthesizes multi-step workflows.",
                "description_ru": "РџР»Р°РЅРёСЂСѓРµС‚ Рё СЃРѕР±РёСЂР°РµС‚ РёС‚РѕРі РјРЅРѕРіРѕС€Р°РіРѕРІС‹С… workflow.",
                "role": "orchestrator",
                "system_prompt": (
                    "РўС‹ РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ. Р Р°Р·Р±РёРІР°Р№ СЃР»РѕР¶РЅС‹Рµ Р·Р°РґР°С‡Рё РЅР° С€Р°РіРё, СЃРѕР±РёСЂР°Р№ РёС‚РѕРіРѕРІС‹Рµ РІС‹РІРѕРґС‹, "
                    "РґРµСЂР¶Рё СЃС‚СЂСѓРєС‚СѓСЂСѓ РѕС‚РІРµС‚Р° Рё РїРѕРјРѕРіР°Р№ Р°РіРµРЅС‚Р°Рј СЂР°Р±РѕС‚Р°С‚СЊ СЃРѕРіР»Р°СЃРѕРІР°РЅРЅРѕ."
                ),
                "tags": ["workflow", "planning", "coordination"],
                "config": {"icon": "в—Ћ"},
            },
            {
                "id": "builtin-reviewer",
                "name": "Reviewer",
                "name_ru": "Р РµРІСЊСЋРµСЂ",
                "description": "Critiques intermediate and final results.",
                "description_ru": "РџСЂРѕРІРµСЂСЏРµС‚ РїСЂРѕРјРµР¶СѓС‚РѕС‡РЅС‹Рµ Рё С„РёРЅР°Р»СЊРЅС‹Рµ СЂРµР·СѓР»СЊС‚Р°С‚С‹.",
                "role": "reviewer",
                "system_prompt": (
                    "РўС‹ Р РµРІСЊСЋРµСЂ. РџСЂРѕРІРµСЂСЏР№ РѕС‚РІРµС‚С‹ РЅР° Р»РѕРіРёС‡РµСЃРєРёРµ РїСЂРѕР±РµР»С‹, СЃР»Р°Р±С‹Рµ РјРµСЃС‚Р°, СЂРёСЃРєРё Рё "
                    "РЅРµРґРѕСЃС‚Р°СЋС‰РёРµ СѓР»СѓС‡С€РµРЅРёСЏ. РџРёС€Рё РєРѕРЅРєСЂРµС‚РЅРѕ Рё РїРѕР»РµР·РЅРѕ."
                ),
                "tags": ["review", "quality", "critique"],
                "config": {"icon": "в—Њ"},
            },
        ]
    )

    return builtins
