"""Gregorian calendar facts without model inference, locale, shell or network."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
MAX_DATES = 31


def date_info(expression: str) -> dict[str, Any]:
    """Accept one date or up to 31 comma-separated ISO dates; no date arithmetic."""
    values = expression.split(",")
    if not 1 <= len(values) <= MAX_DATES:
        raise ValueError(f"date_info: нужно от 1 до {MAX_DATES} дат YYYY-MM-DD через запятую")
    result = []
    for raw in values:
        value = raw.strip()
        if not _ISO_DATE.fullmatch(value):
            raise ValueError("date_info: ожидается дата YYYY-MM-DD; несколько дат разделяй запятой")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"date_info: недопустимая календарная дата {value}") from exc
        result.append({"date": parsed.isoformat(), "weekday_iso": parsed.isoweekday(),
                       "weekday_ru": WEEKDAYS_RU[parsed.weekday()]})
    return {"operation": "date_info", "calendar": "gregorian", "dates": result}


def runtime_date_context(now: datetime | None = None) -> str:
    """Host-local runtime date, weekday and UTC offset from one clock reading."""
    current = datetime.now().astimezone() if now is None else now
    if current.utcoffset() is None:
        raise ValueError("runtime date requires a timezone-aware datetime")
    return f"{current:%Y-%m-%d} ({WEEKDAYS_RU[current.weekday()]}) UTC{current:%z}"
