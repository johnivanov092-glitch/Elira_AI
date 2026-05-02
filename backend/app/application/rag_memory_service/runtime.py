# -*- coding: utf-8 -*-
"""Application-layer runtime for the RAG Memory service.

Owns DB_PATH, embedding constants, connection wiring, and all wrapper
functions that delegate to ``application/rag_memory/runtime.py``.
Pure Python — no HTTP, no FastAPI imports.
"""
from __future__ import annotations

import logging
import sqlite3

from app.application.rag_memory import runtime as rag_runtime
from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

logger = logging.getLogger(__name__)

DB_PATH = sqlite_data_file("rag_memory.db", key_tables=("rag_items",))
SEED_RAG_TEXT = "rag alpha memory"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768


# ── low-level helpers ─────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)


def _init() -> None:
    rag_runtime.init_db(conn_factory=_conn)


def _cleanup_seed_data() -> None:
    rag_runtime.cleanup_seed_data(conn_factory=_conn, seed_rag_text=SEED_RAG_TEXT)


def _get_embedding(text: str) -> list[float] | None:
    return rag_runtime.get_embedding(embed_model=EMBED_MODEL, text=text)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    return rag_runtime.cosine_sim(a, b)


# ── public API ────────────────────────────────────────────────────────────────

def add_to_rag(text: str, category: str = "fact", importance: int = 5) -> dict:
    return rag_runtime.add_to_rag(
        conn_factory=_conn,
        get_embedding_func=_get_embedding,
        text=text,
        category=category,
        importance=importance,
    )


def search_rag(query: str, limit: int = 5, min_score: float = 0.3) -> dict:
    return rag_runtime.search_rag(
        conn_factory=_conn,
        get_embedding_func=_get_embedding,
        cosine_sim_func=_cosine_sim,
        query=query,
        limit=limit,
        min_score=min_score,
    )


def get_rag_context(query: str, max_items: int = 5, max_chars: int = 2000) -> str:
    return rag_runtime.get_rag_context(
        search_rag_func=search_rag,
        query=query,
        max_items=max_items,
        max_chars=max_chars,
    )


def list_rag(limit: int = 50) -> dict:
    return rag_runtime.list_rag(conn_factory=_conn, limit=limit)


def delete_rag(item_id: int) -> dict:
    return rag_runtime.delete_rag(conn_factory=_conn, item_id=item_id)


def rag_stats() -> dict:
    return rag_runtime.rag_stats(conn_factory=_conn, embed_model=EMBED_MODEL)


# ── module-level bootstrap ────────────────────────────────────────────────────

_init()
_cleanup_seed_data()
