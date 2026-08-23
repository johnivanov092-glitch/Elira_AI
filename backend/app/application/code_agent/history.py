"""Conversation-history helpers and rolling summarization for the code-agent.

Extracted from ``agent_loop.py`` to shrink that module and give the
summarization path a home that does NOT import ``agent_loop`` — breaking
the import cycle that previously blocked this split (``agent_loop`` uses
``summarize_history`` as ``summarize_fn=...``, while the function needs
``_coerce_history`` / ``_local_chat`` / ``_resolve_code_route`` and the
``DEFAULT_MODEL`` constant).

Every dependency below resolves to stdlib or the LLM-infra / config layers,
so this module is a leaf relative to ``agent_loop``. ``agent_loop`` re-imports
these names for backward compatibility, and external importers
(``code_agent_routes``, tests) keep importing ``summarize_history`` unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.infrastructure.llm.openai_compatible import (
    chat_completion,
    is_local_llm_model,
    local_llm_config,
)

logger = logging.getLogger(__name__)

# The frontend tags compressed summary turns with this prefix. They remain
# assistant-shaped runtime context because strict Qwen templates allow a system
# message only at index 0.
_SUMMARY_PREFIX = "[CONTEXT SUMMARY]"
# Grounded facts the discovery tools established in a prior turn (see
# loop_helpers.FACTS_PREFIX). The frontend carries them as an assistant message;
# we frame them as authoritative runtime context so factual follow-ups remain
# grounded without creating a late system message.
_FACTS_PREFIX = "[ПРОВЕРЕННЫЕ ФАКТЫ]"
# Verbatim raw output of the previous turn's grounding tools (see
# loop_helpers.RECENT_TOOLS_PREFIX) — framed like the facts block.
_RECENT_PREFIX = "[РЕЗУЛЬТАТЫ ИНСТРУМЕНТОВ ПРОШЛОГО ХОДА]"
_RUNTIME_CONTEXT_PREFIX = "[RUNTIME CONTEXT:"
_SUMMARY_CONTEXT_PREFIX = f"{_RUNTIME_CONTEXT_PREFIX} PRIOR SUMMARY]"
_FACTS_CONTEXT_PREFIX = f"{_RUNTIME_CONTEXT_PREFIX} VERIFIED FACTS]"
_RECENT_CONTEXT_PREFIX = f"{_RUNTIME_CONTEXT_PREFIX} RECENT TOOL OUTPUT]"

DEFAULT_MODEL = "local-model"
# Deprecated compatibility constant. Live runs do not use it; ``None`` means
# Auto and resolves from llama.cpp /props.
DEFAULT_NUM_CTX = 131_072


def _local_chat(**kwargs: Any) -> dict[str, Any]:
    """Wrapper so tests can monkeypatch one symbol."""
    model = str(kwargs.get("model") or "")
    if is_local_llm_model(model):
        return chat_completion(
            model=model,
            messages=list(kwargs.get("messages") or []),
            tools=kwargs.get("tools"),
            options=kwargs.get("options"),
        )
    expected = local_llm_config().model
    raise RuntimeError(f"Local llama-server provider expects model '{expected}', got '{model}'.")


def _coerce_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate / normalize prior conversation messages from the client.
    Only role + content fields are kept; tool_calls and tool results from
    past turns are dropped (we feed the agent fresh tools each turn).

    Runtime summary/facts/tool-output blocks stay assistant-shaped and receive
    explicit framing. Strict Qwen templates reject any system message after
    index 0, so history normalization must never create one.
    """
    if not history:
        return []
    out: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content:
            continue
        if role == "assistant" and content.startswith(_SUMMARY_PREFIX):
            stripped = content[len(_SUMMARY_PREFIX):].lstrip("\n").lstrip()
            if not stripped:
                continue
            out.append({
                "role": "assistant",
                "content": (
                    f"{_SUMMARY_CONTEXT_PREFIX}\n"
                    "Earlier conversation summary (compressed from prior turns by the "
                    "user — treat as context, not as your own previous answer):\n"
                    + stripped
                ),
            })
            continue
        if role == "assistant" and content.startswith(_FACTS_PREFIX):
            stripped = content[len(_FACTS_PREFIX):].lstrip("\n").lstrip()
            if not stripped:
                continue
            out.append({
                "role": "assistant",
                "content": (
                    f"{_FACTS_CONTEXT_PREFIX}\n"
                    "Проверенные факты, установленные инструментами в предыдущих ходах "
                    "(это ДОСТОВЕРНАЯ запись того, что реально проверено — опирайся на "
                    "неё для фактических вопросов о проекте/файлах/коде; если нужного "
                    "факта здесь нет — перепроверь инструментом, не выдумывай):\n"
                    + stripped
                ),
            })
            continue
        if role == "assistant" and content.startswith(_RECENT_PREFIX):
            stripped = content[len(_RECENT_PREFIX):].lstrip("\n").lstrip()
            if not stripped:
                continue
            out.append({
                "role": "assistant",
                "content": (
                    f"{_RECENT_CONTEXT_PREFIX}\n"
                    "Полный вывод инструментов из ПРЕДЫДУЩЕГО хода (достоверное сырьё — "
                    "опирайся на него дословно для фактических вопросов; чётко отделяй "
                    "то, что тут реально написано, от своих домыслов):\n" + stripped
                ),
            })
            continue
        out.append({"role": role, "content": content})
    return out


def _resolve_code_route(
    model: str,
    num_ctx: int | None,
    *,
    agent_id: str = "code-agent",
) -> tuple[str, int, Any]:
    """P9.3: route code-agent through the shared model order (route='code'):
    explicit model -> enabled 'code' profile (if installed) -> route_model_map
    -> DEFAULT_MODEL.

    Context sizing is resolved separately from the live llama.cpp ``/props``.
    The middle return value remains only for injected/offline compatibility.
    """
    from app.core.config import resolve_model_for_route

    available_models = None
    try:
        from app.infrastructure.llm.local_models import get_models

        result = get_models()
        if result.get("ok"):
            names: list[str] = []
            for item in result.get("models", []):
                for key in ("name", "model"):
                    value = item.get(key)
                    if value:
                        names.append(str(value))
            available_models = names
    except Exception:
        available_models = None

    decision = resolve_model_for_route("code", model, available_models)

    return decision.model, int(num_ctx or 0), decision


SUMMARIZE_SYSTEM_PROMPT = (
    "Ты сжимаешь предыдущий диалог пользователя с code-агентом в краткое summary "
    "которое заменит исходные сообщения в контексте, чтобы освободить токены.\n"
    "Сохрани:\n"
    "- пути к файлам и модули которые обсуждались\n"
    "- архитектурные решения и договорённости\n"
    "- состояние задач (что сделано, что не доделано)\n"
    "- какие инструменты вызывались и их краткие итоги: созданные/правленные "
    "файлы, выполненные команды и их ok/exit-статус\n"
    "- конвенции/стиль/правила которые пользователь упоминал\n"
    "- найденные баги и их статус\n"
    "Не пиши:\n"
    "- полное содержимое файлов\n"
    "- полные выводы инструментов\n"
    "- общие фразы и вежливость\n"
    "Блоки с пометкой PRIOR_SUMMARY / [PRIOR SUMMARY] — это прежние сжатия: "
    "интегрируй их факты в итог, не дублируя.\n"
    "Формат: маркированный список 5-15 строк, плотный, без воды. На русском."
)


def summarize_history(
    messages: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    num_ctx: int | None = None,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the LLM to summarize the given user/assistant messages into a
    compact bulleted summary. Returns {'ok': bool, 'summary': str,
    'error': str | None, 'turn_count': int}.

    The summary is one assistant-shaped text block intended to replace
    the original messages in conversation_history on the next agent
    invocation.
    """
    cleaned = _coerce_history(messages)
    if not cleaned:
        return {"ok": True, "summary": "", "error": None, "turn_count": 0}

    # P9.3: never let the "auto" sentinel reach the provider as a literal model name —
    # resolve it through the same code route first (concrete callers are a no-op).
    from app.core.config import is_auto_route

    if is_auto_route(model):
        model = _resolve_code_route(model, num_ctx)[0]

    chat = chat_fn or _local_chat
    from app.application.context.profile import resolve_context_window

    context_profile = resolve_context_window(
        num_ctx,
        model=model,
        live=chat_fn is None,
        fresh=True,
    )
    effective_num_ctx = int(context_profile["ctx_size"])

    safe_input_tokens = max(1, int(context_profile["safe_input_budget"]))
    transcript_cap = max(4_096, safe_input_tokens * 2)
    per_message_cap = max(1_024, transcript_cap // 16)

    # First pass: walk newest -> oldest, take as much as fits.
    rev_lines: list[str] = []
    total = 0
    dropped_any = False
    for m in reversed(cleaned):
        role = m["role"]
        content = m["content"]
        if role == "system" or content.startswith(_RUNTIME_CONTEXT_PREFIX):
            # Runtime context from earlier turns stays assistant-shaped for the
            # main Qwen call, but the summarizer must still treat it as prior
            # context rather than as a normal agent answer.
            prefix = "PRIOR_SUMMARY:"
        elif role == "user":
            prefix = "USER:"
        else:
            prefix = "AGENT:"
        if len(content) > per_message_cap:
            content = content[:per_message_cap] + " [...]"
        line = f"{prefix} {content}"
        # +2 for the "\n\n" separator we'll add when joining
        if rev_lines and total + len(line) + 2 > transcript_cap:
            dropped_any = True
            break
        rev_lines.append(line)
        total += len(line) + 2

    lines = list(reversed(rev_lines))
    if dropped_any:
        lines.insert(
            0,
            "[... earlier messages dropped to stay under transcript cap; "
            "only most recent shown ...]",
        )
    transcript = "\n\n".join(lines)

    try:
        response = chat(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                {"role": "user", "content": "Диалог для сжатия:\n\n" + transcript},
            ],
            options={
                "num_ctx": effective_num_ctx,
                "active_context_limit": effective_num_ctx,
            },
        )
    except Exception as exc:
        logger.exception("Summarize history failed")
        return {"ok": False, "summary": "", "error": str(exc), "turn_count": len(cleaned)}

    msg = (response or {}).get("message") or {}
    text = (msg.get("content") or "").strip()
    return {"ok": True, "summary": text, "error": None, "turn_count": len(cleaned)}
