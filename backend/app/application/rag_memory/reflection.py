"""Chat reflection → episodic memory.

Consolidates a finished conversation into a single durable "episode" row in
RAG. Because episodes live in the same ``rag_items`` table as facts, the
existing ``search_rag`` / ``enrich_context_with_memory`` recall path surfaces
them automatically in later chats — episodic recall comes for free. The decay
job (``prune_rag``) only targets ``agent_turn`` rows, so episodes are never
auto-evicted.

This module is pure orchestration: every side effect (LLM summarization, RAG
write, prior-episode cleanup) is injected, so it is trivially testable and the
service layer wires the real implementations.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

EPISODE_CATEGORY = "episode"
DEFAULT_MIN_MESSAGES = 4
DEFAULT_IMPORTANCE = 6
_MAX_TITLE = 80


def reflect_chat(
    *,
    chat_id: Any,
    chat_title: str,
    messages: list[dict[str, Any]],
    summarize_fn: Callable[[list[dict[str, Any]]], dict[str, Any]],
    add_to_rag_func: Callable[..., dict[str, Any]],
    delete_prior_func: Callable[[str, str], int],
    min_messages: int = DEFAULT_MIN_MESSAGES,
    importance: int = DEFAULT_IMPORTANCE,
) -> dict[str, Any]:
    """Reflect a chat into one episodic RAG row.

    Args (all I/O injected for testability):
        summarize_fn(turns) -> {"ok": bool, "summary": str, "error"?: str}
        add_to_rag_func(text=, category=, importance=, project=) -> dict
        delete_prior_func(project, category) -> int  — removes the chat's prior
            episode so re-reflection replaces rather than duplicates.

    Returns a dict with ``action`` one of: ``skipped`` (too short),
    ``failed`` (no summary), ``stored`` (episode written).
    """
    turns = [
        {"role": m.get("role"), "content": (m.get("content") or "").strip()}
        for m in (messages or [])
        if m.get("role") in {"user", "assistant"} and (m.get("content") or "").strip()
    ]
    if len(turns) < max(2, int(min_messages)):
        return {"ok": True, "action": "skipped", "reason": "too_short", "turns": len(turns)}

    result = summarize_fn(turns) or {}
    summary = (result.get("summary") or "").strip()
    if not result.get("ok") or not summary:
        return {
            "ok": False,
            "action": "failed",
            "reason": result.get("error") or "empty_summary",
            "turns": len(turns),
        }

    project = f"chat:{chat_id}"
    try:
        delete_prior_func(project, EPISODE_CATEGORY)
    except Exception as exc:  # cleanup is best-effort; a stale dup is tolerable
        logger.debug("reflect_chat: prior-episode cleanup failed (non-fatal): %s", exc)

    title = (chat_title or "").strip()[:_MAX_TITLE]
    episode_text = f"[episode] {title}: {summary}" if title else f"[episode] {summary}"
    add = add_to_rag_func(
        text=episode_text,
        category=EPISODE_CATEGORY,
        importance=int(importance),
        project=project,
    )
    return {
        "ok": True,
        "action": "stored",
        "summary": summary,
        "project": project,
        "rag": add,
        "turns": len(turns),
    }
