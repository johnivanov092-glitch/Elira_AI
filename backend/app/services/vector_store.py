import numpy as np

class VectorStore:
    def __init__(self):
        self.vectors = []
        self.texts = []

    def add(self, vector, text):
        self.vectors.append(np.array(vector))
        self.texts.append(text)

    def search(self, query_vector, top_k=5):
        if not self.vectors:
            return []
        sims = []
        q = np.array(query_vector)
        for v in self.vectors:
            sims.append(float(np.dot(q, v) / (np.linalg.norm(q) * np.linalg.norm(v) + 1e-8)))
        idx = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)[:top_k]
        return [self.texts[i] for i in idx]