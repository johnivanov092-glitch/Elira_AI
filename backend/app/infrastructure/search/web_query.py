from __future__ import annotations

import re


_QUERY_NOISE = [
    r"^(дай|дай мне|покажи|скажи|расскажи|найди|покажи мне)\s+",
    r"\s+(пожалуйста|плиз|please)$",
]


def clean_query(query: str) -> str:
    """Clean and improve a raw user query for web search engines."""
    from datetime import datetime

    from app.application.chat.temporal_intent import detect_temporal_intent

    q = query.strip()
    for pattern in _QUERY_NOISE:
        q = re.sub(pattern, "", q, flags=re.IGNORECASE).strip()

    ql = q.lower()

    is_news = any(
        word in ql
        for word in ["новости", "новость", "события", "произошло", "случилось", "происшеств"]
    )
    is_price = any(word in ql for word in ["курс", "цена", "стоимость"])
    is_weather = "погода" in ql

    temporal = detect_temporal_intent(q)
    if (is_news or is_price or is_weather) and not temporal.get("years"):
        q += " " + str(datetime.now().year)

    date_match = re.search(r"(\d{1,2})\.(\d{2})(?:\.\d{2,4})?", q)
    if date_match and is_news:
        day = date_match.group(1)
        month_num = int(date_match.group(2))
        months = {
            1: "января",
            2: "февраля",
            3: "марта",
            4: "апреля",
            5: "мая",
            6: "июня",
            7: "июля",
            8: "августа",
            9: "сентября",
            10: "октября",
            11: "ноября",
            12: "декабря",
        }
        month_name = months.get(month_num, "")
        if month_name:
            q = re.sub(r"\d{1,2}\.\d{2}(?:\.\d{2,4})?", f"{day} {month_name}", q)

    if is_news and not any(
        word in ql for word in ["россия", "украина", "сша", "мир", "казахстан", "кз"]
    ):
        kz_cities = [
            "алматы",
            "астана",
            "шымкент",
            "караганд",
            "актау",
            "атырау",
            "павлодар",
            "семей",
            "тараз",
        ]
        if any(city in ql for city in kz_cities):
            q += " Казахстан"

    return q or query


def is_strict_web_only_query(user_input: str) -> bool:
    q = (user_input or "").lower()
    hard_terms = (
        "новост",
        "news",
        "курс",
        "доллар",
        "евро",
        "рубл",
        "тенге",
        "usd",
        "eur",
        "kzt",
        "погод",
        "weather",
        "сегодня",
        "today",
        "сейчас",
        "current",
        "актуальн",
        "latest",
        "последние",
    )
    return any(term in q for term in hard_terms)
