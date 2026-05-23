"""memory.py — optional vector memory capability check.

The active memory implementation lives in app.application.memory.smart_memory.
This module retains only the dependency-probe used by the status endpoint.
"""
from typing import Any, Dict, List

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

try:
    import faiss
except Exception:
    faiss = None

try:
    import numpy as np
except Exception:
    np = None


def vector_memory_capability_status() -> Dict[str, Any]:
    missing: List[str] = []
    if SentenceTransformer is None:
        missing.append("sentence-transformers")
    if faiss is None:
        missing.append("faiss-cpu")
    if np is None:
        missing.append("numpy")

    available = not missing
    return {
        "feature": "vector_memory",
        "available": available,
        "mode": "vector" if available else "keyword_fallback",
        "reason": None if available else "optional_dependency_missing",
        "missing_packages": missing,
        "hint": None if available else "pip install -r requirements-optional.txt",
    }
