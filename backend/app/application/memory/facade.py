"""MemoryService — single entry point over Elira's two long-term memory engines.

Two engines, one door:

  - **facts** → ``smart_memory`` (lexical, curated, profile-scoped): the
    "I know THIS about you" store. Backs the "Память чата" UI and the agent's
    ``search_memory`` tool.
  - **semantic / episodic** → ``rag_memory`` (vectors + reflection episodes):
    the "what's related / what did we discuss" store, with decay.

``recall()`` combines both into one ready-to-inject context blob — the intended
single recall call for the chat / code-agent path (wiring it into the live path
is a later phase). The per-engine helpers stay for callers that need one side.

Engine imports are lazy (inside functions) to match the house style and avoid
triggering each engine's import-time DB init just by importing this module.
"""

from __future__ import annotations

from typing import Any


def default_profile() -> str:
    from app.application import smart_memory

    return smart_memory.DEFAULT_PROFILE


def _profile(profile: str | None) -> str:
    return profile or default_profile()


# ── Facts (smart_memory) ─────────────────────────────────────────────────────

def add_fact(
    text: str,
    *,
    category: str = "fact",
    source: str = "manual",
    importance: int = 5,
    profile: str | None = None,
) -> dict[str, Any]:
    from app.application import smart_memory
    from app.application.memory.policy import normalize_fact_category

    return smart_memory.add_memory(
        text,
        category=normalize_fact_category(text, category),
        source=source,
        importance=importance,
        profile_name=_profile(profile),
    )


def search_facts(query: str, *, limit: int = 10, profile: str | None = None) -> dict[str, Any]:
    from app.application import smart_memory

    return smart_memory.search_memory(query, limit=limit, profile_name=_profile(profile))


def list_facts(
    *, limit: int = 50, profile: str | None = None, category: str | None = None
) -> dict[str, Any]:
    from app.application import smart_memory

    return smart_memory.list_memories(
        category=category, limit=limit, profile_name=_profile(profile)
    )


def delete_fact(mem_id: int | str, *, profile: str | None = None) -> dict[str, Any]:
    from app.application import smart_memory

    return smart_memory.delete_memory(int(mem_id), profile_name=_profile(profile))


def fact_stats(*, profile: str | None = None) -> dict[str, Any]:
    from app.application import smart_memory

    return smart_memory.get_stats(profile_name=_profile(profile))


def fact_context(query: str, *, max_items: int = 5, profile: str | None = None) -> str:
    items = authoritative_facts(query, limit=max_items, profile=profile)
    if not items:
        return ""
    lines: list[str] = []
    total = 0
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        line = f"- {text}"
        if total + len(line) > 1500:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return (
        "Заметки о пользователе (используй ТОЛЬКО если они прямо относятся к вопросу; "
        "временные состояния сервера здесь намеренно исключены):\n"
        + "\n".join(lines)
    )


def authoritative_facts(
    query: str,
    *,
    limit: int = 8,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Relevant durable facts only; never a dump of the whole memory store."""
    from app.application.memory.policy import is_authoritative_fact

    safe_limit = max(1, int(limit))
    result = search_facts(
        query,
        limit=max(safe_limit * 3, safe_limit),
        profile=profile,
    )
    items = result.get("items", []) or []
    return [item for item in items if is_authoritative_fact(item)][:safe_limit]


def list_profiles() -> dict[str, Any]:
    from app.application import smart_memory

    return smart_memory.list_profiles()


# ── Semantic / episodic (rag_memory) ─────────────────────────────────────────

def add_semantic(
    text: str, *, category: str = "fact", importance: int = 5, project: str = ""
) -> dict[str, Any]:
    from app.application.rag_memory import service as rag

    return rag.add_to_rag(text, category=category, importance=importance, project=project)


def search_semantic(
    query: str, *, limit: int = 5, min_score: float = 0.3, project: str | None = None
) -> dict[str, Any]:
    from app.application.rag_memory import service as rag

    return rag.search_rag(query, limit=limit, min_score=min_score, project=project)


def semantic_context(
    query: str, *, max_items: int = 5, max_chars: int = 2000, project: str | None = None
) -> str:
    from app.application.rag_memory import service as rag

    return rag.get_rag_context(query, max_items=max_items, max_chars=max_chars, project=project)


def reflect_chat(chat_id: int, *, min_messages: int = 4) -> dict[str, Any]:
    from app.application.rag_memory import service as rag

    return rag.reflect_chat(chat_id, min_messages=min_messages)


def prune(*, max_age_days: int = 30, max_importance: int = 3, dry_run: bool = False) -> dict[str, Any]:
    from app.application.rag_memory import service as rag

    return rag.prune_rag(max_age_days=max_age_days, max_importance=max_importance, dry_run=dry_run)


# ── Unified recall (both engines) ────────────────────────────────────────────

def recall(
    query: str,
    *,
    profile: str | None = None,
    project: str | None = None,
    fact_limit: int = 5,
    semantic_limit: int = 3,
    max_chars: int = 2000,
) -> dict[str, Any]:
    """Single recall over both engines: curated facts (lexical) + semantic /
    episodic (vector). Returns the raw context blobs plus a combined, length-
    capped ``context`` string ready to prepend to a prompt. This is the one
    door the live recall path should call once it is wired in."""
    facts = fact_context(query, max_items=fact_limit, profile=profile) if fact_limit > 0 else ""
    semantic = (
        semantic_context(query, max_items=semantic_limit, project=project)
        if semantic_limit > 0
        else ""
    )
    parts = [part for part in (facts, semantic) if part]
    context = "\n\n".join(parts)
    if len(context) > max_chars:
        context = context[:max_chars]
    return {"ok": True, "facts": facts, "semantic": semantic, "context": context}
