from __future__ import annotations

import hashlib
import re
from typing import Callable


def normalize_query(text: str) -> str:
    normalized = (text or "").lower().strip()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def query_hash(normalized: str, model: str, profile: str) -> str:
    key = f"{normalized}|{model}|{profile}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def should_cache_query(
    *,
    query: str,
    route: str,
    detect_temporal_intent_func: Callable[[str], dict],
) -> bool:
    normalized = (query or "").lower()

    if any(
        word in normalized
        for word in ("запомни", "забудь", "сохрани в память", "удали из памяти")
    ):
        return False

    if route in ("project", "code"):
        return False

    temporal = detect_temporal_intent_func(query)
    if temporal.get("requires_web") or temporal.get("freshness_sensitive"):
        return False

    if any(
        word in normalized
        for word in ("сейчас", "сегодня", "прямо сейчас", "только что", "right now")
    ):
        return False

    return True

