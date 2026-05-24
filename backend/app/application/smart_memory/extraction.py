from __future__ import annotations

import re
from typing import Any

from app.application.smart_memory.store import add_memory, normalize_profile


REMEMBER_PATTERNS = [
    r"запомни[,:]?\s+(?:что\s+)?(.+)",
    r"сохрани[,:]?\s+(?:что\s+)?(.+)",
    r"remember[,:]?\s+(?:that\s+)?(.+)",
    r"save[,:]?\s+(?:that\s+)?(.+)",
    r"мой (?:сервер|ip|адрес|номер|пароль|ключ|api).+",
    r"я (?:живу|работаю|учусь|люблю|предпочитаю|использую).+",
    r"меня зовут\s+.+",
    r"my name is\s+.+",
]

FACT_PATTERNS = [
    (r"(?:мой|моя|мое|мои)\s+((?:сервер|ip|адрес|api|ключ|токен|email|почта|имя|название)\s*(?:—|:|-|это)?\s*.+)", "preference"),
    (r"(?:я\s+(?:живу|работаю|учусь|люблю|предпочитаю|использую))\s+(.+)", "preference"),
    (r"(?:меня зовут|my name is)\s+(.+)", "fact"),
    (r"(?:ip|сервер|server)\s*(?:—|:|-|=)\s*(\S+)", "fact"),
    (r"(?:api.?key|token|ключ)\s*(?:—|:|-|=)\s*(\S+)", "fact"),
]


def is_memory_command(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return bool(re.match(r"^(запомни|сохрани|remember|save)\b", normalized))


def classify_memory_text(text: str) -> str:
    normalized = (text or "").strip().lower()
    if not normalized:
        return "fact"

    if re.search(r"\b(для |всегда |никогда |используй|не используй|отвечай|пиши|говори|remember to|always|never)\b", normalized):
        return "instruction"
    if re.search(r"\b(люблю|нравит|предпочита|хочу|нужно|важно|удобно|коротк|подробн|минимализм|новости)\b", normalized):
        return "preference"
    return "fact"


def extract_and_save(
    user_message: str,
    assistant_message: str = "",
    profile_name: str | None = None,
) -> list[dict[str, Any]]:
    del assistant_message

    normalized_profile = normalize_profile(profile_name)
    normalized_text = (user_message or "").strip()
    if not normalized_text:
        return []

    saved: list[dict[str, Any]] = []

    for pattern in REMEMBER_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if not match:
            continue
        fact = match.group(1) if match.lastindex else match.group(0)
        fact = fact.strip().rstrip(".")
        if len(fact) > 5:
            result = add_memory(
                fact,
                category=classify_memory_text(fact),
                source="user_command",
                importance=8,
                profile_name=normalized_profile,
            )
            if result.get("ok"):
                saved.append(result)
        return saved

    for pattern, category in FACT_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if not match:
            continue
        fact = match.group(1) if match.lastindex else match.group(0)
        fact = fact.strip().rstrip(".")
        if len(fact) > 3:
            result = add_memory(
                fact,
                category=category,
                source="auto_extract",
                importance=6,
                profile_name=normalized_profile,
            )
            if result.get("ok"):
                saved.append(result)

    return saved
