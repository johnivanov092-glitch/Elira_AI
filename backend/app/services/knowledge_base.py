class KnowledgeBase:
    def __init__(self):
        self.docs = []

    def add(self, text):
        self.docs.append(text)

    def list_docs(self):
        return self.docs