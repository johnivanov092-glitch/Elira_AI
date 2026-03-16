from typing import List
from .embedding_service import EmbeddingService
from .faiss_store import FaissStore

class LibraryRAG:
    def __init__(self):
        self.embed = EmbeddingService()
        self.store = FaissStore()

    def index_documents(self, docs: List[str]):
        vecs = self.embed.encode(docs)
        payloads = [{"text": d, "source": "library"} for d in docs]
        self.store.add(vecs, payloads)

    def search(self, query: str):
        qv = self.embed.encode([query])[0]
        return self.store.search(qv, top_k=5)
