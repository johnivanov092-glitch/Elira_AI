from __future__ import annotations
from typing import List, Dict, Any

try:
    import faiss  # type: ignore
except Exception:
    faiss = None

class FaissStore:
    def __init__(self, dim: int = 384):
        self.dim = dim
        self.items: List[Dict[str, Any]] = []
        self.index = faiss.IndexFlatIP(dim) if faiss else None

    def add(self, vectors: List[List[float]], payloads: List[Dict[str, Any]]) -> int:
        if len(vectors) != len(payloads):
            raise ValueError("vectors/payloads size mismatch")
        start = len(self.items)
        self.items.extend(payloads)
        if self.index is not None:
            import numpy as np
            arr = np.array(vectors, dtype="float32")
            self.index.add(arr)
        else:
            for i, vec in enumerate(vectors):
                self.items[start + i]["_vector"] = vec
        return len(vectors)

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        if not self.items:
            return []
        if self.index is not None:
            import numpy as np
            q = np.array([query_vector], dtype="float32")
            scores, idxs = self.index.search(q, top_k)
            out = []
            for score, idx in zip(scores[0], idxs[0]):
                if idx < 0:
                    continue
                item = dict(self.items[idx])
                item["score"] = float(score)
                out.append(item)
            return out

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5 or 1.0
            nb = sum(y * y for y in b) ** 0.5 or 1.0
            return dot / (na * nb)

        ranked = []
        for item in self.items:
            score = cosine(query_vector, item.get("_vector", []))
            payload = dict(item)
            payload["score"] = float(score)
            ranked.append(payload)
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked[:top_k]
