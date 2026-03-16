import time

class MemoryWeights:
    def score(self, memory):
        age = time.time() - memory.get("timestamp", time.time())
        importance = memory.get("importance", 1.0)
        return importance / (1 + age / 86400)