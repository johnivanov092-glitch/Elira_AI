from __future__ import annotations

from typing import Any


_ROLE_DEFS = [
    ("ниверсальн", "general", "Universal", "builtin-universal"),
    ("сследовател", "researcher", "Researcher", "builtin-researcher"),
    ("рограммист", "programmer", "Programmer", "builtin-programmer"),
    ("налитик", "analyst", "Analyst", "builtin-analyst"),
    ("ократ", "teacher", "Socrat", "builtin-socrat"),
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
                "name_ru": "Оркестратор",
                "description": "Plans and synthesizes multi-step workflows.",
                "description_ru": "Планирует и собирает итог многошаговых workflow.",
                "role": "orchestrator",
                "system_prompt": (
                    "Ты Оркестратор. Разбивай сложные задачи на шаги, собирай итоговые выводы, "
                    "держи структуру ответа и помогай агентам работать согласованно."
                ),
                "tags": ["workflow", "planning", "coordination"],
                "config": {"icon": "◎"},
            },
            {
                "id": "builtin-reviewer",
                "name": "Reviewer",
                "name_ru": "Ревьюер",
                "description": "Critiques intermediate and final results.",
                "description_ru": "Проверяет промежуточные и финальные результаты.",
                "role": "reviewer",
                "system_prompt": (
                    "Ты Ревьюер. Проверяй ответы на логические пробелы, слабые места, риски и "
                    "недостающие улучшения. Пиши конкретно и полезно."
                ),
                "tags": ["review", "quality", "critique"],
                "config": {"icon": "◌"},
            },
        ]
    )

    return builtins
