class RAGService:
    def __init__(self, semantic_search):
        self.semantic_search = semantic_search

    def build_context(self, query_embedding):
        docs = self.semantic_search.search(query_embedding, top_k=5)
        context = "\n".join(docs)
        return context