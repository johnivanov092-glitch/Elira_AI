from __future__ import annotations
from typing import Any
from app.services.chat_service import run_chat


def run_reviewer_agent(model_name: str, profile_name: str, user_input: str, draft_text: str) -> dict[str, Any]:
    prompt = (
        "Ты reviewer agent. Проверь черновик решения. "
        "Найди слабые места, неточности, пропуски, риски. "
        "Ответ дай коротко: 1) что хорошо 2) что надо улучшить 3) что критично.\n\n"
        f"Задача:\n{user_input}\n\n"
        f"Черновик:\n{draft_text}"
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
