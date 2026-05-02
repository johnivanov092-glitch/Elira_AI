# -*- coding: utf-8 -*-
"""Application-layer runtime for Ollama chat.

Thin wrapper over the Ollama client: builds the system prompt from the
active persona, assembles the message history, and returns a plain dict.
No HTTP at module level — the Ollama client is instantiated inside the
call so tests can mock it without patching module-level state.
"""
from __future__ import annotations

from typing import Any, Generator

from app.core.persona_defaults import DEFAULT_PROFILE, PROFILE_MODE_OVERLAYS


# ── helpers ───────────────────────────────────────────────────────────────────

def normalize_profile(name: str) -> str:
    """Return a valid profile name, falling back to DEFAULT_PROFILE."""
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    return name if name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE


# ── public API ────────────────────────────────────────────────────────────────

def run_chat(
    model_name: str,
    profile_name: str,
    user_input: str,
    history: list[dict] | None = None,
    num_ctx: int = 8192,
    task_context: str = "",
) -> dict[str, Any]:
    """Run a single Ollama chat turn and return the result dict.

    Returns:
      ok: bool
      answer: str
      warnings: list[str]
      meta: dict  — includes ``profile`` key
    """
    import ollama
    from app.application.persona_service.runtime import build_persona_prompt

    profile = normalize_profile(profile_name)
    system = build_persona_prompt(profile, model_name=model_name, task_context=task_context)

    messages: list[dict] = [{"role": "system", "content": system}]
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
    task_context: str = "",
) -> Generator[str, None, None]:
    """Token-streaming generator over Ollama chat.

    Each ``yield`` is one token string.  Falls back to a single-turn
    ``run_chat`` call if the streaming client raises.
    """
    import ollama
    from app.application.persona_service.runtime import build_persona_prompt

    profile = normalize_profile(profile_name)
    system = build_persona_prompt(profile, model_name=model_name, task_context=task_context)

    messages: list[dict] = [{"role": "system", "content": system}]
    for m in (history or []):
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("user", "assistant") and content.strip():
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_input})

    try:
        client = ollama.Client()
        stream = client.chat(
            model=model_name, messages=messages, stream=True, options={"num_ctx": num_ctx}
        )
        for chunk in stream:
            token = chunk.message.content or ""
            if token:
                yield token
    except Exception as e:
        result = run_chat(
            model_name, profile_name, user_input, history,
            num_ctx=num_ctx, task_context=task_context,
        )
        if result.get("ok") and result.get("answer"):
            yield result["answer"]
        else:
            yield f"\n\n⚠️ Ошибка: {e}"
