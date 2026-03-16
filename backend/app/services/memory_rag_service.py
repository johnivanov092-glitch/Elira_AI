from __future__ import annotations
from typing import List, Dict, Any

class MemoryRAGService:
    def __init__(self, vector_store, embedding_service):
        self.vector_store = vector_store
        self.embedding_service = embedding_service

    def add_memories(self, memories: List[Dict[str, Any]]) -> int:
        texts = [m.get("text", "") for m in memories]
        vectors = self.embedding_service.encode(texts)
        return self.vector_store.add(vectors, memories)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        qv = self.embedding_service.encode([query])[0]
        return self.vector_store.search(qv, top_k=top_k)
