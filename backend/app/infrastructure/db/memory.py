"""memory.py — vector-memory capability probe.

Elira's vector memory is rag_memory.db (Ollama nomic-embed-text embeddings
compared as numpy buffers) and is part of core. numpy is the only hard
requirement; when present, semantic recall is active, otherwise it degrades to
keyword search. (The old FAISS/sentence-transformers stack was removed — no
code used it.)

Single source of truth for the capability shape consumed by the project-brain
status endpoint and the smoke-contract check.
"""
import importlib.util
from typing import Any, Dict


def vector_memory_capability_status() -> Dict[str, Any]:
    has_numpy = importlib.util.find_spec("numpy") is not None
    return {
        "feature": "vector_memory",
        "available": has_numpy,
        "mode": "vector" if has_numpy else "keyword_fallback",
        "reason": None if has_numpy else "numpy_missing",
        "missing_packages": [] if has_numpy else ["numpy"],
        "hint": None if has_numpy else "pip install -r requirements.txt",
    }
