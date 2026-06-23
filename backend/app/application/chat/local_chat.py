from __future__ import annotations

import threading
from typing import Any, Generator

from app.application.monitoring.inference import extract_llm_usage
from app.application.persona.service import build_persona_prompt
from app.core.persona_defaults import DEFAULT_PROFILE, PROFILE_MODE_OVERLAYS
from app.infrastructure.llm.openai_compatible import (
    chat_completion,
    chat_completion_event_stream,
    is_local_llm_model,
    local_llm_config,
)


def normalize_profile(name: str) -> str:
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    return name if name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE


def resolve_profile_name(name: str | None) -> str:
    """Pick the effective persona profile for a request.

    The frontend sends "default" (or nothing) when the user hasn't picked a
    per-message override, so an empty/"default" value means "use whatever the
    user saved in Settings". Resolve that to the stored `agent_profile` before
    normalizing; an explicit name is honored as-is. Shared by the chat routes
    and the autopipeline runner so background runs honor the saved profile too.
    """
    if name and name.lower() != "default":
        return normalize_profile(name)
    # Lazy import: settings pulls in storage and would create an import cycle
    # if loaded at module top alongside the persona machinery.
    from app.application.elira_memory.settings import get_settings

    stored = ""
    try:
        stored = str(get_settings().get("agent_profile") or "")
    except Exception:
        stored = ""
    return normalize_profile(stored)


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
    usage_sink: dict[str, Any] | None = None,
    cancel_event: "threading.Event | None" = None,
) -> Generator[str, None, None]:
    """Stream visible tokens from the local model.

    Yields plain token strings so existing planner/route consumers stay
    unchanged. Two optional side-channels:

    * ``usage_sink`` — when given, it is filled in-place with the final
      ``extract_llm_usage`` dict (token counts + timing) so callers can
      surface tokens/sec without re-parsing the response.
    * ``cancel_event`` — when set mid-stream the generator stops pulling
      tokens and closes the upstream request, so a Stop button actually
      frees the server instead of leaving it generating in the background.
    """
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

        events = chat_completion_event_stream(
            model=model_name,
            messages=messages,
            options={"num_ctx": num_ctx},
            timeout=timeout,
        )
        try:
            for event in events:
                if cancel_event is not None and cancel_event.is_set():
                    break
                etype = event.get("type")
                if etype == "delta":
                    token = str(event.get("content") or "")
                    if token:
                        yield token
                elif etype == "message" and usage_sink is not None:
                    # Closing the generator (below) skips its own message
                    # event on cancel, so capture usage here on clean finish.
                    usage_sink.update(extract_llm_usage(event.get("response") or {}))
        finally:
            # Closing mid-iteration triggers the event stream's `finally`,
            # which calls response.close() and frees the upstream connection.
            events.close()
    except Exception as exc:
        yield f"\n\nError: {exc}"
