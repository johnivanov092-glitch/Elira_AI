from __future__ import annotations
from typing import Any
from app.services.chat_service import run_chat


def run_research_agent(model_name: str, profile_name: str, user_input: str, context_bundle: str) -> dict[str, Any]:
    prompt = (
        "Ты research agent. На основе контекста ниже выдели только полезные факты, "
        "которые реально нужны для решения задачи. Если данных мало — скажи это.\n\n"
        f"Задача:\n{user_input}\n\n"
        f"Контекст:\n{context_bundle}"
    )
    result = run_chat(
        model_name=model_name,
        profile_name=profile_name,
        user_input=prompt,
        history=[],
    )
    return {
        "ok": bool(result.get("ok")),
        "answer": result.get("answer", ""),
        "meta": result.get("meta", {}),
        "warnings": result.get("warnings", []),
    }
