"""memory.py — optional vector memory capability check.

The active memory implementation lives in app.application.memory.smart_memory.
This module retains only the dependency-probe used by the status endpoint.
"""
import importlib.util
from typing import Any, Dict, List


def _has(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def vector_memory_capability_status() -> Dict[str, Any]:
    missing: List[str] = []
    if not _has("sentence_transformers"):
        missing.append("sentence-transformers")
    if not _has("faiss"):
        missing.append("faiss-cpu")
    if not _has("numpy"):
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
