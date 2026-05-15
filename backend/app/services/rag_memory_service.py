"""Thin facade — all RAG memory logic lives in application/memory/rag_memory_service.py."""
from app.application.memory.rag_memory_service import (  # noqa: F401
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
