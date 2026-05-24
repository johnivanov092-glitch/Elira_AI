from __future__ import annotations

import hashlib
from typing import Any, Callable

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


LoadMemoriesFunc = Callable[..., list[Any]]

_EMBEDDER = None
_FAISS_CACHE: dict[str, Any] = {}


def _get_embedder():
    global _EMBEDDER
    if SentenceTransformer is None:
        return None
    if _EMBEDDER is None:
        _EMBEDDER = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _EMBEDDER


def vector_memory_capability_status() -> dict[str, Any]:
    missing: list[str] = []
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


def keyword_search_memory(
    *,
    query: str,
    top_k: int = 10,
    profile_name: str = "",
    load_memories_func: LoadMemoriesFunc,
) -> list[str]:
    rows = load_memories_func(2000, profile_name=profile_name)
    normalized_query = (query or "").strip()
    if not normalized_query:
        return []

    query_words = normalized_query.lower().split()
    scored: list[tuple[int, str]] = []
    for _, text, *_ in rows:
        lower_text = text.lower()
        score = sum(2 for word in query_words if word in lower_text)
        if normalized_query.lower() in lower_text:
            score += 5
        if score > 0:
            scored.append((score, text))

    scored.sort(reverse=True)
    return [text for _, text in scored[:top_k]]


def semantic_search_memory(
    *,
    query: str,
    top_k: int = 5,
    profile_name: str = "",
    load_memories_func: LoadMemoriesFunc,
) -> list[str]:
    rows = load_memories_func(1000, profile_name=profile_name)
    texts = [row[1] for row in rows]
    if not query or not texts:
        return []

    if not vector_memory_capability_status()["available"]:
        scored = [
            (sum(1 for word in query.lower().split() if word in text.lower()), text)
            for text in texts
        ]
        scored.sort(reverse=True)
        return [text for score, text in scored[:top_k] if score > 0]

    model = _get_embedder()
    if model is None:
        return []

    try:
        texts_key = hashlib.md5(("||".join(texts)).encode()).hexdigest()
        global _FAISS_CACHE

        if _FAISS_CACHE.get("key") == texts_key and _FAISS_CACHE.get("index") is not None:
            index = _FAISS_CACHE["index"]
        else:
            embeddings = np.array(
                model.encode(texts, normalize_embeddings=True),
                dtype="float32",
            )
            index = faiss.IndexFlatIP(embeddings.shape[1])
            index.add(embeddings)
            _FAISS_CACHE = {"key": texts_key, "index": index}
    except Exception:
        embeddings = np.array(
            model.encode(texts, normalize_embeddings=True),
            dtype="float32",
        )
        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)

    query_vector = np.array(
        model.encode([query], normalize_embeddings=True),
        dtype="float32",
    )
    _, ids = index.search(query_vector, min(top_k, len(texts)))
    return [texts[index_id] for index_id in ids[0] if index_id != -1]
