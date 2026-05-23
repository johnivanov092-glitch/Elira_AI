"""Web search infrastructure.

Extracted from agents_service.py — all web search query execution,
page fetching, subquery context building, and temporal enrichment.

This module contains no route or service-layer concerns.  It receives
search parameters and returns structured results that callers
(application services) can feed into prompt context.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from app.core.temporal_intent import detect_temporal_intent
from app.core.web import (
    fetch_page_text as core_fetch,
    research_web,
    search_news as core_search_news,
    search_web as core_search,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QUERY_NOISE = [
    r"^(дай|дай мне|покажи|скажи|расскажи|найди|покажи мне)\s+",
    r"\s+(пожалуйста|плиз|please)$",
]

WEB_SKIP_FETCH_DOMAINS = [
    "youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "vk.com", "t.me", "pinterest.com",
]

# ---------------------------------------------------------------------------
# Query cleaning
# ---------------------------------------------------------------------------


def clean_query(query: str) -> str:
    """Clean and improve a raw user query for web search engines."""
    q = query.strip()
    for p in _QUERY_NOISE:
        q = re.sub(p, "", q, flags=re.IGNORECASE).strip()

    ql = q.lower()

    is_news = any(w in ql for w in [
        "новости", "новость", "события", "произошло", "случилось", "происшеств",
    ])
    is_price = any(w in ql for w in ["курс", "цена", "стоимость"])
    is_weather = "погода" in ql

    temporal = detect_temporal_intent(q)
    if (is_news or is_price or is_weather) and not temporal.get("years"):
        q += " " + str(datetime.now().year)

    date_match = re.search(r"(\d{1,2})\.(\d{2})(?:\.\d{2,4})?", q)
    if date_match and is_news:
        day = date_match.group(1)
        month_num = int(date_match.group(2))
        months = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
        }
        month_name = months.get(month_num, "")
        if month_name:
            q = re.sub(r"\d{1,2}\.\d{2}(?:\.\d{2,4})?", f"{day} {month_name}", q)

    if is_news and not any(w in ql for w in [
        "россия", "украина", "сша", "мир", "казахстан", "кз",
    ]):
        kz_cities = [
            "алматы", "астана", "шымкент", "караганд", "актау",
            "атырау", "павлодар", "семей", "тараз",
        ]
        if any(c in ql for c in kz_cities):
            q += " Казахстан"

    return q or query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_web_search_result(tool_results: list[dict]) -> dict[str, Any]:
    """Return the last web_search tool result payload from *tool_results*."""
    for item in reversed(tool_results or []):
        if item.get("tool") == "web_search":
            result = item.get("result") or {}
            if isinstance(result, dict):
                return result
    return {}


# ---------------------------------------------------------------------------
# Timeline helper (thin wrapper so callers can inject their own)
# ---------------------------------------------------------------------------

TimelineAppender = Any  # Callable[[list, str, str, str, str], None]


def _default_tl(timeline: list, step: str, title: str, status: str, detail: str) -> None:
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


# ---------------------------------------------------------------------------
# Single subquery context builder – private helpers
# ---------------------------------------------------------------------------


def _fetch_news_items(
    query: str,
    intent_kind: str,
    geo_scope: str,
    local_first: bool,
    preferred_domains: tuple,
) -> list[dict[str, Any]]:
    """Run a news-specific search and return normalised result dicts."""
    raw_news = core_search_news(
        query, max_results=5, intent_kind=intent_kind, geo_scope=geo_scope,
        local_first=local_first, preferred_domains=preferred_domains,
    )
    results: list[dict[str, Any]] = []
    for item in raw_news:
        href = item.get("href") or item.get("url") or ""
        if href.startswith("http"):
            results.append({
                "title": item.get("title", ""),
                "url": href,
                "snippet": item.get("body", ""),
                "date": item.get("date", ""),
                "source": item.get("source", ""),
                "engine": item.get("engine", "ddg-news"),
            })
    return results


def _search_and_fetch_pages(
    query: str,
    intent_kind: str,
    geo_scope: str,
    local_first: bool,
    preferred_domains: tuple,
    needs_news_feed: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], set[str]]:
    """Run web search, optional news search, and fetch top result pages.

    Returns (normalized_search, news_results, deep_content, fetched_urls).
    """
    search_results = core_search(
        query, max_results=6, intent_kind=intent_kind, geo_scope=geo_scope,
        local_first=local_first, preferred_domains=preferred_domains,
    )
    normalized_search = [
        {"title": item.get("title", ""), "url": item.get("href", ""),
         "snippet": item.get("body", ""), "engine": item.get("engine", "")}
        for item in search_results
        if item.get("href", "").startswith("http")
    ]

    news_results: list[dict[str, Any]] = (
        _fetch_news_items(query, intent_kind, geo_scope, local_first, preferred_domains)
        if needs_news_feed else []
    )

    fetch_candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in normalized_search:
        url = item["url"]
        if not url or url in seen_urls or any(d in url for d in WEB_SKIP_FETCH_DOMAINS):
            continue
        seen_urls.add(url)
        fetch_candidates.append(item)
        if len(fetch_candidates) >= 4:
            break

    deep_content: list[str] = []
    fetched_urls: set[str] = set()
    for item in fetch_candidates[:2]:
        text = (core_fetch(item["url"]) or "")[:3000]
        if text and len(text) > 100:
            deep_content.append("--- " + item["title"] + " ---\n" + text)
            fetched_urls.add(item["url"])

    return normalized_search, news_results, deep_content, fetched_urls


def _check_coverage_and_deepen(
    normalized_search: list[dict[str, Any]],
    news_results: list[dict[str, Any]],
    preferred_domains: tuple,
    needs_news_feed: bool,
    needs_deep_search: bool,
    query: str,
    intent_kind: str,
    geo_scope: str,
    local_first: bool,
) -> tuple[int, str, bool]:
    """Assess result coverage; optionally run deep multi-engine research.

    Returns (local_source_hits, deep_context, deeper_search_was_used).
    """
    local_source_hits = count_hits_for_domains(
        [{"href": item.get("url", "")} for item in normalized_search + news_results],
        preferred_domains,
    )
    weak_coverage = (
        len(normalized_search) < 3
        or (needs_news_feed and not news_results)
        or (local_first and preferred_domains and local_source_hits == 0)
    )
    if not (needs_deep_search and weak_coverage):
        return local_source_hits, "", False
    deep_engines = (
        ("wikipedia", "tavily", "duckduckgo")
        if intent_kind == "historical"
        else ("tavily", "duckduckgo", "wikipedia")
    )
    deep_context = research_web(
        query,
        max_results=6,
        pages_to_read=3,
        engines=deep_engines,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )
    return local_source_hits, deep_context, bool(deep_context)


def _assemble_subquery_context_parts(
    label: str,
    query: str,
    normalized_search: list[dict[str, Any]],
    news_results: list[dict[str, Any]],
    deep_content: list[str],
    fetched_urls: set[str],
    deep_context: str,
) -> list[str]:
    """Build the ordered list of formatted text sections for a subquery."""
    parts: list[str] = [f"=== ПОДТЕМА: {label} ===", f"Запрос: {query}"]
    if deep_content:
        parts.append("СОДЕРЖИМОЕ ВЕБ-СТРАНИЦ:\n" + "\n\n".join(deep_content))
    if news_results:
        lines = []
        for item in news_results[:5]:
            date_str = f" [{item['date']}]" if item.get("date") else ""
            source_str = f" ({item['source']})" if item.get("source") else ""
            lines.append(f"- {item['title']}{date_str}{source_str}: {item['snippet']}")
        parts.append("СВЕЖИЕ НОВОСТИ:\n" + "\n".join(lines))
    remaining = [item for item in normalized_search if item["url"] not in fetched_urls][:4]
    if remaining:
        lines = [f"- {item['title']}: {item['snippet']}" for item in remaining]
        parts.append("ОСТАЛЬНЫЕ РЕЗУЛЬТАТЫ:\n" + "\n".join(lines))
    if deep_context:
        parts.append("УГЛУБЛЕННЫЙ ПОИСК:\n" + deep_context)
    if not normalized_search and not news_results and not deep_context:
        parts.append("Недостаточно свежих подтвержденных данных по этой подтеме.")
    return parts


# ---------------------------------------------------------------------------
# Single subquery context builder
# ---------------------------------------------------------------------------


def build_single_web_subquery_context(subquery: dict[str, Any]) -> dict[str, Any]:
    """Execute a single web sub-query and return context + debug info."""
    query = subquery.get("query", "")
    label = subquery.get("label", "Поиск")
    intent_kind = subquery.get("intent_kind", "")
    geo_scope = subquery.get("geo_scope", "")
    local_first = bool(subquery.get("local_first"))
    needs_news_feed = bool(subquery.get("needs_news_feed"))
    needs_deep_search = bool(subquery.get("needs_deep_search"))
    preferred_domains = tuple(subquery.get("preferred_domains", []) or [])

    normalized_search, news_results, deep_content, fetched_urls = _search_and_fetch_pages(
        query, intent_kind, geo_scope, local_first, preferred_domains, needs_news_feed,
    )
    local_source_hits, deep_context, deeper_search = _check_coverage_and_deepen(
        normalized_search, news_results, preferred_domains, needs_news_feed, needs_deep_search,
        query, intent_kind, geo_scope, local_first,
    )
    parts = _assemble_subquery_context_parts(
        label, query, normalized_search, news_results, deep_content, fetched_urls, deep_context,
    )

    engines_used = sorted({
        item.get("engine", "")
        for item in normalized_search + news_results
        if item.get("engine")
    })

    return {
        "context": "\n\n".join(part for part in parts if part.strip()),
        "debug": {
            "label": label,
            "query": query,
            "intent_kind": intent_kind,
            "geo_scope": geo_scope,
            "found": len(normalized_search),
            "news_hits": len(news_results),
            "fetched_pages": len(deep_content),
            "engines": engines_used,
            "local_source_hits": local_source_hits,
            "deeper_search_used": deeper_search,
            "coverage": (
                "strong"
                if (len(normalized_search) >= 3 or news_results or deep_content)
                else "weak"
            ),
        },
    }


# ---------------------------------------------------------------------------

def _normalize_search_plan(
    query: str,
    web_plan: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalise the caller-supplied web_plan into a canonical plan + passes.

    Returns ``(search_query, plan, raw_subqueries, passes)``.
    """
    search_query = clean_query(query)
    plan: dict[str, Any] = web_plan or {
        "is_multi_intent": False,
        "subqueries": [{
            "label": "Web search",
            "query": search_query,
            "intent_kind": "general_web",
            "geo_scope": "",
            "freshness_class": "stable",
            "local_first": False,
            "needs_news_feed": False,
            "needs_deep_search": False,
            "preferred_domains": [],
        }],
    }
    raw_subqueries = list(plan.get("subqueries") or [])[:6]
    if not raw_subqueries:
        raw_subqueries = [{
            "label": "Web search",
            "query": search_query,
            "intent_kind": "general_web",
            "geo_scope": "",
            "freshness_class": "stable",
            "local_first": False,
            "needs_news_feed": False,
            "needs_deep_search": False,
            "preferred_domains": [],
            "priority": 0,
        }]
    passes = list(plan.get("passes") or [])
    if not passes:
        passes = [
            {
                "name": f"pass_{pass_index + 1}",
                "subqueries": raw_subqueries[offset:offset + 3],
            }
            for pass_index, offset in enumerate(range(0, len(raw_subqueries), 3))
        ]
    return search_query, plan, raw_subqueries, passes


# Multi-pass web search (planner-driven subquery execution)
# ---------------------------------------------------------------------------


def _process_subquery(
    subquery: dict[str, Any],
    pass_name: str,
    sections: list[str],
    debug_rows: list[dict],
    pass_queries: list[str],
    pass_uncovered: list[str],
    uncovered_subqueries: list[str],
    timeline: list,
    _tl: TimelineAppender,
) -> dict[str, Any]:
    """Execute one subquery and accumulate results into the caller's lists.

    Returns per-subquery metrics dict with keys:
    found, news, pages, local, deeper, engines.
    """
    subquery_result = build_single_web_subquery_context(subquery)
    context = (subquery_result.get("context") or "").strip()
    debug = dict(subquery_result.get("debug") or {})
    debug["pass_name"] = pass_name
    debug_rows.append(debug)
    pass_queries.append(debug.get("query", ""))
    if context:
        sections.append(context)

    found = int(debug.get("found", 0) or 0)
    news_hits = int(debug.get("news_hits", 0) or 0)
    fetched_pages = int(debug.get("fetched_pages", 0) or 0)
    local_hits = int(debug.get("local_source_hits", 0) or 0)
    coverage = str(debug.get("coverage", "weak") or "weak")

    if coverage != "strong":
        pass_uncovered.append(debug.get("query", ""))
        uncovered_subqueries.append(debug.get("query", ""))

    step_id = f"tool_web_{pass_name}_{len(pass_queries)}"
    if found or news_hits or fetched_pages:
        _tl(timeline, step_id, f"Веб-поиск {pass_name}", "done",
            f"{debug.get('query', '')}: found={found}, news={news_hits}, pages={fetched_pages}")
    else:
        _tl(timeline, step_id, f"Веб-поиск {pass_name}", "error",
            f"{debug.get('query', '')}: no confirmed results")

    return {
        "found": found, "news": news_hits, "pages": fetched_pages,
        "local": local_hits, "deeper": bool(debug.get("deeper_search_used")),
        "engines": list(debug.get("engines", []) or []),
    }


def _build_web_search_payload(
    search_query: str,
    plan: dict[str, Any],
    raw_subqueries: list[dict],
    debug_rows: list[dict],
    pass_summaries: list[dict],
    engines_used: set[str],
    total_found: int,
    total_news: int,
    total_fetched: int,
    total_local_hits: int,
    deeper_search_used: bool,
    uncovered_subqueries: list[str],
) -> dict[str, Any]:
    """Assemble the result_payload dict from accumulated multi-pass search data."""
    unique_uncovered = list(dict.fromkeys(item for item in uncovered_subqueries if item))
    return {
        "query": search_query,
        "count": total_found,
        "found": total_found,
        "news": total_news,
        "fetched_pages": total_fetched,
        "engines": sorted(engines_used),
        "subqueries": [debug.get("query", "") for debug in debug_rows],
        "coverage_by_subquery": {
            debug.get("query", f"subquery_{idx + 1}"): debug.get("coverage", "weak")
            for idx, debug in enumerate(debug_rows)
        },
        "engines_by_subquery": {
            debug.get("query", f"subquery_{idx + 1}"): debug.get("engines", [])
            for idx, debug in enumerate(debug_rows)
        },
        "local_source_hits": total_local_hits,
        "news_hits": total_news,
        "deeper_search_used": deeper_search_used,
        "is_multi_intent": bool(plan.get("is_multi_intent")),
        "passes": pass_summaries,
        "pass_count": len(pass_summaries),
        "total_subqueries": len(raw_subqueries),
        "overflow_applied": bool(plan.get("overflow_applied") or len(raw_subqueries) > 3),
        "uncovered_subqueries": unique_uncovered,
    }


# (Legacy single-pass do_web_search_legacy removed — zero callers)

def do_web_search(
    query: str,
    timeline: list,
    tool_results: list,
    web_plan: dict[str, Any] | None = None,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    """Execute a planner-driven multi-pass web search."""
    _tl = tl or _default_tl
    search_query, plan, raw_subqueries, passes = _normalize_search_plan(query, web_plan)


    sections: list[str] = []
    debug_rows: list[dict] = []
    pass_summaries: list[dict] = []
    engines_used: set[str] = set()
    total_found = 0
    total_news = 0
    total_fetched = 0
    total_local_hits = 0
    deeper_search_used = False
    uncovered_subqueries: list[str] = list(plan.get("uncovered_subqueries") or [])

    for pass_index, pass_spec in enumerate(passes, start=1):
        pass_name = str(pass_spec.get("name") or f"pass_{pass_index}")
        pass_found = 0
        pass_news = 0
        pass_pages = 0
        pass_engines: set[str] = set()
        pass_queries: list[str] = []
        pass_uncovered: list[str] = []

        for subquery in list(pass_spec.get("subqueries") or [])[:3]:
            m = _process_subquery(
                subquery, pass_name, sections, debug_rows, pass_queries, pass_uncovered,
                uncovered_subqueries, timeline, _tl,
            )
            total_found += m["found"]; total_news += m["news"]; total_fetched += m["pages"]
            total_local_hits += m["local"]; deeper_search_used = deeper_search_used or m["deeper"]
            engines_used.update(m["engines"]); pass_found += m["found"]
            pass_news += m["news"]; pass_pages += m["pages"]; pass_engines.update(m["engines"])

        pass_summaries.append({
            "name": pass_name,
            "subqueries": pass_queries,
            "found": pass_found,
            "news_hits": pass_news,
            "fetched_pages": pass_pages,
            "engines": sorted(pass_engines),
            "uncovered_subqueries": [item for item in pass_uncovered if item],
        })
        _tl(timeline, f"tool_web_{pass_name}", f"Веб-проход {pass_index}", "done",
            f"{len(pass_queries)} подтем, found={pass_found}, news={pass_news}, pages={pass_pages}")

    result_payload = _build_web_search_payload(
        search_query, plan, raw_subqueries, debug_rows, pass_summaries, engines_used,
        total_found, total_news, total_fetched, total_local_hits, deeper_search_used, uncovered_subqueries,
    )
    tool_results.append({"tool": "web_search", "result": result_payload})

    if not sections:
        _tl(timeline, "tool_web", "Веб-поиск", "error", "Нет подтвержденных результатов")
        return "[Поиск не дал результатов]"

    _tl(timeline, "tool_web", "Веб-поиск", "done",
        f"{total_found} найдено, {total_news} новостей, "
        f"{total_fetched} страниц, {len(raw_subqueries)} подтем, "
        f"{len(pass_summaries)} проходов")
    return "\n\n".join(section for section in sections if section.strip())


# ---------------------------------------------------------------------------
# Temporal enrichment wrappers
# ---------------------------------------------------------------------------


def _compute_freshness(
    *,
    temporal: dict[str, Any],
    has_current_evidence: bool,
    news_count: int,
    fetched_pages: int,
    deeper_search: bool,
) -> tuple[str, str]:
    """Return (freshness_state, freshness_note) based on temporal flags."""
    if temporal.get("freshness_sensitive"):
        is_fresh = has_current_evidence and (news_count > 0 or fetched_pages >= 2 or deeper_search)
        state = "fresh_checked" if is_fresh else "unverified_current"
        note = (
            "Freshness status: fresh_checked. Use current web findings as the main evidence."
            if is_fresh
            else "Freshness status: unverified_current. If confidence is limited, "
                 "say that the data may be outdated or not fully verified."
        )
    elif temporal.get("stable_historical"):
        state = "historical_or_stable"
        note = "Freshness status: historical_or_stable. Treat this as a mostly stable historical topic."
    else:
        state = "standard_web"
        note = "Freshness status: standard_web. Use the web findings naturally without exposing internal formatting."
    return state, note


def _try_deep_research(
    query: str,
    temporal: dict[str, Any],
    timeline: list,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    """Attempt an additional deep research pass if coverage is weak."""
    _tl_fn = tl or _default_tl
    try:
        deep_engines = (
            ("wikipedia", "tavily", "duckduckgo")
            if temporal.get("stable_historical")
            else ("tavily", "duckduckgo", "wikipedia")
        )
        deep_context = research_web(
            clean_query(query),
            max_results=8,
            pages_to_read=4,
            engines=deep_engines,
            intent_kind="historical" if temporal.get("stable_historical") else "general_web",
        )
        if deep_context:
            _tl_fn(timeline, "tool_web_deep", "Углубленный веб-поиск", "done",
                    "Дополнительная проверка источников")
            return deep_context
    except Exception as exc:
        _tl_fn(timeline, "tool_web_deep", "Углубленный веб-поиск", "error", str(exc))
    return ""


def do_temporal_web_search(
    query: str,
    timeline: list,
    tool_results: list,
    temporal: dict[str, Any] | None = None,
    web_plan: dict[str, Any] | None = None,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    """Planner-driven multi-pass temporal web search."""
    temporal = temporal or {}
    context = do_web_search(query, timeline, tool_results, web_plan=web_plan, tl=tl)
    web_result = get_web_search_result(tool_results)
    found = int(web_result.get("found", 0) or 0)
    fetched_pages = int(web_result.get("fetched_pages", 0) or 0)
    news_count = int(web_result.get("news", 0) or 0)
    subquery_count = int(
        web_result.get("total_subqueries", len(web_result.get("subqueries", []) or []))
        or 0
    )
    engines_used = set(web_result.get("engines", []) or [])
    current_evidence_engines = {"tavily", "duckduckgo", "ddg-news"}
    has_current_evidence = bool(engines_used & current_evidence_engines) or news_count > 0
    deeper_search = bool(web_result.get("deeper_search_used"))

    if temporal.get("requires_web") and temporal.get("reasoning_depth") == "deep":
        weak_coverage = (
            found < max(4, subquery_count * 2)
            or fetched_pages < max(2, subquery_count)
            or (temporal.get("freshness_sensitive") and not has_current_evidence)
        )
        if weak_coverage:
            deep_ctx = _try_deep_research(query, temporal, timeline, tl=tl)
            if deep_ctx:
                deeper_search = True
                context = (
                    context + "\n\nДополнительный углубленный веб-поиск:\n" + deep_ctx
                    if context else deep_ctx
                )

    freshness_state, freshness_note = _compute_freshness(
        temporal=temporal,
        has_current_evidence=has_current_evidence,
        news_count=news_count,
        fetched_pages=fetched_pages,
        deeper_search=deeper_search,
    )

    if tool_results and tool_results[-1].get("tool") == "web_search":
        result = tool_results[-1].setdefault("result", {})
        if isinstance(result, dict):
            result["freshness_state"] = freshness_state
            result["deeper_search"] = deeper_search
            result["temporal_mode"] = temporal.get("mode", "none")
            result["has_current_evidence"] = has_current_evidence

    if context:
        context += "\n\n" + freshness_note
    return context
