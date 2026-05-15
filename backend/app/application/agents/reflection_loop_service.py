"""
reflection_loop_service.py — рефлексия: улучшение черновика через второй проход LLM.

Вызывается только для маршрутов code/project/research (экономия на обычном чате).
"""
from __future__ import annotations

from typing import Any

from app.services.chat_service import run_chat


def run_reflection_loop(
    model_name: str,
    profile_name: str,
    user_input: str,
    draft_text: str,
    review_text: str,
    context: str | None = None,
) -> dict[str, Any]:
    """
    Reflection stage:
    - Объединяет черновик + замечания reviewer
    - Использует опциональный контекст
    - Возвращает улучшённый ответ
    """
    prompt_parts = [
        "Ты reflection agent Elira.",
        "Твоя задача: улучшить черновик с учётом замечаний reviewer и собрать финальный ответ.",
        "Пиши по существу, без воды.",
        "Если задача про код — укажи конкретные файлы, изменения и следующий шаг.",
        "Форматируй ответ с помощью Markdown.",
        "",
        f"Запрос пользователя:\n{user_input}",
        "",
        f"Черновик:\n{draft_text}",
        "",
        f"Reviewer notes:\n{review_text}",
    ]

    if context:
        prompt_parts.extend(["", f"Дополнительный контекст:\n{context}"])

    result = run_chat(
        model_name=model_name,
        profile_name=profile_name,
        user_input="\n".join(prompt_parts),
        history=[],
    )

    answer = result.get("answer", "")
    return {
        "ok": bool(result.get("ok")),
        "answer": answer,
        "meta": {
            **result.get("meta", {}),
            "stage": "reflection_loop",
            "used_context": bool(context),
        },
        "warnings": result.get("warnings", []),
    }
