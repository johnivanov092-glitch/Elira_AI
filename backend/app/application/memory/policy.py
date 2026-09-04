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
HARNESS_POLICY_CATEGORY = "agent_policy"

_AUTHORITATIVE_CATEGORIES = frozenset({"fact", "user_fact", "preference"})
_TRUSTED_SOURCES = frozenset({
    "manual",
    "user",
    "user_command",
    "user_correction",
})

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

_HARNESS_POLICY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.UNICODE | re.DOTALL)
    for pattern in (
        r"\bперед\s+ответом\b.{0,300}\b(?:memory_search|долговременн\w*\s+памят\w*)\b",
        r"\b(?:elira|агент)\b.{0,160}\b(?:обязан\w*|должен\w*)\b.{0,240}\b(?:memory_search|tool\w*|памят\w*)\b",
        r"\bbefore\s+(?:the\s+)?answer\b.{0,300}\b(?:memory|tool|search|prompt)\w*\b",
        r"\b(?:игнорир\w*|забудь\w*|отмени\w*)\b.{0,160}\b(?:инструкц\w*|правил\w*|промпт\w*)\b",
        r"\b(?:раскрой\w*|выдай\w*|покажи\w*)\b.{0,100}\b(?:секрет\w*|парол\w*|токен\w*)\b",
        r"\b(?:ignore|disregard|override)\b.{0,160}\b(?:instructions?|rules?|prompts?)\b",
        r"\b(?:reveal|expose)\b.{0,100}\b(?:secrets?|passwords?|tokens?)\b",
        r"<\|(?:system|assistant|im_start|im_end)\|>|(?:^|\n)\s*(?:#{1,6}\s*)?(?:system|developer)\s*:",
    )
)
_DIRECTIVE_PATTERN = re.compile(
    r"\b(?:всегда|никогда|перед\s+ответом|обязан\w*|должен\w*|"
    r"используй\w*|вызов\w*|выполн\w*|сначала|always|never|must|should|"
    r"use|call|run|before\s+answering)\b",
    re.IGNORECASE | re.UNICODE,
)
_RUNTIME_TARGET_PATTERN = re.compile(
    r"\b(?:memory(?:_search)?|web_search|tool\w*|инструмент\w*|permission\w*|"
    r"разрешени\w*|system\s+prompt|системн\w*\s+промпт\w*|памят\w*)\b",
    re.IGNORECASE | re.UNICODE,
)


def is_volatile_fact(text: object) -> bool:
    """Whether a fact describes state that can drift without user action."""
    value = str(text or "").strip()
    return bool(value and any(pattern.search(value) for pattern in _VOLATILE_PATTERNS))


def normalize_fact_category(text: object, category: object) -> str:
    """Map operational state to a non-authoritative category at write time."""
    normalized = str(category or "fact").strip() or "fact"
    if is_harness_policy(text):
        return HARNESS_POLICY_CATEGORY
    if normalized in _AUTHORITATIVE_CATEGORIES and is_volatile_fact(text):
        return VOLATILE_CATEGORY
    return normalized


def is_harness_policy(text: object) -> bool:
    """Whether a row is an agent-behaviour rule, not user/domain knowledge."""
    value = str(text or "").strip()
    if not value:
        return False
    if any(pattern.search(value) for pattern in _HARNESS_POLICY_PATTERNS):
        return True
    return bool(_DIRECTIVE_PATTERN.search(value) and _RUNTIME_TARGET_PATTERN.search(value))


def is_authoritative_fact(
    item: dict[str, Any],
    *,
    allow_legacy_runtime_control: bool = False,
) -> bool:
    """Whether an item may be injected into a prompt as durable source truth."""
    source = str(item.get("source") or "").strip()
    category = str(item.get("category") or "").strip()
    source_is_trusted = source in _TRUSTED_SOURCES or (
        allow_legacy_runtime_control and source == "runtime_control"
    )
    if not source_is_trusted:
        return False
    if category not in _AUTHORITATIVE_CATEGORIES:
        return False
    text = item.get("text")
    return not is_volatile_fact(text) and not is_harness_policy(text)
