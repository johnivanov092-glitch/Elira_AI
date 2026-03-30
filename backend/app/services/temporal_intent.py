from __future__ import annotations

import re
from datetime import datetime
from typing import Any


YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

RELATIVE_TIME_TERMS = (
    "сегодня",
    "сейчас",
    "прямо сейчас",
    "на сегодня",
    "на данный момент",
    "в данный момент",
    "в этом году",
    "в следующем году",
    "в этом месяце",
    "последнее",
    "последние",
    "актуально",
    "актуальный",
    "currently",
    "current",
    "today",
    "right now",
    "at the moment",
    "this year",
    "next year",
    "latest",
    "recent",
)

CURRENT_WORLD_TERMS = (
    "новости",
    "новость",
    "news",
    "погода",
    "weather",
    "курс",
    "price",
    "цена",
    "стоимость",
    "rate",
    "котировка",
    "рынок",
    "market",
    "статус",
    "status",
    "релиз",
    "release",
    "запуск",
    "launch",
    "произошло",
    "случилось",
    "что нового",
    "кто сейчас",
    "кто теперь",
    "что происходит",
    "что сейчас",
    "что с",
    "обновление",
    "update",
    "тренд",
    "trend",
)

ANALYTIC_TERMS = (
    "аналитик",
    "аналитика",
    "analysis",
    "trend",
    "динамик",
    "сравни",
    "comparison",
    "compare",
    "прогноз",
    "forecast",
    "разбор",
    "оценка",
    "перспектив",
)

HISTORICAL_TERMS = (
    "история",
    "исторически",
    "historical",
    "в прошлом",
    "тогда",
    "на тот момент",
    "кто был",
    "как было",
    "в то время",
    "раньше",
)

HISTORICAL_PATTERNS = (
    re.compile(r"\bчто\s+произошло\s+в\s+(?:19|20)\d{2}\s+году\b", re.IGNORECASE),
    re.compile(r"\bкто\s+был\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+happened\s+in\s+(?:19|20)\d{2}\b", re.IGNORECASE),
    re.compile(r"\bwho\s+was\b", re.IGNORECASE),
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _collect_years(text: str) -> list[int]:
    return sorted({int(match.group(1)) for match in YEAR_RE.finditer(text)})


def detect_temporal_intent(query: str, now: datetime | None = None) -> dict[str, Any]:
    text = (query or "").strip()
    normalized = text.lower()
    now = now or datetime.now()
    current_year = now.year
    years = _collect_years(normalized)

    has_relative_time = _contains_any(normalized, RELATIVE_TIME_TERMS)
    has_current_world_terms = _contains_any(normalized, CURRENT_WORLD_TERMS)
    has_analytic_terms = _contains_any(normalized, ANALYTIC_TERMS)
    has_historical_terms = _contains_any(normalized, HISTORICAL_TERMS) or any(
        pattern.search(normalized) for pattern in HISTORICAL_PATTERNS
    )

    has_year = bool(years)
    has_future_or_current_year = any(year >= current_year for year in years)
    has_recent_year = any(year >= current_year - 1 for year in years)

    signals: list[str] = []
    if has_year:
        signals.append("explicit_year")
    if has_relative_time:
        signals.append("relative_time")
    if has_current_world_terms:
        signals.append("current_world")
    if has_analytic_terms:
        signals.append("analytic")
    if has_historical_terms:
        signals.append("historical")

    if has_relative_time or has_future_or_current_year:
        mode = "hard"
    elif has_current_world_terms and (has_analytic_terms or has_recent_year or not has_year):
        mode = "hard"
    elif has_year and has_historical_terms:
        mode = "stable_historical"
    elif has_year:
        mode = "soft"
    elif has_current_world_terms:
        mode = "hard"
    else:
        mode = "none"

    requires_web = mode in {"hard", "soft"}
    freshness_sensitive = mode == "hard"
    reasoning_depth = "none"
    if requires_web:
        reasoning_depth = "deep" if (has_analytic_terms or freshness_sensitive) else "standard"

    return {
        "mode": mode,
        "requires_web": requires_web,
        "freshness_sensitive": freshness_sensitive,
        "analytic": has_analytic_terms,
        "years": years,
        "signals": signals,
        "reasoning_depth": reasoning_depth,
        "current_year": current_year,
        "stable_historical": mode == "stable_historical",
    }
