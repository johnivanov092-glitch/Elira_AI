from __future__ import annotations
from typing import List
import requests

class OllamaEmbeddingService:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def encode(self, texts: List[str]) -> List[List[float]]:
        vectors = []
        for text in texts:
            r = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=60
            )
            r.raise_for_status()
            data = r.json()
            vectors.append(data.get("embedding", []))
        return vectors
