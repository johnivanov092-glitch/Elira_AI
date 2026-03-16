from __future__ import annotations
from typing import List

class EmbeddingService:
    '''
    Placeholder embedding service.
    Replace encode() with Ollama embeddings or sentence-transformers.
    '''
    def __init__(self, dim: int = 384):
        self.dim = dim

    def encode(self, texts: List[str]) -> List[List[float]]:
        vectors = []
        for text in texts:
            seed = sum(ord(ch) for ch in text) % 997
            vec = [0.0] * self.dim
            for i, ch in enumerate(text[: self.dim * 2]):
                vec[i % self.dim] += ((ord(ch) + seed) % 17) / 17.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors
