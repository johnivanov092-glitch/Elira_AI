"""RAG memory compatibility facade."""

from __future__ import annotations

from app.application.rag_memory.service import (
    DB_PATH,
    EMBED_DIM,
    EMBED_MODEL,
    SEED_RAG_TEXT,
    _cleanup_seed_data,
    _conn,
    _cosine_sim,
    _get_embedding,
    _init,
    add_to_rag,
    delete_rag,
    get_rag_context,
    list_rag,
    rag_stats,
    search_rag,
)

__all__ = [
    "DB_PATH",
    "EMBED_DIM",
    "EMBED_MODEL",
    "SEED_RAG_TEXT",
    "_cleanup_seed_data",
    "_conn",
    "_cosine_sim",
    "_get_embedding",
    "_init",
    "add_to_rag",
    "delete_rag",
    "get_rag_context",
    "list_rag",
    "rag_stats",
    "search_rag",
]
