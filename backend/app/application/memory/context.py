from __future__ import annotations

import re
from typing import Any, Callable

from app.domain.memory.knowledge_base import build_kb_context, build_tool_memory_context
from app.domain.memory.strategy_tracking import build_web_learning_context


LoadMemoriesFunc = Callable[..., list[Any]]
SearchMemoryFunc = Callable[..., list[str]]
ContentHashFunc = Callable[[str], str]


def _memory_type_weight(memory_type: str, pinned: bool = False, source: str = "") -> float:
    memory_type = (memory_type or "").strip().lower()
    source = (source or "").strip().lower()
    weight_map = {
        "profile": 4.2,
        "pinned": 4.0,
        "insight": 3.4,
        "orchestrator": 3.1,
        "summary": 2.9,
        "file": 2.4,
        "chat_snapshot": 1.8,
        "chat": 1.0,
        "general": 1.3,
    }
    weight = weight_map.get(memory_type, 1.2)
    if pinned:
        weight += 1.2
    if source.startswith("manual"):
        weight += 0.3
    return weight


def _clean_memory_text(text: str, max_chars: int = 900) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars] + " …"


def _memory_query_words(query: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[\wа-яА-ЯёЁ-]+", (query or "").lower())
        if len(word) >= 3
    ]


def search_memories_weighted(
    *,
    query: str,
    profile_name: str = "",
    top_k: int = 8,
    load_memories_func: LoadMemoriesFunc,
    semantic_search_memory_func: SearchMemoryFunc,
    keyword_search_memory_func: SearchMemoryFunc,
    content_hash_func: ContentHashFunc,
) -> list[dict[str, Any]]:
    rows = load_memories_func(2500, profile_name=profile_name)
    if not rows:
        return []

    normalized_query = (query or "").strip().lower()
    query_words = _memory_query_words(query)

    semantic_hits = (
        set(
            semantic_search_memory_func(
                query,
                top_k=max(top_k * 2, 8),
                profile_name=profile_name,
            )
        )
        if normalized_query
        else set()
    )
    keyword_hits = (
        set(
            keyword_search_memory_func(
                query,
                top_k=max(top_k * 2, 8),
                profile_name=profile_name,
            )
        )
        if normalized_query
        else set()
    )

    scored: list[dict[str, Any]] = []
    for row_id, content, source, created_at, pinned, memory_type, profile in rows:
        text = (content or "").strip()
        if not text:
            continue

        score = _memory_type_weight(memory_type, bool(pinned), source)
        lower_text = text.lower()

        if normalized_query:
            if normalized_query in lower_text:
                score += 8.0
            score += sum(1.6 for word in query_words if word in lower_text)
            if text in semantic_hits:
                score += 3.0
            if text in keyword_hits:
                score += 2.2

        if (memory_type or "").lower() == "chat" and score < 5:
            score -= 1.4
        if len(text) > 3000:
            score -= 0.5
        if score <= 0:
            continue

        scored.append(
            {
                "id": row_id,
                "content": text,
                "source": source,
                "created_at": created_at,
                "pinned": bool(pinned),
                "memory_type": memory_type,
                "profile_name": profile,
                "score": round(score, 3),
            }
        )

    scored.sort(key=lambda item: (item["score"], item["created_at"], item["id"]), reverse=True)

    unique: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for item in scored:
        dedupe_key = content_hash_func(_clean_memory_text(item["content"], max_chars=400))
        if dedupe_key in seen_hashes:
            continue
        seen_hashes.add(dedupe_key)
        unique.append(item)
        if len(unique) >= top_k:
            break
    return unique


def build_memory_context(
    *,
    query: str,
    profile_name: str,
    top_k: int = 5,
    load_memories_func: LoadMemoriesFunc,
    semantic_search_memory_func: SearchMemoryFunc,
    keyword_search_memory_func: SearchMemoryFunc,
    content_hash_func: ContentHashFunc,
) -> str:
    pinned_rows = load_memories_func(20, only_pinned=True, profile_name=profile_name)
    weighted = search_memories_weighted(
        query=query,
        profile_name=profile_name,
        top_k=max(top_k + 3, 8),
        load_memories_func=load_memories_func,
        semantic_search_memory_func=semantic_search_memory_func,
        keyword_search_memory_func=keyword_search_memory_func,
        content_hash_func=content_hash_func,
    )
    kb_ctx = build_kb_context(query, profile_name=profile_name, top_k=max(2, top_k // 2))
    tool_ctx = build_tool_memory_context(query, profile_name=profile_name, limit=3)
    weblearn_ctx = build_web_learning_context(query, profile_name=profile_name, limit=3)

    parts: list[str] = []

    if pinned_rows:
        pinned_lines: list[str] = []
        seen_pinned: set[str] = set()
        for row in pinned_rows[:6]:
            text = _clean_memory_text(row[1], max_chars=700)
            dedupe_key = content_hash_func(text)
            if dedupe_key in seen_pinned:
                continue
            seen_pinned.add(dedupe_key)
            pinned_lines.append(f"- {text}")
        if pinned_lines:
            parts.append("Закреплённая память:\n" + "\n".join(pinned_lines))

    if weighted:
        weighted_lines: list[str] = []
        pinned_hashes = {
            content_hash_func(_clean_memory_text(row[1], max_chars=700))
            for row in pinned_rows[:20]
        }
        for item in weighted:
            text = _clean_memory_text(item["content"], max_chars=700)
            dedupe_key = content_hash_func(text)
            if dedupe_key in pinned_hashes:
                continue
            tag = (item.get("memory_type") or "general").lower()
            weighted_lines.append(f"- [{tag}] {text}")
        if weighted_lines:
            parts.append("Релевантная память:\n" + "\n".join(weighted_lines[: max(top_k, 6)]))

    if kb_ctx:
        parts.append(kb_ctx)
    if tool_ctx:
        parts.append(tool_ctx)
    if weblearn_ctx:
        parts.append(weblearn_ctx)

    context = "\n\n".join(part for part in parts if part.strip())
    return context[:16000]
