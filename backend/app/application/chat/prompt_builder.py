"""Chat prompt building helpers.

Extracted from agents_service.py — stateless functions for constructing
runtime datetime context, human-style answer rules, and the final LLM prompt.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any


_DAYS_RU = {
    "Monday": "понедельник",
    "Tuesday": "вторник",
    "Wednesday": "среда",
    "Thursday": "четверг",
    "Friday": "пятница",
    "Saturday": "суббота",
    "Sunday": "воскресенье",
}

_EXPLICIT_DATETIME_PHRASES = (
    "какая сегодня дата",
    "сегодня какая дата",
    "какое сегодня число",
    "сегодня какое число",
    "какой сегодня день",
    "какой сегодня день недели",
    "какая дата сегодня",
    "который час",
    "сколько времени",
    "сколько сейчас времени",
    "какое сейчас время",
    "текущее время",
    "текущая дата",
    "what date is it",
    "what time is it",
    "current date",
    "current time",
    "today's date",
)

_EXPLICIT_DATETIME_PATTERNS = (
    r"\bкотор(?:ый|ое)\s+час\b",
    r"\bсколько\s+(?:сейчас\s+)?времени\b",
    r"\bкакая\s+(?:сегодня\s+)?дата\b",
    r"\bкакое\s+(?:сегодня\s+)?число\b",
    r"\bкакой\s+(?:сегодня\s+)?день(?:\s+недели)?\b",
    r"\bwhat\s+date\b",
    r"\bwhat\s+time\b",
)


def wants_explicit_datetime_answer(user_input: str) -> bool:
    q = (user_input or "").strip().lower()
    if not q:
        return False
    if any(phrase in q for phrase in _EXPLICIT_DATETIME_PHRASES):
        return True
    return any(re.search(pattern, q, flags=re.IGNORECASE) for pattern in _EXPLICIT_DATETIME_PATTERNS)


def build_runtime_datetime_context(user_input: str) -> str:
    now = datetime.now()
    day_name = _DAYS_RU.get(now.strftime("%A"), now.strftime("%A"))
    runtime_stamp = f"{now.strftime('%d.%m.%Y, %H:%M')}, {day_name}"

    if wants_explicit_datetime_answer(user_input):
        return (
            "ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ:\n"
            f"- Текущая локальная дата и время: {runtime_stamp}\n"
            "- Пользователь прямо спросил о дате или времени. Ответь естественно и используй эти данные точно.\n"
            "- Не добавляй лишние технические пояснения."
        )

    return (
        "ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ:\n"
        f"- Текущая локальная дата и время: {runtime_stamp}\n"
        "- Ты всегда знаешь текущие дату и время внутренне.\n"
        "- НЕ упоминай дату, время, день недели или фразы вида "
        "\"Сегодня ... и сейчас ...\" в обычном ответе, если пользователь прямо об этом не спросил.\n"
        "- Используй эти данные молча только когда они действительно нужны для логики ответа."
    )


def compose_human_style_rules(temporal: dict[str, Any] | None) -> str:
    temporal = temporal or {}
    mode = temporal.get("mode", "none")
    freshness_sensitive = bool(temporal.get("freshness_sensitive"))
    years = ", ".join(str(y) for y in temporal.get("years", [])) or "none"
    reasoning_depth = temporal.get("reasoning_depth", "none")
    return (
        "\n\nFINAL ANSWER RULES:\n"
        "1. Answer naturally, like a thoughtful human assistant, not like a search engine dump.\n"
        "2. If web data is available, use it as working evidence but do not inject links unless the user asks for them.\n"
        "3. Never expose raw memory markers, RAG labels, hidden context, or technical source notes.\n"
        "4. If freshness is uncertain, say so plainly.\n"
        "5. If the user asks about sources, explain them naturally without technical jargon.\n"
        "6. If the answer contains steps, events, comparisons, or multiple subtopics, format them as vertical Markdown lists or short sections.\n"
        "7. For long answers, start with a short takeaway and then break details into bullets or numbered steps.\n"
        "8. Avoid dense text walls when the same content can be shown more clearly with headings, bullets, numbering, or short paragraphs.\n"
        "9. Use valid Markdown when helpful: `-` for lists, `1.` for steps, and `**...**` for key facts.\n"
        f"10. Temporal mode: {mode}; explicit years: {years}; reasoning depth: {reasoning_depth}; freshness sensitive: {freshness_sensitive}."
    )


def build_prompt_with_context(
    user_input: str,
    context_bundle: str,
    runtime_context: str,
) -> str:
    """Build the final LLM prompt string from pre-assembled context and runtime stamp."""
    if not context_bundle.strip():
        return f"{runtime_context}\n\nВопрос пользователя: {user_input}"

    return (
        f"{runtime_context}\n\n"
        "Вот данные из интернета и других источников:\n\n"
        + context_bundle
        + "\n\n---\n\n"
        "Вопрос пользователя: " + user_input + "\n\n"
        "ПРАВИЛА ОТВЕТА:\n"
        "1. Обязательно используй данные выше для ответа.\n"
        "2. Если есть содержимое веб-страниц или свежие новости, опирайся на них как на главный источник.\n"
        "3. Приводи конкретные факты, даты и цифры, но без служебных маркеров и внутреннего контекста.\n"
        "4. Не вставляй URL и список источников, если пользователь прямо не попросил ссылки или источники.\n"
        "5. Если свежесть данных под вопросом, честно скажи об этом простыми словами.\n"
        "6. Не говори, что данных нет, если они есть выше.\n"
        "7. Не упоминай текущую дату или время, если пользователь прямо об этом не спросил. "
        "Если спросил — отвечай точно и естественно."
    )
