"""
chat_service.py — обёртка над Ollama для чата.

Изменения:
  • run_chat_stream() — генератор токенов для стриминга
  • run_chat() — без изменений (обратная совместимость)
"""
from typing import Any, Generator

import ollama
from app.core.config import AGENT_PROFILES, DEFAULT_PROFILE


def normalize_profile(name: str):
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    return name if name in AGENT_PROFILES else DEFAULT_PROFILE


def run_chat(model_name: str, profile_name: str, user_input: str, history: list[dict] | None = None, num_ctx: int = 8192) -> dict[str, Any]:
    profile = normalize_profile(profile_name)
    system = AGENT_PROFILES.get(profile, AGENT_PROFILES[DEFAULT_PROFILE])

    messages = [{"role": "system", "content": system}]

    # Добавляем историю диалога
    for m in (history or []):
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and content.strip():
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_input})

    try:
        client = ollama.Client()
        resp = client.chat(model=model_name, messages=messages, options={"num_ctx": num_ctx})
        text = resp.message.content or ""
        return {"ok": True, "answer": text, "warnings": [], "meta": {"profile": profile}}
    except Exception as e:
        return {"ok": False, "answer": "", "warnings": [str(e)], "meta": {}}


def run_chat_stream(
    model_name: str,
    profile_name: str,
    user_input: str,
    history: list[dict] | None = None,
    num_ctx: int = 8192,
) -> Generator[str, None, None]:
    """Генератор токенов для стриминга. Каждый yield — один токен."""
    profile = normalize_profile(profile_name)
    system = AGENT_PROFILES.get(profile, AGENT_PROFILES[DEFAULT_PROFILE])

    messages = [{"role": "system", "content": system}]

    for m in (history or []):
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and content.strip():
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_input})

    try:
        client = ollama.Client()
        stream = client.chat(model=model_name, messages=messages, stream=True, options={"num_ctx": num_ctx})
        for chunk in stream:
            token = chunk.message.content or ""
            if token:
                yield token
    except Exception as e:
        # При ошибке стриминга — fallback на обычный вызов
        result = run_chat(model_name, profile_name, user_input, history, num_ctx=num_ctx)
        if result.get("ok") and result.get("answer"):
            yield result["answer"]
        else:
            yield f"\n\n⚠️ Ошибка: {e}"
