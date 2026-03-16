from __future__ import annotations
from typing import Any
from app.services.chat_service import run_chat


def run_coder_agent(model_name: str, profile_name: str, user_input: str, plan_text: str, research_text: str) -> dict[str, Any]:
    prompt = (
        "Ты coder/solution agent. "
        "На основе плана и исследовательского контекста собери рабочий черновик решения. "
        "Если задача про код — опиши конкретные изменения по файлам. "
        "Если задача про анализ — дай структурированный черновик ответа.\n\n"
        f"Задача:\n{user_input}\n\n"
        f"План:\n{plan_text}\n\n"
        f"Research:\n{research_text}"
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
