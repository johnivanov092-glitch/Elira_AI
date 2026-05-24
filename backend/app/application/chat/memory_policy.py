from __future__ import annotations

import re
from typing import Any, Callable


_DIRECT_PERSONAL_MEMORY_RE = re.compile(
    r"(?iu)^\s*(?:как\s+меня\s+зовут|ты\s+знаешь\s+как\s+меня\s+зовут|what\s+is\s+my\s+name|do\s+you\s+know\s+my\s+name)\s*\??\s*$"
)


def is_direct_personal_memory_query(user_input: str) -> bool:
    return bool(_DIRECT_PERSONAL_MEMORY_RE.search(user_input or ""))


def should_recall_memory_context(
    user_input: str,
    route: str,
    temporal: dict[str, Any] | None,
    *,
    is_memory_command_func: Callable[[str], bool],
) -> bool:
    temporal = temporal or {}
    if is_memory_command_func(user_input):
        return False
    if route == "research" and temporal.get("mode") == "hard" and temporal.get("freshness_sensitive"):
        return False
    return True


def get_memory_recall_limits(user_input: str) -> tuple[int, int]:
    if is_direct_personal_memory_query(user_input):
        return (1, 0)
    return (5, 3)


def trim_history(history: list[Any] | None, max_pairs: int = 10) -> list[Any]:
    if not history:
        return []
    limit = max_pairs * 2
    if len(history) <= limit:
        return list(history)
    first_pair = list(history[:2])
    recent = list(history[-(limit - 2) :])
    return first_pair + recent


def enrich_context_with_memory(
    *,
    planner_input: str,
    route: str,
    temporal: dict[str, Any] | None,
    context: str,
    has_rag: bool,
    is_memory_command_func: Callable[[str], bool],
    get_relevant_context_func: Callable[..., str],
    get_rag_context_func: Callable[..., str],
    append_timeline_func: Callable[[list[dict[str, Any]], str, str, str, str], None] | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> tuple[str, int]:
    if not should_recall_memory_context(
        planner_input,
        route,
        temporal,
        is_memory_command_func=is_memory_command_func,
    ):
        return context, 0

    try:
        mem_limit, rag_limit = get_memory_recall_limits(planner_input)
        memory_context = get_relevant_context_func(planner_input, max_items=mem_limit)
        memory_count = memory_context.count("\n- ") if memory_context else 0
        if has_rag and rag_limit > 0:
            rag_context = get_rag_context_func(planner_input, max_items=rag_limit)
            if rag_context:
                memory_context = (memory_context + "\n\n" + rag_context) if memory_context else rag_context
        if memory_context:
            context = memory_context + "\n\n" + context if context else memory_context
            if append_timeline_func and timeline is not None:
                append_timeline_func(timeline, "memory_recall", "Память", "done", "Найдены релевантные заметки")
        return context, memory_count
    except Exception:
        return context, 0
