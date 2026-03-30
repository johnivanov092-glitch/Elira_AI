from __future__ import annotations

import re
from typing import Any


MAX_SUBQUERIES = 6
PASS_SIZE = 3

FINANCE_TERMS = (
    "курс",
    "доллар",
    "usd",
    "евро",
    "eur",
    "тенге",
    "kzt",
    "валют",
    "обменник",
    "forex",
)

NEWS_TERMS = (
    "новости",
    "новость",
    "происшеств",
    "события",
    "криминал",
    "дтп",
    "авар",
    "crime",
    "incident",
)

STATUS_CURRENT_TERMS = (
    "статус",
    "кто сейчас",
    "что сейчас",
    "что происходит",
    "сейчас",
    "сегодня",
    "на данный момент",
    "рейс",
    "рейсы",
    "погода",
    "weather",
    "live",
)

PRICE_RATE_TERMS = (
    "цена",
    "цены",
    "стоимость",
    "сколько стоит",
    "rate",
    "rates",
    "tariff",
    "тариф",
    "бензин",
    "нефть",
    "золото",
)

CURRENT_HINT_TERMS = (
    "сегодня",
    "сейчас",
    "последние",
    "актуаль",
    "на сегодня",
    "на данный момент",
    "за ",
    "в этом году",
    "в следующем году",
)

CITY_GEO_MAP = {
    "алматы": "Алматы",
    "астана": "Астана",
    "шымкент": "Шымкент",
    "караганда": "Караганда",
    "актобе": "Актобе",
    "атырау": "Атырау",
    "семей": "Семей",
    "тараз": "Тараз",
    "актау": "Актау",
    "павлодар": "Павлодар",
}

KZ_LOCAL_NEWS_DOMAINS = (
    "nur.kz",
    "tengrinews.kz",
    "zakon.kz",
    "sputnik.kz",
    "informburo.kz",
    "kazinform.kz",
)

FINANCE_HIGH_CONFIDENCE_DOMAINS = (
    "nationalbank.kz",
    "prodengi.kz",
    "bcc.kz",
    "halykbank.kz",
    "investing.com",
    "wise.com",
)

INTRO_PREFIX_RE = re.compile(
    r"^(?:хочу узнать|хотел бы узнать|мне нужно узнать|подскажи|расскажи|скажи|интересует|нужна информация о)\s+",
    re.IGNORECASE,
)
SPLIT_CONNECTORS_RE = re.compile(r"\s+(?:и|а также|плюс|along with|and)\s+", re.IGNORECASE)
CLAUSE_SPLIT_RE = re.compile(r"\s*(?:;|\n|(?<!\d),(?!\d))\s*")
DAY_WINDOW_RE = re.compile(r"\bза\s+\d+\s+(?:дн(?:я|ей)?|сут(?:ок|ки)?)\b", re.IGNORECASE)
HOUR_WINDOW_RE = re.compile(r"\bза\s+\d+\s+час(?:а|ов)?\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b20\d{2}\b")

INTENT_LABELS = {
    "finance": "Курс валют",
    "geo_news": "Локальные новости",
    "general_news": "Новости",
    "status_current": "Текущий статус",
    "price_rate": "Цены",
    "historical": "Историческая справка",
    "general_web": "Основной поиск",
}


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _strip_intro(query: str) -> str:
    return INTRO_PREFIX_RE.sub("", (query or "").strip()).strip()


def _extract_geo(query: str) -> dict[str, str]:
    normalized = (query or "").lower()
    country = "Казахстан" if any(token in normalized for token in ("казахстан", " кз", "(кз)", "kz")) else ""
    city_key = next((key for key in CITY_GEO_MAP if key in normalized), "")
    city = CITY_GEO_MAP.get(city_key, "")

    if city and not country:
        country = "Казахстан"

    if city and country:
        label = f"{city}, {country}"
        scope = city.lower()
    elif city:
        label = city
        scope = city.lower()
    elif country:
        label = country
        scope = "kazakhstan"
    else:
        label = ""
        scope = ""

    return {"city": city, "country": country, "label": label, "scope": scope}


def _extract_time_window(query: str) -> str:
    normalized = (query or "").lower()
    for pattern in (DAY_WINDOW_RE, HOUR_WINDOW_RE):
        match = pattern.search(normalized)
        if match:
            return match.group(0)
    for phrase in ("на сегодня", "сегодня", "сейчас", "за неделю", "за месяц", "последние 24 часа"):
        if phrase in normalized:
            return phrase
    return ""


def _split_candidate_segments(query: str) -> list[str]:
    cleaned = _strip_intro(query)
    candidates: list[str] = []
    for clause in CLAUSE_SPLIT_RE.split(cleaned):
        clause = clause.strip(" .")
        if not clause:
            continue
        pieces = [part.strip(" .") for part in SPLIT_CONNECTORS_RE.split(clause) if part.strip(" .")]
        candidates.extend(pieces or [clause])
    return candidates or [cleaned or query]


def _infer_intent(segment: str, temporal: dict[str, Any], geo: dict[str, str]) -> str:
    normalized = segment.lower()

    if temporal.get("stable_historical") and YEAR_RE.search(normalized):
        return "historical"
    if _contains_any(normalized, FINANCE_TERMS):
        return "finance"
    if _contains_any(normalized, NEWS_TERMS):
        return "geo_news" if geo.get("scope") else "general_news"
    if _contains_any(normalized, PRICE_RATE_TERMS):
        return "price_rate"
    if _contains_any(normalized, STATUS_CURRENT_TERMS):
        return "status_current"
    if temporal.get("stable_historical"):
        return "historical"
    return "general_web"


def _freshness_class(segment: str, temporal: dict[str, Any]) -> str:
    normalized = segment.lower()
    if temporal.get("stable_historical"):
        return "stable"
    if temporal.get("freshness_sensitive") or temporal.get("requires_web") or _contains_any(normalized, CURRENT_HINT_TERMS):
        return "current"
    return "stable"


def _needs_news_feed(intent_kind: str) -> bool:
    return intent_kind in {"geo_news", "general_news"}


def _needs_deep_search(intent_kind: str, temporal: dict[str, Any]) -> bool:
    if temporal.get("reasoning_depth") == "deep":
        return True
    return intent_kind in {"geo_news", "general_news", "status_current"}


def _preferred_domains(intent_kind: str, local_first: bool) -> list[str]:
    if intent_kind == "finance":
        return list(FINANCE_HIGH_CONFIDENCE_DOMAINS)
    if intent_kind == "geo_news" and local_first:
        return list(KZ_LOCAL_NEWS_DOMAINS)
    return []


def _priority(intent_kind: str, freshness_class: str, local_first: bool, temporal: dict[str, Any]) -> int:
    score = 0
    if freshness_class == "current":
        score += 400
    if temporal.get("mode") == "hard":
        score += 100
    if intent_kind == "geo_news":
        score += 300 if local_first else 260
    elif intent_kind == "general_news":
        score += 250
    elif intent_kind == "finance":
        score += 240
    elif intent_kind == "price_rate":
        score += 230
    elif intent_kind == "status_current":
        score += 220
    elif intent_kind == "general_web":
        score += 180
    elif intent_kind == "historical":
        score += 80
    return score


def _finance_query(segment: str, geo: dict[str, str], time_window: str) -> str:
    normalized = segment.lower()
    pieces: list[str] = ["курс"]

    currencies: list[str] = []
    if "доллар" in normalized or "usd" in normalized:
        currencies.append("доллара")
    if "евро" in normalized or "eur" in normalized:
        currencies.append("евро")
    if not currencies:
        currencies.append("валюты")

    pieces.append(" и ".join(currencies))

    if "тенге" in normalized or "kzt" in normalized:
        pieces.append("к тенге")
    if time_window in {"на сегодня", "сегодня", "сейчас"}:
        pieces.append("на сегодня")
    if geo.get("country") == "Казахстан" and "казахстан" not in normalized:
        pieces.append("Казахстан")

    return " ".join(piece for piece in pieces if piece).strip()


def _geo_news_query(segment: str, geo: dict[str, str], time_window: str) -> str:
    normalized = segment.lower()
    pieces: list[str] = ["новости"]

    if any(term in normalized for term in ("происшеств", "криминал", "дтп", "авар")):
        pieces.append("происшествия")

    if geo.get("city"):
        pieces.append(geo["city"])
    elif geo.get("country"):
        pieces.append(geo["country"])

    if time_window:
        pieces.append(time_window)
    elif "сегодня" in normalized or "последние" in normalized:
        pieces.append("сегодня")

    return " ".join(piece for piece in pieces if piece).strip()


def _augment_current_query(segment: str, geo: dict[str, str], time_window: str, default_time: str) -> str:
    normalized = segment.lower()
    pieces = [segment.strip()]
    if geo.get("label") and geo["city"].lower() not in normalized and geo["country"].lower() not in normalized:
        pieces.append(geo["label"])
    if time_window and time_window not in normalized:
        pieces.append(time_window)
    elif default_time and default_time not in normalized and not (
        default_time == "на сегодня" and ("сегодня" in normalized or "на сегодня" in normalized)
    ):
        pieces.append(default_time)
    return " ".join(piece for piece in pieces if piece).strip()


def _build_search_query(segment: str, intent_kind: str, geo: dict[str, str], time_window: str) -> str:
    if intent_kind == "finance":
        return _finance_query(segment, geo, time_window)
    if intent_kind == "geo_news":
        return _geo_news_query(segment, geo, time_window)
    if intent_kind == "general_news":
        return _augment_current_query(segment, geo, time_window, "сегодня")
    if intent_kind == "price_rate":
        return _augment_current_query(segment, geo, time_window, "на сегодня")
    if intent_kind == "status_current":
        return _augment_current_query(segment, geo, time_window, "сейчас")
    return segment.strip()


def _should_merge(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if previous["intent_kind"] != current["intent_kind"]:
        return False
    if previous["intent_kind"] == "finance":
        return True
    if previous["intent_kind"] == "price_rate" and previous["geo_scope"] == current["geo_scope"]:
        return True
    return False


def _build_subquery(segment: str, temporal: dict[str, Any], overall_geo: dict[str, str], time_window: str, original_index: int) -> dict[str, Any]:
    segment_geo = _extract_geo(segment)
    segment_time_window = _extract_time_window(segment)
    geo = {
        "city": segment_geo.get("city") or overall_geo.get("city", ""),
        "country": segment_geo.get("country") or overall_geo.get("country", ""),
        "label": segment_geo.get("label") or overall_geo.get("label", ""),
        "scope": segment_geo.get("scope") or overall_geo.get("scope", ""),
    }
    intent_kind = _infer_intent(segment, temporal, geo)
    freshness_class = _freshness_class(segment, temporal)
    local_first = bool(geo.get("scope")) and intent_kind in {"geo_news", "price_rate", "status_current"}
    if intent_kind == "finance":
        local_first = geo.get("country") == "Казахстан" or "тенге" in segment.lower() or "kzt" in segment.lower()

    query = _build_search_query(segment, intent_kind, geo, segment_time_window)
    preferred_domains = _preferred_domains(intent_kind, local_first)
    return {
        "label": INTENT_LABELS.get(intent_kind, "Основной поиск"),
        "query": query,
        "intent_kind": intent_kind,
        "priority": _priority(intent_kind, freshness_class, local_first, temporal),
        "geo_scope": geo.get("scope", ""),
        "freshness_class": freshness_class,
        "local_first": local_first,
        "needs_news_feed": _needs_news_feed(intent_kind),
        "needs_deep_search": _needs_deep_search(intent_kind, temporal),
        "preferred_domains": preferred_domains,
        "_segment": segment,
        "_original_index": original_index,
    }


def _merge_same_intent_subqueries(candidates: list[dict[str, Any]], temporal: dict[str, Any], overall_geo: dict[str, str], time_window: str) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in candidates:
        if merged and _should_merge(merged[-1], candidate):
            combined_segment = merged[-1]["_segment"] + " и " + candidate["_segment"]
            rebuilt = _build_subquery(
                combined_segment,
                temporal=temporal,
                overall_geo=overall_geo,
                time_window=time_window,
                original_index=merged[-1]["_original_index"],
            )
            merged[-1] = rebuilt
            continue
        merged.append(candidate)
    return merged


def _default_single_query(query: str, temporal: dict[str, Any], geo: dict[str, str], time_window: str) -> dict[str, Any]:
    subquery = _build_subquery(query, temporal=temporal, overall_geo=geo, time_window=time_window, original_index=0)
    subquery.pop("_segment", None)
    subquery.pop("_original_index", None)
    return {
        "is_multi_intent": False,
        "geo_scope": geo.get("scope", ""),
        "freshness_class": subquery["freshness_class"],
        "total_subqueries": 1,
        "pass_count": 1,
        "overflow_applied": False,
        "passes": [{"name": "pass_1", "subqueries": [subquery]}],
        "subqueries": [subquery],
        "uncovered_subqueries": [],
    }


def plan_web_query(user_input: str, temporal: dict[str, Any] | None = None) -> dict[str, Any]:
    temporal = temporal or {}
    query = (user_input or "").strip()
    if not query:
        return {
            "is_multi_intent": False,
            "geo_scope": "",
            "freshness_class": "stable",
            "total_subqueries": 0,
            "pass_count": 0,
            "overflow_applied": False,
            "passes": [],
            "subqueries": [],
            "uncovered_subqueries": [],
        }

    overall_geo = _extract_geo(query)
    time_window = _extract_time_window(query)
    segments = _split_candidate_segments(query)

    raw_candidates = [
        _build_subquery(segment, temporal=temporal, overall_geo=overall_geo, time_window=time_window, original_index=index)
        for index, segment in enumerate(segments)
    ]
    merged_candidates = _merge_same_intent_subqueries(raw_candidates, temporal=temporal, overall_geo=overall_geo, time_window=time_window)

    if len(merged_candidates) <= 1:
        return _default_single_query(query, temporal, overall_geo, time_window)

    ordered = sorted(
        merged_candidates,
        key=lambda item: (-int(item.get("priority", 0)), int(item.get("_original_index", 0))),
    )

    kept = ordered[:MAX_SUBQUERIES]
    dropped = ordered[MAX_SUBQUERIES:]
    normalized_subqueries: list[dict[str, Any]] = []
    for item in kept:
        payload = dict(item)
        payload.pop("_segment", None)
        payload.pop("_original_index", None)
        normalized_subqueries.append(payload)

    passes = [
        {
            "name": f"pass_{pass_index + 1}",
            "subqueries": normalized_subqueries[offset : offset + PASS_SIZE],
        }
        for pass_index, offset in enumerate(range(0, len(normalized_subqueries), PASS_SIZE))
    ]

    freshness_class = "current" if any(item["freshness_class"] == "current" for item in normalized_subqueries) else "stable"
    uncovered = [item["query"] for item in dropped]

    return {
        "is_multi_intent": len(normalized_subqueries) > 1,
        "geo_scope": overall_geo.get("scope", ""),
        "freshness_class": freshness_class,
        "total_subqueries": len(normalized_subqueries),
        "pass_count": len(passes),
        "overflow_applied": len(normalized_subqueries) > PASS_SIZE,
        "passes": passes,
        "subqueries": normalized_subqueries,
        "uncovered_subqueries": uncovered,
    }
