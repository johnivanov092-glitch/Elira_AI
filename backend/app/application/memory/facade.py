"""MemoryService — single entry point over Elira's two long-term memory engines.

Two engines, one door:

  - **facts** → ``smart_memory`` (lexical, curated, profile-scoped): the
    "I know THIS about you" store. Backs the "Память чата" UI and the agent's
    ``search_memory`` tool.
  - **semantic / episodic** → ``rag_memory`` (vectors + reflection episodes):
    the "what's related / what did we discuss" store, with decay.

``recall()`` combines both into one ready-to-inject context blob. The live
chat/code-agent runtime exposes this path through ``recall`` and
``runtime_control(memory_recall)``; the per-engine helpers stay for callers that
need one side.

Engine imports are lazy (inside functions) to match the house style and avoid
triggering each engine's import-time DB init just by importing this module.
"""

from __future__ import annotations

import re
from typing import Any


_OWNERSHIP_CONTEXT_RE = re.compile(
    r"\b(?:помни(?:шь|те)?|мы\s+(?:обсуждали|говорили)|у\s+(?:меня|нас)|"
    r"мо(?:й|я|ё|е|и|ю|его|ей|ём|ем|ему|им|ими|их)|"
    r"наш(?:а|е|и|у|его|ей|ем|ему|им|ими|их)?|"
    r"remember|we\s+discussed|my|our)\b",
    re.IGNORECASE | re.UNICODE,
)
_ENTITY_RE = re.compile(
    r"(?<![\w@])(?:[A-ZА-ЯЁ][a-zа-яё]{2,}|[A-Z][A-Za-z0-9_-]{2,})(?!\w)"
)
_NON_ENTITY_WORDS = frozenset({
    "авто", "без", "вот", "где", "давай", "для", "если", "зачем",
    "как", "какая", "какие", "какой", "когда", "кто", "можно", "нужно",
    "объясни", "покажи", "почему", "привет", "проверь", "продолжи", "сделай",
    "такая", "такой", "что", "who", "what", "when", "where", "why", "how",
    "explain", "show", "check", "continue", "hello", "пользователь", "мой",
    "моя", "моё", "мои", "наш", "наша", "наше", "наши", "elira", "клиент",
    "компания", "проект", "сервер", "заказчик", "партнёр", "партнер",
    "сотрудник", "подрядчик", "организация", "жена", "муж", "мама", "папа",
    "дочь", "сын", "семья", "она", "он", "они", "это", "the", "his", "her",
})
_TECHNICAL_ACRONYMS = frozenset({
    "api", "cpu", "db", "gpu", "http", "https", "llm", "ocr", "rag", "ram",
    "sql", "sse", "ssh", "stt", "tts", "ui", "ux", "vram",
})
_CONTEXT_SEARCH_TERMS = (
    (re.compile(r"\bклиент\w*\b", re.IGNORECASE | re.UNICODE), "клиент"),
    (re.compile(r"\bкомпан\w*\b", re.IGNORECASE | re.UNICODE), "компания"),
    (re.compile(r"\bпроект\w*\b", re.IGNORECASE | re.UNICODE), "проект"),
    (re.compile(r"\bсервер\w*\b", re.IGNORECASE | re.UNICODE), "сервер"),
    (re.compile(r"\bзаказчик\w*\b", re.IGNORECASE | re.UNICODE), "заказчик"),
    (re.compile(r"\bпартн[её]р\w*\b", re.IGNORECASE | re.UNICODE), "партнёр"),
    (re.compile(r"\bсотрудник\w*\b", re.IGNORECASE | re.UNICODE), "сотрудник"),
    (re.compile(r"\bподрядчик\w*\b", re.IGNORECASE | re.UNICODE), "подрядчик"),
    (re.compile(r"\bорганизац\w*\b", re.IGNORECASE | re.UNICODE), "организация"),
    (re.compile(r"\bжен(?:а|у|ы|е|ой)\b", re.IGNORECASE | re.UNICODE), "жена"),
    (re.compile(r"\bмуж(?:а|у|ем)?\b", re.IGNORECASE | re.UNICODE), "муж"),
    (re.compile(r"\bмам(?:а|у|ы|е|ой)\b", re.IGNORECASE | re.UNICODE), "мама"),
    (re.compile(r"\bпап(?:а|у|ы|е|ой)\b", re.IGNORECASE | re.UNICODE), "папа"),
    (re.compile(r"\bдоч(?:ь|ери|ерью)\b", re.IGNORECASE | re.UNICODE), "дочь"),
    (re.compile(r"\bсын(?:а|у|ом)?\b", re.IGNORECASE | re.UNICODE), "сын"),
    (re.compile(r"\bсемь(?:я|и|ю|ёй|е)\b", re.IGNORECASE | re.UNICODE), "семья"),
)


def _stored_fact_entities(text: str) -> tuple[str, ...]:
    """Conservative named anchors; sentence capitalization alone is insufficient."""
    entities: list[str] = []
    seen: set[str] = set()
    for match in _ENTITY_RE.finditer(text or ""):
        value = match.group(0).strip()
        folded = value.casefold()
        if folded in _NON_ENTITY_WORDS or folded in _TECHNICAL_ACRONYMS:
            continue
        prefix = text[:match.start()].rstrip(" \t\"'«(")
        sentence_initial = not prefix or prefix[-1] in ".!?\n"
        labelled = bool(re.match(r"[\"'»)]?\s*[:—–-]", text[match.end():]))
        suffix = text[match.end():]
        owner = re.match(r"\s+(?:это\s+)?(?:мо[йяеёи]|наш[ае]?|наши)\s+", suffix, re.IGNORECASE)
        related = bool(owner and any(
            pattern.match(suffix[owner.end():]) for pattern, _ in _CONTEXT_SEARCH_TERMS
        ))
        identifier = any(char.isdigit() or char in "_-" for char in value) or (
            not value.isupper() and any(char.isupper() for char in value[1:])
        )
        if sentence_initial and not labelled and not identifier and not related:
            continue
        if folded not in seen:
            entities.append(value)
            seen.add(folded)
    return tuple(entities)


def _contains_entity(text: object, entity: str) -> bool:
    return bool(
        re.search(
            rf"(?<!\w){re.escape(entity)}(?!\w)",
            str(text or ""),
            re.IGNORECASE | re.UNICODE,
        )
    )


def _topic_tokens(text: str) -> set[str]:
    # Shared verbs/adjectives do not establish relevance to a private fact.
    return {
        canonical
        for pattern, canonical in _CONTEXT_SEARCH_TERMS
        if pattern.search(text)
    }


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
    replaces_id: int | str | None = None,
) -> dict[str, Any]:
    from app.application import smart_memory
    from app.application.memory.policy import normalize_fact_category

    return smart_memory.add_memory(
        text,
        category=normalize_fact_category(text, category),
        source=source,
        importance=importance,
        profile_name=_profile(profile),
        replaces_id=replaces_id,
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


def prune_volatile_facts(
    *,
    max_age_days: int = 7,
    dry_run: bool = True,
    profile: str | None = None,
) -> dict[str, Any]:
    from app.application import smart_memory

    return smart_memory.prune_volatile_memories(
        max_age_days=max_age_days,
        dry_run=dry_run,
        profile_name=_profile(profile),
    )


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


def resolve_relevant_facts(
    query: str,
    *,
    limit: int = 8,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve trusted user context before generation, with no model call.

    Every substantive query reaches the lexical facts store. Injection is then
    gated by an explicit user/work-context cue or an exact entity found in the
    stored fact. This includes people, clients, companies, projects,
    servers and other user-owned entities without dumping unrelated memory.
    """
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []
    ownership_cue = bool(_OWNERSHIP_CONTEXT_RE.search(normalized_query))
    query_topics = _topic_tokens(normalized_query)

    from app.application.memory.policy import is_authoritative_fact

    safe_limit = max(1, int(limit))
    search_limit = max(safe_limit * 3, safe_limit)
    canonical_terms = [
        canonical
        for pattern, canonical in _CONTEXT_SEARCH_TERMS
        if ownership_cue and pattern.search(normalized_query)
    ]
    search_query = " ".join(dict.fromkeys((normalized_query, *canonical_terms)))
    candidates: list[dict[str, Any]] = []
    seen_candidates: set[object] = set()
    result = search_facts(search_query, limit=search_limit, profile=profile)
    for item in result.get("items", []) or []:
        identity: object = item.get("id")
        if identity is None:
            identity = str(item.get("text") or "").casefold()
        if identity in seen_candidates:
            continue
        seen_candidates.add(identity)
        candidates.append(item)
    ranked: list[tuple[float, int, int, dict[str, Any]]] = []
    for index, item in enumerate(candidates):
        if not is_authoritative_fact(item, allow_legacy_runtime_control=True):
            continue
        fact_text = str(item.get("text") or "")
        exact_entities = {
            entity.casefold()
            for entity in _stored_fact_entities(fact_text)
            if _contains_entity(normalized_query, entity)
        }
        exact_matches = len(exact_entities)
        topic_overlap = len(query_topics.intersection(_topic_tokens(fact_text)))
        if exact_matches == 0 and (not ownership_cue or topic_overlap == 0):
            continue
        score = (
            (100.0 + exact_matches * 10.0)
            if exact_matches
            else max(1.0, 20.0 + topic_overlap * 5.0 - index)
        )
        selected = dict(item)
        selected["retrieval_reason"] = (
            "exact_entity" if exact_matches else "user_context"
        )
        selected["retrieval_score"] = score
        ranked.append((score, int(item.get("importance") or 0), -index, selected))

    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [row[3] for row in ranked[:safe_limit]]


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
    door used by the live recall path."""
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
