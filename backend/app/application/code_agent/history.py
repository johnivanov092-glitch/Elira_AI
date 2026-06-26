"""Conversation-history helpers and rolling summarization for the code-agent.

Extracted from ``agent_loop.py`` to shrink that module and give the
summarization path a home that does NOT import ``agent_loop`` — breaking
the import cycle that previously blocked this split (``agent_loop`` uses
``summarize_history`` as ``summarize_fn=...``, while the function needs
``_coerce_history`` / ``_local_chat`` / ``_resolve_code_route`` and the
``DEFAULT_MODEL`` / ``DEFAULT_NUM_CTX`` constants).

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

# The frontend tags compressed summary turns with this prefix; _coerce_history
# re-tags them as system messages so the LLM treats them as out-of-band context
# rather than as its own prior reply.
_SUMMARY_PREFIX = "[CONTEXT SUMMARY]"

DEFAULT_MODEL = "local-model"
# A long-thinking local model must not be cut off mid-task; this large window is
# the default code-agent tool context.
DEFAULT_NUM_CTX = 131072


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

    Compressed summary turns (assistant messages with the
    `[CONTEXT SUMMARY]` prefix that the frontend produces after a
    `summarize_history` call) are re-tagged as system messages so the
    LLM treats them as out-of-band context rather than as its own prior
    reply — that prevents 'I never said that' confusion.
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
                "role": "system",
                "content": (
                    "Earlier conversation summary (compressed from prior turns by the "
                    "user — treat as context, not as your own previous answer):\n"
                    + stripped
                ),
            })
            continue
        out.append({"role": role, "content": content})
    return out


def _resolve_code_route(model: str, num_ctx: int, *, agent_id: str = "code-agent") -> tuple[str, int, Any]:
    """P9.3: route code-agent through the shared model order (route='code'):
    explicit model -> enabled 'code' profile (if installed) -> route_model_map
    -> DEFAULT_MODEL. Effective num_ctx = min(requested, monitoring cap,
    selected profile context_limit), except a profile is not allowed to shrink
    the default code-agent tool window below DEFAULT_NUM_CTX.

    MODEL_SAFE_CTX is deliberately NOT applied for code-agent: it is a
    conservative chat-safe table (not a confirmed hard provider limit), while
    code-agent intentionally uses a large tool-context window (DEFAULT_NUM_CTX).
    Applying it can regress the default code-agent tool window. We skip it
    by NOT passing `model=` to effective_context_limit.
    """
    from app.core.config import effective_context_limit, resolve_model_for_route

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

    monitoring_max = None
    try:
        from app.application.monitoring.runtime import ensure_agent_limit

        # ensure_agent_limit (not get_agent_limit): the default max_context_tokens
        # must participate in effective_num_ctx even before any limit row exists,
        # otherwise a request above the default cap reaches preflight uncapped and
        # gets blocked.
        limit = ensure_agent_limit(agent_id or "code-agent") or {}
        cap = int(limit.get("max_context_tokens") or 0)
        monitoring_max = cap if cap > 0 else None
    except Exception:
        monitoring_max = None

    profile_ctx = decision.context_limit if decision.source == "profile" else None
    if profile_ctx is not None:
        profile_ctx = max(int(profile_ctx), DEFAULT_NUM_CTX)
    effective = effective_context_limit(
        int(num_ctx),
        monitoring_max_context=monitoring_max,
        profile_context_limit=profile_ctx,
    )
    return decision.model, int(effective), decision


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
    num_ctx: int = DEFAULT_NUM_CTX,
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

    # Build a compact transcript to summarize. Two layers of protection:
    #
    #   1. Per-message cap (4000 chars) so a single long agent answer
    #      doesn't dominate the input.
    #   2. Total transcript cap (TRANSCRIPT_CAP, ~30K chars ≈ 7.5K
    #      tokens) so the whole prompt + SUMMARIZE_SYSTEM_PROMPT fits
    #      inside `num_ctx` with room to spare. Without this, a long
    #      session (50+ turns) would silently produce a garbage summary
    #      because small provider defaults can truncate instructions off the front.
    #
    # When the cap kicks in we keep the MOST RECENT turns (oldest are
    # least relevant) and emit a marker so the LLM knows context is
    # incomplete.
    TRANSCRIPT_CAP = 30000
    PER_MESSAGE_CAP = 4000

    # First pass: walk newest -> oldest, take as much as fits.
    rev_lines: list[str] = []
    total = 0
    dropped_any = False
    for m in reversed(cleaned):
        role = m["role"]
        content = m["content"]
        if role == "system":
            # System messages (re-tagged summary turns from earlier
            # compressions) should appear with a distinctive prefix
            # so the summarizer treats them as prior summary context.
            prefix = "PRIOR_SUMMARY:"
        elif role == "user":
            prefix = "USER:"
        else:
            prefix = "AGENT:"
        if len(content) > PER_MESSAGE_CAP:
            content = content[:PER_MESSAGE_CAP] + " [...]"
        line = f"{prefix} {content}"
        # +2 for the "\n\n" separator we'll add when joining
        if rev_lines and total + len(line) + 2 > TRANSCRIPT_CAP:
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
            options={"num_ctx": int(num_ctx), "active_context_limit": int(num_ctx)},
        )
    except Exception as exc:
        logger.exception("Summarize history failed")
        return {"ok": False, "summary": "", "error": str(exc), "turn_count": len(cleaned)}

    msg = (response or {}).get("message") or {}
    text = (msg.get("content") or "").strip()
    return {"ok": True, "summary": text, "error": None, "turn_count": len(cleaned)}
