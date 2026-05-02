# -*- coding: utf-8 -*-
"""Application-layer runtime for the LLM reflection loop.

Improves a draft answer by running a second Ollama pass that incorporates
reviewer notes.  Only invoked for code/project/research routes to save
tokens on plain chat.  No HTTP/DB at module level — the Ollama call is
delegated via ``app.application.chat_service.runtime.run_chat``.
"""
from __future__ import annotations

from typing import Any


# ── public API ────────────────────────────────────────────────────────────────

def run_reflection_loop(
    model_name: str,
    profile_name: str,
    user_input: str,
    draft_text: str,
    review_text: str,
    context: str | None = None,
) -> dict[str, Any]:
    """Run the reflection stage: improve *draft_text* using *review_text*.

    Returns:
      ok: bool
      answer: str        — improved response
      meta: dict         — includes ``stage`` and ``used_context`` keys
      warnings: list[str]
    """
    from app.application.chat_service.runtime import run_chat

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
