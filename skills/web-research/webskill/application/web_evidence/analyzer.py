"""W1 retrieval foundation: RU-aware tokenizer + BM25 (no external deps).

BM25 is the OBLIGATORY base (contract §5): retrieval must work with embeddings
off. The analyzer is VERSIONED — `ANALYZER_VERSION` is stamped on every indexed
document; a mismatch forces re-index. Exact-phrase / quote verification does NOT
go through the analyzer (it matches canonical_text directly), so stemming is a
ranking improvement only, never a correctness dependency.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# Bump when tokenization/stemming changes so stored indexes re-build (contract §5).
ANALYZER_VERSION = "ru1"

_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)

# Light RU suffix stripping — deliberately conservative (ranking aid, not lemmas).
# Ordered longest-first so the greediest suffix wins.
_RU_SUFFIXES = tuple(sorted((
    "ами", "ями", "ого", "ему", "ому", "ыми", "ими", "ает", "уют", "ють",
    "ах", "ях", "ов", "ев", "ей", "ий", "ый", "ой", "ая", "яя", "ое", "ее",
    "ые", "ие", "ам", "ям", "ом", "ем", "ум", "ю", "у", "ы", "и", "е", "а",
    "я", "о", "ь",
), key=len, reverse=True))

_MIN_STEM = 4  # never strip below this many chars — keeps short words intact


def normalize(text: str) -> str:
    """Canonical case/ё folding used everywhere a comparison must be stable."""
    return (text or "").lower().replace("ё", "е")


def _stem(token: str) -> str:
    if len(token) <= _MIN_STEM or token.isdigit():
        return token
    for suf in _RU_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= _MIN_STEM:
            return token[: -len(suf)]
    return token


def tokenize(text: str, *, stem: bool = True) -> list[str]:
    """Tokens for BM25. `stem=False` gives raw normalized tokens (used by
    exact-phrase paths that must not depend on the stemmer)."""
    toks = _TOKEN_RE.findall(normalize(text))
    return [_stem(t) for t in toks] if stem else toks


class Bm25Index:
    """In-memory BM25 over a set of {id, text} docs. Rebuilt from the store on
    demand and cached by (run_id, analyzer_ver) upstream. Pure/stdlib."""

    __slots__ = ("k1", "b", "_docs", "_df", "_len", "_avg", "_n")

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._docs: dict[str, Counter] = {}
        self._df: Counter = Counter()
        self._len: dict[str, int] = {}
        self._avg = 0.0
        self._n = 0

    def add(self, doc_id: str, text: str) -> None:
        toks = tokenize(text)
        tf = Counter(toks)
        self._docs[doc_id] = tf
        self._len[doc_id] = len(toks)
        for term in tf:
            self._df[term] += 1
        self._n = len(self._docs)
        self._avg = (sum(self._len.values()) / self._n) if self._n else 0.0

    def search(self, query: str, top_k: int = 8) -> list[tuple[str, float]]:
        if not self._n:
            return []
        q_terms = set(tokenize(query))
        scores: dict[str, float] = {}
        for term in q_terms:
            df = self._df.get(term, 0)
            if not df:
                continue
            idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5))
            for doc_id, tf in self._docs.items():
                f = tf.get(term, 0)
                if not f:
                    continue
                dl = self._len[doc_id] or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self._avg or 1))
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * (f * (self.k1 + 1) / denom)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]
