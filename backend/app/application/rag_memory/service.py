"""RAG memory service wiring backed by SQLite and the local embedding endpoint."""

from __future__ import annotations

import logging
import sqlite3

from app.application.rag_memory import runtime as rag_runtime
from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

logger = logging.getLogger(__name__)


DB_PATH = sqlite_data_file("rag_memory.db", key_tables=("rag_items",))
SEED_RAG_TEXT = "rag alpha memory"
EMBED_MODEL = "local-embed"
# The store can decode legacy rows of any dimension, while the provider validates
# every new vector against the fixed provider dimension before it reaches this
# service. This records the live `local-embed` (Qwen3-Embedding-0.6B) space. The
# legacy nomic 768-dim vectors were already migrated via scripts/reembed_rag.py.
EMBED_DIM = 1024


def _effective_embed_model() -> str:
    """The embedding model actually in use by the local embedding endpoint."""
    from app.infrastructure.llm.openai_compatible import local_embed_config

    cfg = local_embed_config()
    return cfg.model if cfg.enabled else EMBED_MODEL


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _init() -> None:
    rag_runtime.init_db(conn_factory=_conn)


def _cleanup_seed_data() -> None:
    rag_runtime.cleanup_seed_data(conn_factory=_conn, seed_rag_text=SEED_RAG_TEXT)


_init()
_cleanup_seed_data()


def _get_embedding(text: str) -> list[float] | None:
    return rag_runtime.get_embedding(embed_model=EMBED_MODEL, text=text)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    return rag_runtime.cosine_sim(a, b)


def add_to_rag(
    text: str,
    category: str = "fact",
    importance: int = 5,
    project: str = "",
    source_uri: str = "",
    source_hash: str = "",
    metadata: dict | None = None,
) -> dict:
    return rag_runtime.add_to_rag(
        conn_factory=_conn,
        get_embedding_func=_get_embedding,
        text=text,
        category=category,
        importance=importance,
        project=project,
        source_uri=source_uri,
        source_hash=source_hash,
        metadata=metadata,
    )


def search_rag(
    query: str,
    limit: int = 5,
    min_score: float = 0.3,
    project: str | None = None,
) -> dict:
    return rag_runtime.search_rag(
        conn_factory=_conn,
        get_embedding_func=_get_embedding,
        cosine_sim_func=_cosine_sim,
        query=query,
        limit=limit,
        min_score=min_score,
        project=project,
    )


def get_rag_context(
    query: str,
    max_items: int = 5,
    max_chars: int = 2000,
    project: str | None = None,
) -> str:
    return rag_runtime.get_rag_context(
        search_rag_func=lambda q, limit: search_rag(q, limit=limit, project=project),
        query=query,
        max_items=max_items,
        max_chars=max_chars,
    )


def list_rag(limit: int = 50) -> dict:
    return rag_runtime.list_rag(conn_factory=_conn, limit=limit)


def delete_rag(item_id: int) -> dict:
    return rag_runtime.delete_rag(conn_factory=_conn, item_id=item_id)


def rag_stats() -> dict:
    from app.infrastructure.llm.openai_compatible import local_embed_config

    result = rag_runtime.rag_stats(
        conn_factory=_conn,
        embed_model=_effective_embed_model(),
    )
    result["embedding_enabled"] = local_embed_config().enabled
    return result


def prune_rag(
    max_age_days: int = 30,
    max_importance: int = 4,
    categories: tuple[str, ...] = ("agent_turn", "verified_turn"),
    dry_run: bool = False,
) -> dict:
    """Evict stale, never-recalled machine-made memories (decay). See
    rag_runtime.prune_rag for the candidate criteria — user facts are never
    touched."""
    return rag_runtime.prune_rag(
        conn_factory=_conn,
        max_age_days=max_age_days,
        max_importance=max_importance,
        categories=categories,
        dry_run=dry_run,
    )


# ── Reflection → episodic memory ────────────────────────────────────────────

_REFLECTION_SYSTEM_PROMPT = (
    "Ты подводишь итог завершённого диалога пользователя с ассистентом в одну "
    "плотную заметку-эпизод для долговременной памяти. Сохрани: о чём шла речь "
    "(темы), какие факты и предпочтения пользователь сообщил о себе, какие "
    "решения были приняты, что осталось открытым/нерешённым. 3-8 предложений, "
    "на русском, без вежливости и воды. Не выдумывай того, чего в диалоге не "
    "было."
)
_REFLECTION_TRANSCRIPT_CAP = 24000
_REFLECTION_PER_MESSAGE_CAP = 3000


def _build_reflection_transcript(turns: list[dict]) -> str:
    """Newest-first packing under a char cap (mirrors summarize_history), then
    restored to chronological order."""
    rev: list[str] = []
    total = 0
    for m in reversed(turns):
        prefix = "ПОЛЬЗОВАТЕЛЬ:" if m.get("role") == "user" else "АССИСТЕНТ:"
        content = (m.get("content") or "")
        if len(content) > _REFLECTION_PER_MESSAGE_CAP:
            content = content[:_REFLECTION_PER_MESSAGE_CAP] + " [...]"
        line = f"{prefix} {content}"
        if rev and total + len(line) + 2 > _REFLECTION_TRANSCRIPT_CAP:
            break
        rev.append(line)
        total += len(line) + 2
    return "\n\n".join(reversed(rev))


def _fallback_summary(turns: list[dict]) -> str:
    """Deterministic, LLM-free episode used when the local model is disabled or
    fails — keeps reflection working (degraded) instead of producing nothing."""
    user_lines = [
        (m.get("content") or "").strip().replace("\n", " ")[:160]
        for m in turns
        if m.get("role") == "user"
    ]
    user_lines = [line for line in user_lines if line]
    if not user_lines:
        return ""
    joined = "; ".join(user_lines[:8])
    return ("Обсуждалось: " + joined)[:600]


def _summarize_for_reflection(turns: list[dict]) -> dict:
    """LLM reflection summary with a deterministic fallback. Always returns
    ok=True with a non-empty summary when there is any user content."""
    transcript = _build_reflection_transcript(turns)
    try:
        from app.infrastructure.llm.openai_compatible import (
            chat_completion,
            is_local_llm_enabled,
            local_llm_config,
        )

        if is_local_llm_enabled():
            response = chat_completion(
                model=local_llm_config().model,
                messages=[
                    {"role": "system", "content": _REFLECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": "Диалог:\n\n" + transcript},
                ],
                options={"num_ctx": 32768, "active_context_limit": 32768},
            )
            text = ((response or {}).get("message") or {}).get("content", "").strip()
            if text:
                return {"ok": True, "summary": text, "error": None}
    except Exception as exc:
        logger.warning("reflection LLM failed, using fallback: %s", exc)

    fallback = _fallback_summary(turns)
    return {"ok": bool(fallback), "summary": fallback, "error": None if fallback else "empty"}


def reflect_chat(chat_id: int, min_messages: int = 4) -> dict:
    """Reflect a stored chat into a durable episodic RAG row and mark the chat
    as saved. Idempotent: re-reflecting replaces the chat's prior episode."""
    from app.application.elira_memory.service import (
        get_messages,
        list_chats,
        set_chat_memory_saved,
    )
    from app.application.rag_memory import reflection as _reflection

    messages = get_messages(chat_id)
    title = ""
    try:
        for chat in list_chats():
            if str(chat.get("id")) == str(chat_id):
                title = chat.get("title") or ""
                break
    except Exception:
        title = ""

    def _delete_prior(project: str, category: str) -> int:
        conn = _conn()
        try:
            cur = conn.execute(
                "DELETE FROM rag_items WHERE project = ? AND category = ?",
                (project, category),
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()

    result = _reflection.reflect_chat(
        chat_id=chat_id,
        chat_title=title,
        messages=messages,
        summarize_fn=_summarize_for_reflection,
        add_to_rag_func=add_to_rag,
        delete_prior_func=_delete_prior,
        min_messages=min_messages,
    )

    if result.get("ok") and result.get("action") == "stored":
        try:
            set_chat_memory_saved(chat_id, True)
        except Exception as exc:
            logger.debug("reflect_chat: set memory_saved failed (non-fatal): %s", exc)

    return result
