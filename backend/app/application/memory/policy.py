"""Server-owned quality policy for durable user facts.

Curated memory is authoritative only for durable identity, preferences and
instructions. Operational state (active model, uptime, temperatures, free
space, live service status) must be re-checked against the live target before
use, even when a user explicitly asked Elira to remember it.
"""
from __future__ import annotations

import re
from typing import Any


VOLATILE_CATEGORY = "volatile_fact"

_AUTHORITATIVE_CATEGORIES = frozenset({"fact", "user_fact", "preference"})
_TRUSTED_SOURCES = frozenset({"manual", "user", "user_command", "user_correction"})

_VOLATILE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.UNICODE)
    for pattern in (
        r"\b(?:сейчас|на\s+данный\s+момент|currently|right\s+now)\b",
        r"\b(?:текущ\w*|активн\w*)\s+(?:llm[- ]?)?модел\w*\b",
        r"\bactive\s+(?:llm\s+)?model\b",
        r"\b(?:uptime|аптайм)\b",
        r"\b(?:температур\w*|temperature)\b",
        r"\b(?:vram|видеопамят\w*)\b.{0,80}\b(?:used|free|занят\w*|свободн\w*|\d+\s*%)\b",
        r"\b(?:used|free|занят\w*|свободн\w*)\b.{0,40}\b\d+(?:[.,]\d+)?\s*(?:gb|гб|tb|тб|%)\b",
        r"\b(?:последн\w*\s+перезагруз\w*|last\s+reboot)\b",
        r"\b(?:не\s+)?запущен\w*\b|\b(?:running|stopped)\b",
        r"\b(?:порт\w*|ports?)\b.{0,80}\b(?:слуша\w*|listen\w*|работа\w*|online|up)\b",
        r"\b(?:сервис\w*|services?)\b.{0,80}\b(?:работа\w*|online|healthy|up|down)\b",
        r"\bактуальн\w*\s+на\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|\d{4})",
    )
)


def is_volatile_fact(text: object) -> bool:
    """Whether a fact describes state that can drift without user action."""
    value = str(text or "").strip()
    return bool(value and any(pattern.search(value) for pattern in _VOLATILE_PATTERNS))


def normalize_fact_category(text: object, category: object) -> str:
    """Map operational state to a non-authoritative category at write time."""
    normalized = str(category or "fact").strip() or "fact"
    if normalized in _AUTHORITATIVE_CATEGORIES and is_volatile_fact(text):
        return VOLATILE_CATEGORY
    return normalized


def is_authoritative_fact(item: dict[str, Any]) -> bool:
    """Whether an item may be injected into a prompt as durable source truth."""
    source = str(item.get("source") or "").strip()
    category = str(item.get("category") or "").strip()
    if source not in _TRUSTED_SOURCES:
        return False
    if category not in _AUTHORITATIVE_CATEGORIES:
        return False
    return not is_volatile_fact(item.get("text"))
