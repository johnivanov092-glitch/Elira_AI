from __future__ import annotations
from typing import List, Dict, Any
import numpy as np
import faiss

class RealFaissStore:
    def __init__(self, dim: int):
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim)
        self.items: List[Dict[str, Any]] = []

    def _norm(self, arr):
        faiss.normalize_L2(arr)
        return arr

    def add(self, vectors: List[List[float]], payloads: List[Dict[str, Any]]) -> int:
        if len(vectors) != len(payloads):
            raise ValueError("vectors/payloads size mismatch")
        arr = np.array(vectors, dtype="float32")
        self._norm(arr)
        self.index.add(arr)
        self.items.extend(payloads)
        return len(vectors)

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        if not self.items:
            return []
        q = np.array([query_vector], dtype="float32")
        self._norm(q)
        scores, idxs = self.index.search(q, top_k)
        out = []
        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0 or idx >= len(self.items):
                continue
            payload = dict(self.items[idx])
            payload["score"] = float(score)
            out.append(payload)
        return out
