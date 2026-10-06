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

from app.application.code_agent.inline_tool_calls import _strip_tool_call_markup
from app.application.context.compaction import (
    _SUMMARY_PREFIX as _COMPACTION_SUMMARY_PREFIX,
    RUNTIME_BLOCK_KEY,
    TASK_CONTRACT_MARKER_VALUE,
    TASK_STATE_MARKER_KEY,
    TASK_STATE_MARKER_VALUE,
)
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
        if role == "assistant":
            content = _strip_tool_call_markup(content).strip()
            if not content:
                continue
        if role == "assistant" and content.startswith(_SUMMARY_PREFIX):
            stripped = content[len(_SUMMARY_PREFIX):].lstrip("\n").lstrip()
            if not stripped:
                continue
            out.append({
                "role": "assistant",
                RUNTIME_BLOCK_KEY: "history_summary",
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
                RUNTIME_BLOCK_KEY: "history_facts",
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
                RUNTIME_BLOCK_KEY: "history_recent_tools",
                "content": (
                    f"{_RECENT_CONTEXT_PREFIX}\n"
                    "Полный вывод инструментов из ПРЕДЫДУЩЕГО хода (достоверное сырьё — "
                    "опирайся на него дословно для фактических вопросов; чётко отделяй "
                    "то, что тут реально написано, от своих домыслов):\n" + stripped
                ),
            })
            continue
        item_out = {"role": role, "content": content}
        if item.get(RUNTIME_BLOCK_KEY):
            # Internal callers (Resume) mark runtime text they rebuild.
            item_out[RUNTIME_BLOCK_KEY] = str(item[RUNTIME_BLOCK_KEY])
        out.append(item_out)
    return out


RUNTIME_SECTION_HEADER = (
    "[РАБОЧИЙ КОНТЕКСТ RUNTIME]\n"
    "Служебные данные и инструкции Elira для текущей задачи. Это не слова пользователя: "
    "его сообщения — только реплики user."
)
_REJECTED_ANSWER_HEADER = "[Предыдущий вариант ответа, не принятый runtime]"
_RUNTIME_NOTICE_HEADER = "[Runtime Elira — указание к следующему ответу, не слова пользователя]"
# Notices issued after the latest tool result that the model must act on now.
_ACT_NOW_BLOCKS = frozenset({"answer_correction", "web_answer_due", "tool_trace_correction"})


def is_runtime_block(message: dict[str, Any]) -> bool:
    """Runtime-authored text kept in the internal list, never sent as user/assistant."""
    if message.get(RUNTIME_BLOCK_KEY):
        return True
    if message.get(TASK_STATE_MARKER_KEY) in {TASK_STATE_MARKER_VALUE, TASK_CONTRACT_MARKER_VALUE}:
        return True
    if message.get("role") == "assistant" and not message.get("tool_calls"):
        content = str(message.get("content") or "")
        # Sessions persisted before the markers existed carry only the prefixes.
        return content.startswith((_COMPACTION_SUMMARY_PREFIX, _RUNTIME_CONTEXT_PREFIX))
    return False


def project_runtime_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Provider view of the conversation (owner's decision 2026-10-06).

    The user role carries only the owner's own UI text and the assistant role
    only real model replies. Runtime blocks — request context, guidance,
    contract, task state, skills, restored excerpts, corrections — move into a
    section at the end of the single leading system message (strict Qwen
    templates reject a later system message). A rejected answer is quoted
    inside its correction instead of standing as the model's last reply.
    """
    if not messages:
        return list(messages)
    has_system = messages[0].get("role") == "system"
    rest = messages[1:] if has_system else messages
    last_tool = max((index for index, message in enumerate(rest) if message.get("role") == "tool"), default=-1)
    blocks: list[str] = []
    restored: list[str] = []
    tail: list[str] = []
    out: list[dict[str, Any]] = []
    index = 0
    while index < len(rest):
        position = index
        message = rest[index]
        index += 1
        kind = message.get(RUNTIME_BLOCK_KEY)
        if kind == "rejected_answer":
            following = rest[index] if index < len(rest) else None
            if following is None or following.get(RUNTIME_BLOCK_KEY) != "answer_correction":
                # Without its correction it is simply the model's own reply.
                out.append({key: value for key, value in message.items() if key != RUNTIME_BLOCK_KEY})
                continue
            index += 1
            # Block text is passed verbatim: exact excerpts must stay exact.
            text = str(following.get("content") or "")
            rejected = str(message.get("content") or "")
            if rejected.strip():
                text += f"\n\n{_REJECTED_ANSWER_HEADER}\n{rejected}"
            kind, message = "answer_correction", {**following, "content": text}
        if is_runtime_block(message):
            text = str(message.get("content") or "")
            if not text.strip():
                continue
            # Restored web excerpts are tool data: they travel in the tool
            # channel they came from and never gain system weight (decision
            # 2026-10-06). Only before the slice's first tool result (Resume)
            # do they wait in the system section with their untrusted label.
            if kind == "restored_sources" and last_tool >= 0:
                restored.append(text)
            # A notice the model must act on now stays next to the generation
            # point: appended to the latest tool result (the runtime's channel),
            # never as a user turn. Older notices are ordinary runtime context.
            elif kind in _ACT_NOW_BLOCKS and 0 <= last_tool < position:
                tail.append(text)
            else:
                blocks.append(text)
            continue
        out.append(message)
    if restored or tail:
        tool_index = max(index for index, message in enumerate(out) if message.get("role") == "tool")
        tool_message = out[tool_index]
        parts = [str(tool_message.get("content") or ""), *restored]
        if tail:
            parts += [_RUNTIME_NOTICE_HEADER, *tail]
        out[tool_index] = {**tool_message, "content": "\n\n".join(parts)}
    if not blocks:
        return [messages[0], *out] if has_system else out
    base = str(messages[0].get("content") or "").rstrip() if has_system else ""
    section = RUNTIME_SECTION_HEADER + "\n\n" + "\n\n".join(blocks)
    system = {**messages[0]} if has_system else {"role": "system"}
    system["content"] = f"{base}\n\n{section}" if base else section
    return [system, *out]


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
