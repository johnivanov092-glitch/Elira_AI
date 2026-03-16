from .vector_store import VectorStore

class SemanticSearch:
    def __init__(self):
        self.store = VectorStore()

    def add_document(self, text, embedding):
        self.store.add(embedding, text)

    def search(self, embedding, top_k=5):
        return self.store.search(embedding, top_k)