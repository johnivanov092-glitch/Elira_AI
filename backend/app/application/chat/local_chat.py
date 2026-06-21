from __future__ import annotations

from typing import Any, Generator

from app.application.monitoring.inference import extract_llm_usage
from app.application.persona.service import build_persona_prompt
from app.core.persona_defaults import DEFAULT_PROFILE, PROFILE_MODE_OVERLAYS
from app.infrastructure.llm.openai_compatible import (
    chat_completion,
    chat_completion_stream,
    is_local_llm_model,
    local_llm_config,
)


def normalize_profile(name: str) -> str:
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    return name if name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE


def _message_content(resp: Any) -> str:
    message = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _ensure_local_model(model_name: str) -> None:
    if not is_local_llm_model(model_name):
        cfg = local_llm_config()
        expected = cfg.model if cfg.enabled else "local-model"
        raise RuntimeError(f"Local llama-server provider expects model '{expected}', got '{model_name}'.")


def run_chat(
    model_name: str,
    profile_name: str,
    user_input: str,
    history: list[dict] | None = None,
    num_ctx: int = 16384,
    task_context: str = "",
    timeout: float | None = None,
) -> dict[str, Any]:
    profile = normalize_profile(profile_name)
    try:
        _ensure_local_model(model_name)
        system = build_persona_prompt(profile, model_name=model_name, task_context=task_context)
        messages = [{"role": "system", "content": system}]
        for item in history or []:
            role = item.get("role", "")
            content = item.get("content", "")
            if role in ("user", "assistant") and content.strip():
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_input})

        resp = chat_completion(
            model=model_name,
            messages=messages,
            options={"num_ctx": num_ctx},
            timeout=timeout,
        )
        cfg = local_llm_config()
        return {
            "ok": True,
            "answer": _message_content(resp),
            "warnings": [],
            "meta": {
                "profile": profile,
                "provider": cfg.provider,
                "base_url": cfg.base_url,
                "usage": extract_llm_usage(resp),
            },
        }
    except Exception as exc:
        return {"ok": False, "answer": "", "warnings": [str(exc)], "meta": {}}


def run_chat_stream(
    model_name: str,
    profile_name: str,
    user_input: str,
    history: list[dict] | None = None,
    num_ctx: int = 16384,
    task_context: str = "",
    timeout: float | None = None,
) -> Generator[str, None, None]:
    try:
        _ensure_local_model(model_name)
        profile = normalize_profile(profile_name)
        system = build_persona_prompt(profile, model_name=model_name, task_context=task_context)
        messages = [{"role": "system", "content": system}]
        for item in history or []:
            role = item.get("role", "")
            content = item.get("content", "")
            if role in ("user", "assistant") and content.strip():
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_input})

        yield from chat_completion_stream(
            model=model_name,
            messages=messages,
            options={"num_ctx": num_ctx},
            timeout=timeout,
        )
    except Exception as exc:
        yield f"\n\nError: {exc}"
