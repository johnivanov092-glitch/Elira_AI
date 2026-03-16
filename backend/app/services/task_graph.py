from __future__ import annotations
from typing import Any


def make_task_graph(user_input: str) -> dict[str, Any]:
    text = (user_input or "").strip()
    nodes = [
        {
            "id": "plan",
            "title": "Planner",
            "description": "Понять задачу и разложить её на шаги",
            "depends_on": [],
        },
        {
            "id": "research",
            "title": "Research",
            "description": "Собрать контекст из memory / library / project / web / python tools",
            "depends_on": ["plan"],
        },
        {
            "id": "draft",
            "title": "Coder/Drafter",
            "description": "Собрать рабочий черновик ответа или решения",
            "depends_on": ["research"],
        },
        {
            "id": "review",
            "title": "Reviewer",
            "description": "Проверить полноту, ошибки и слабые места",
            "depends_on": ["draft"],
        },
        {
            "id": "final",
            "title": "Final synthesis",
            "description": "Собрать финальный ответ для пользователя",
            "depends_on": ["review"],
        },
    ]

    return {
        "ok": True,
        "task": text,
        "nodes": nodes,
        "count": len(nodes),
    }
