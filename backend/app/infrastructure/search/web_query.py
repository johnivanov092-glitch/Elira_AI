from __future__ import annotations

import re


_QUERY_NOISE = [
    r"^(РґР°Р№|РґР°Р№ РјРЅРµ|РїРѕРєР°Р¶Рё|СЃРєР°Р¶Рё|СЂР°СЃСЃРєР°Р¶Рё|РЅР°Р№РґРё|РїРѕРєР°Р¶Рё РјРЅРµ)\s+",
    r"\s+(РїРѕР¶Р°Р»СѓР№СЃС‚Р°|РїР»РёР·|please)$",
]


def clean_query(query: str) -> str:
    """Clean and improve a raw user query for web search engines."""
    from datetime import datetime

    from app.application.temporal_intent.runtime import detect_temporal_intent

    q = query.strip()
    for pattern in _QUERY_NOISE:
        q = re.sub(pattern, "", q, flags=re.IGNORECASE).strip()

    ql = q.lower()

    is_news = any(
        word in ql
        for word in ["РЅРѕРІРѕСЃС‚Рё", "РЅРѕРІРѕСЃС‚СЊ", "СЃРѕР±С‹С‚РёСЏ", "РїСЂРѕРёР·РѕС€Р»Рѕ", "СЃР»СѓС‡РёР»РѕСЃСЊ", "РїСЂРѕРёСЃС€РµСЃС‚РІ"]
    )
    is_price = any(word in ql for word in ["РєСѓСЂСЃ", "С†РµРЅР°", "СЃС‚РѕРёРјРѕСЃС‚СЊ"])
    is_weather = "РїРѕРіРѕРґР°" in ql

    temporal = detect_temporal_intent(q)
    if (is_news or is_price or is_weather) and not temporal.get("years"):
        q += " " + str(datetime.now().year)

    date_match = re.search(r"(\d{1,2})\.(\d{2})(?:\.\d{2,4})?", q)
    if date_match and is_news:
        day = date_match.group(1)
        month_num = int(date_match.group(2))
        months = {
            1: "СЏРЅРІР°СЂСЏ",
            2: "С„РµРІСЂР°Р»СЏ",
            3: "РјР°СЂС‚Р°",
            4: "Р°РїСЂРµР»СЏ",
            5: "РјР°СЏ",
            6: "РёСЋРЅСЏ",
            7: "РёСЋР»СЏ",
            8: "Р°РІРіСѓСЃС‚Р°",
            9: "СЃРµРЅС‚СЏР±СЂСЏ",
            10: "РѕРєС‚СЏР±СЂСЏ",
            11: "РЅРѕСЏР±СЂСЏ",
            12: "РґРµРєР°Р±СЂСЏ",
        }
        month_name = months.get(month_num, "")
        if month_name:
            q = re.sub(r"\d{1,2}\.\d{2}(?:\.\d{2,4})?", f"{day} {month_name}", q)

    if is_news and not any(
        word in ql for word in ["СЂРѕСЃСЃРёСЏ", "СѓРєСЂР°РёРЅР°", "СЃС€Р°", "РјРёСЂ", "РєР°Р·Р°С…СЃС‚Р°РЅ", "РєР·"]
    ):
        kz_cities = [
            "Р°Р»РјР°С‚С‹",
            "Р°СЃС‚Р°РЅР°",
            "С€С‹РјРєРµРЅС‚",
            "РєР°СЂР°РіР°РЅРґ",
            "Р°РєС‚Р°Сѓ",
            "Р°С‚С‹СЂР°Сѓ",
            "РїР°РІР»РѕРґР°СЂ",
            "СЃРµРјРµР№",
            "С‚Р°СЂР°Р·",
        ]
        if any(city in ql for city in kz_cities):
            q += " РљР°Р·Р°С…СЃС‚Р°РЅ"

    return q or query


def is_strict_web_only_query(user_input: str) -> bool:
    q = (user_input or "").lower()
    hard_terms = (
        "РЅРѕРІРѕСЃС‚",
        "news",
        "РєСѓСЂСЃ",
        "РґРѕР»Р»Р°СЂ",
        "РµРІСЂРѕ",
        "СЂСѓР±Р»",
        "С‚РµРЅРіРµ",
        "usd",
        "eur",
        "kzt",
        "РїРѕРіРѕРґ",
        "weather",
        "СЃРµРіРѕРґРЅСЏ",
        "today",
        "СЃРµР№С‡Р°СЃ",
        "current",
        "Р°РєС‚СѓР°Р»СЊРЅ",
        "latest",
        "РїРѕСЃР»РµРґРЅРёРµ",
    )
    return any(term in q for term in hard_terms)
