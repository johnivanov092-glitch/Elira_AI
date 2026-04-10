"""Web search infrastructure.

Extracted from agents_service.py — all web search query execution,
page fetching, subquery context building, and temporal enrichment.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

_QUERY_NOISE = [
    r"^(дай|дай мне|покажи|скажи|расскажи|найди|покажи мне)\s+",
    r"\s+(пожалуйста|плиз|please)$",
]

WEB_SKIP_FETCH_DOMAINS = [
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "vk.com",
    "t.me",
    "pinterest.com",
]

TimelineAppender = Any


def clean_query(query: str) -> str:
    """Clean and improve a raw user query for web search engines."""
    from datetime import datetime

    from app.services.temporal_intent import detect_temporal_intent

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


def fetch_page_text(url: str, max_chars: int = 4000) -> str:
    """Fetch and extract main text content from a web page."""
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return ""

        if resp.encoding and resp.encoding.lower() != "utf-8":
            resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(
            [
                "script",
                "style",
                "nav",
                "header",
                "footer",
                "aside",
                "form",
                "button",
                "iframe",
                "noscript",
                "svg",
                "img",
                "menu",
                "advertisement",
                "ad",
                "banner",
            ]
        ):
            tag.decompose()

        for element in soup.select(
            "[class*='advert'], [class*='banner'], [class*='cookie'], "
            "[class*='popup'], [class*='modal'], [id*='advert'], [id*='banner']"
        ):
            element.decompose()

        content_selectors = [
            "article",
            "main",
            "[role='main']",
            ".article-body",
            ".article-content",
            ".post-content",
            ".entry-content",
            ".news-body",
            ".story-body",
            ".text-content",
            ".content",
            "#content",
            "#main-content",
        ]
        main_el = None
        for selector in content_selectors:
            main_el = soup.select_one(selector)
            if main_el and len(main_el.get_text(strip=True)) > 100:
                break
            main_el = None

        if main_el:
            text = main_el.get_text(separator="\n", strip=True)
        else:
            body = soup.find("body")
            text = (body or soup).get_text(separator="\n", strip=True)

        lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 20]
        text = "\n".join(lines)
        return text[:max_chars] if text else ""
    except Exception:
        return ""


def count_hits_for_domains(items: list[dict], preferred_domains: tuple[str, ...]) -> int:
    try:
        from app.core.web import count_preferred_domain_hits

        return count_preferred_domain_hits(items, preferred_domains)
    except Exception:
        return 0


def get_web_search_result(tool_results: list[dict]) -> dict[str, Any]:
    """Return the last web_search tool result payload from tool_results."""
    for item in reversed(tool_results or []):
        if item.get("tool") == "web_search":
            result = item.get("result") or {}
            if isinstance(result, dict):
                return result
    return {}


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


def _default_tl(timeline: list, step: str, title: str, status: str, detail: str) -> None:
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


def build_single_web_subquery_context(subquery: dict[str, Any]) -> dict[str, Any]:
    """Execute a single web sub-query and return context + debug info."""
    from app.core.web import (
        fetch_page_text as core_fetch,
        research_web,
        search_news as core_search_news,
        search_web as core_search,
    )

    query = subquery.get("query", "")
    label = subquery.get("label", "Поиск")
    intent_kind = subquery.get("intent_kind", "")
    geo_scope = subquery.get("geo_scope", "")
    local_first = bool(subquery.get("local_first"))
    needs_news_feed = bool(subquery.get("needs_news_feed"))
    needs_deep_search = bool(subquery.get("needs_deep_search"))
    preferred_domains = tuple(subquery.get("preferred_domains", []) or [])

    search_results = core_search(
        query,
        max_results=6,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )
    normalized_search = [
        {
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "snippet": item.get("body", ""),
            "engine": item.get("engine", ""),
        }
        for item in search_results
        if item.get("href", "").startswith("http")
    ]

    news_results: list[dict[str, Any]] = []
    if needs_news_feed:
        raw_news = core_search_news(
            query,
            max_results=5,
            intent_kind=intent_kind,
            geo_scope=geo_scope,
            local_first=local_first,
            preferred_domains=preferred_domains,
        )
        for item in raw_news:
            href = item.get("href") or item.get("url") or ""
            if href.startswith("http"):
                news_results.append(
                    {
                        "title": item.get("title", ""),
                        "url": href,
                        "snippet": item.get("body", ""),
                        "date": item.get("date", ""),
                        "source": item.get("source", ""),
                        "engine": item.get("engine", "ddg-news"),
                    }
                )

    fetch_candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in normalized_search:
        url = item["url"]
        if not url or url in seen_urls or any(domain in url for domain in WEB_SKIP_FETCH_DOMAINS):
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

    local_source_hits = count_hits_for_domains(
        [{"href": item.get("url", "")} for item in normalized_search + news_results],
        preferred_domains,
    )
    weak_coverage = (
        len(normalized_search) < 3
        or (needs_news_feed and not news_results)
        or (local_first and preferred_domains and local_source_hits == 0)
    )

    deeper_search = False
    deep_context = ""
    if needs_deep_search and weak_coverage:
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
        deeper_search = bool(deep_context)

    parts = [f"=== ПОДТЕМА: {label} ===", f"Запрос: {query}"]
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

    engines_used = sorted(
        {
            item.get("engine", "")
            for item in normalized_search + news_results
            if item.get("engine")
        }
    )

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
            "coverage": "strong" if (len(normalized_search) >= 3 or news_results or deep_content) else "weak",
        },
    }


def do_web_search_legacy(
    query: str,
    timeline: list,
    tool_results: list,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    """Multi-engine search with parallel page fetch. Legacy single-pass variant."""
    _tl = tl or _default_tl
    search_query = clean_query(query)

    search_results: list[dict[str, Any]] = []
    engines_used: list[str] = []
    try:
        from app.core.web import search_news as core_search_news
        from app.core.web import search_web as multi_search

        raw = multi_search(search_query, max_results=12)
        for result in raw:
            href = result.get("href", "")
            if href and href.startswith("http"):
                search_results.append(
                    {
                        "title": result.get("title", ""),
                        "url": href,
                        "snippet": result.get("body", ""),
                        "engine": result.get("engine", ""),
                    }
                )
        engines_used = sorted({result.get("engine", "") for result in raw if result.get("engine")})
    except Exception as exc:
        logger.warning("Web search failed: %s", exc)

    if not search_results:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                raw = list(ddgs.text(search_query, max_results=8))
            for result in raw:
                url = result.get("href") or result.get("url") or ""
                if url:
                    search_results.append(
                        {
                            "title": result.get("title", ""),
                            "url": url,
                            "snippet": result.get("body", ""),
                            "engine": "duckduckgo",
                        }
                    )
            engines_used = ["duckduckgo"]
        except Exception as exc:
            logger.warning("DDG fallback also failed: %s", exc)

    news_results: list[dict[str, Any]] = []
    try:
        news_raw = core_search_news(search_query, max_results=5)  # type: ignore[possibly-undefined]
        for item in news_raw:
            url = item.get("href") or item.get("url") or ""
            if url and url.startswith("http"):
                news_results.append(
                    {
                        "title": item.get("title", ""),
                        "url": url,
                        "snippet": item.get("body", ""),
                        "date": item.get("date", ""),
                        "source": item.get("source", ""),
                    }
                )
        if news_results and "ddg-news" not in engines_used:
            engines_used.append("ddg-news")
    except Exception:
        pass

    if not search_results and not news_results:
        _tl(timeline, "tool_web", "Веб-поиск", "error", "Нет результатов")
        tool_results.append({"tool": "web_search", "result": {"count": 0}})
        return "[Поиск не дал результатов]"

    deep_content: list[str] = []
    fetched_urls: set[str] = set()

    all_urls_seen: set[str] = set()
    fetch_candidates: list[dict[str, Any]] = []
    for item in search_results[:7]:
        url = item["url"]
        if url not in all_urls_seen and not any(domain in url for domain in WEB_SKIP_FETCH_DOMAINS):
            all_urls_seen.add(url)
            fetch_candidates.append(item)

    targets = fetch_candidates[:5]
    if targets:
        try:
            from app.core.web import fetch_page_text as core_fetch_fn
        except ImportError:
            core_fetch_fn = fetch_page_text  # type: ignore[assignment]

        page_results: dict[str, tuple[dict, str]] = {}
        with ThreadPoolExecutor(max_workers=min(len(targets), 4)) as executor:
            future_map = {executor.submit(core_fetch_fn, target["url"]): target for target in targets}
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    text = (future.result() or "")[:3000]
                    if text and len(text) > 100:
                        page_results[item["url"]] = (item, text)
                except Exception:
                    pass

        for target in targets:
            if target["url"] in page_results and len(deep_content) < 3:
                item, text = page_results[target["url"]]
                deep_content.append("--- " + item["title"] + " ---\n" + text)
                fetched_urls.add(item["url"])

    fetched_count = len(deep_content)
    engines_str = ", ".join(engines_used) if engines_used else "search"
    tool_results.append(
        {
            "tool": "web_search",
            "result": {
                "query": search_query,
                "found": len(search_results),
                "news": len(news_results),
                "fetched_pages": fetched_count,
                "engines": engines_used,
            },
        }
    )
    _tl(
        timeline,
        "tool_web",
        "Веб-поиск",
        "done",
        f"{len(search_results)} найдено ({engines_str}), {fetched_count} страниц загружено, {len(news_results)} новостей",
    )

    parts: list[str] = []
    if deep_content:
        parts.append("══ СОДЕРЖИМОЕ ВЕБ-СТРАНИЦ (ИСПОЛЬЗУЙ ЭТИ ДАННЫЕ!) ══\n\n" + "\n\n".join(deep_content))

    if news_results:
        news_lines = []
        for item in news_results[:5]:
            date_str = f" [{item['date']}]" if item.get("date") else ""
            source_str = f" ({item['source']})" if item.get("source") else ""
            news_lines.append(f"- {item['title']}{date_str}{source_str}: {item['snippet']}")
        parts.append("══ СВЕЖИЕ НОВОСТИ ══\n" + "\n".join(news_lines))

    remaining = [item for item in search_results if item["url"] not in fetched_urls][:5]
    if remaining:
        snippet_lines = [f"- {item['title']}: {item['snippet']}" for item in remaining]
        parts.append("══ ДРУГИЕ РЕЗУЛЬТАТЫ ══\n" + "\n".join(snippet_lines))

    return "\n\n".join(parts)


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
    search_query = clean_query(query)
    plan = web_plan or {
        "is_multi_intent": False,
        "subqueries": [
            {
                "label": "Web search",
                "query": search_query,
                "intent_kind": "general_web",
                "geo_scope": "",
                "freshness_class": "stable",
                "local_first": False,
                "needs_news_feed": False,
                "needs_deep_search": False,
                "preferred_domains": [],
            }
        ],
    }

    raw_subqueries = list(plan.get("subqueries") or [])[:6]
    if not raw_subqueries:
        raw_subqueries = [
            {
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
            }
        ]

    passes = list(plan.get("passes") or [])
    if not passes:
        passes = [
            {
                "name": f"pass_{pass_index + 1}",
                "subqueries": raw_subqueries[offset : offset + 3],
            }
            for pass_index, offset in enumerate(range(0, len(raw_subqueries), 3))
        ]

    sections: list[str] = []
    debug_rows: list[dict[str, Any]] = []
    pass_summaries: list[dict[str, Any]] = []
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

            total_found += found
            total_news += news_hits
            total_fetched += fetched_pages
            total_local_hits += local_hits
            deeper_search_used = deeper_search_used or bool(debug.get("deeper_search_used"))
            engines_used.update(debug.get("engines", []) or [])

            pass_found += found
            pass_news += news_hits
            pass_pages += fetched_pages
            pass_engines.update(debug.get("engines", []) or [])

            if coverage != "strong":
                pass_uncovered.append(debug.get("query", ""))
                uncovered_subqueries.append(debug.get("query", ""))

            step_id = f"tool_web_{pass_name}_{len(pass_queries)}"
            if found or news_hits or fetched_pages:
                _tl(
                    timeline,
                    step_id,
                    f"Веб-поиск {pass_name}",
                    "done",
                    f"{debug.get('query', '')}: found={found}, news={news_hits}, pages={fetched_pages}",
                )
            else:
                _tl(
                    timeline,
                    step_id,
                    f"Веб-поиск {pass_name}",
                    "error",
                    f"{debug.get('query', '')}: no confirmed results",
                )

        pass_summaries.append(
            {
                "name": pass_name,
                "subqueries": pass_queries,
                "found": pass_found,
                "news_hits": pass_news,
                "fetched_pages": pass_pages,
                "engines": sorted(pass_engines),
                "uncovered_subqueries": [item for item in pass_uncovered if item],
            }
        )
        _tl(
            timeline,
            f"tool_web_{pass_name}",
            f"Веб-проход {pass_index}",
            "done",
            f"{len(pass_queries)} подтем, found={pass_found}, news={pass_news}, pages={pass_pages}",
        )

    unique_uncovered = list(dict.fromkeys(item for item in uncovered_subqueries if item))
    result_payload: dict[str, Any] = {
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
    tool_results.append({"tool": "web_search", "result": result_payload})

    if not sections:
        _tl(timeline, "tool_web", "Веб-поиск", "error", "Нет подтвержденных результатов")
        return "[Поиск не дал результатов]"

    _tl(
        timeline,
        "tool_web",
        "Веб-поиск",
        "done",
        f"{total_found} найдено, {total_news} новостей, {total_fetched} страниц, {len(raw_subqueries)} подтем, {len(pass_summaries)} проходов",
    )
    return "\n\n".join(section for section in sections if section.strip())


def _compute_freshness(
    *,
    temporal: dict[str, Any],
    has_current_evidence: bool,
    news_count: int,
    fetched_pages: int,
    deeper_search: bool,
) -> tuple[str, str]:
    """Return freshness_state and freshness_note based on temporal flags."""
    if temporal.get("freshness_sensitive"):
        is_fresh = has_current_evidence and (news_count > 0 or fetched_pages >= 2 or deeper_search)
        state = "fresh_checked" if is_fresh else "unverified_current"
        note = (
            "Freshness status: fresh_checked. Use current web findings as the main evidence."
            if is_fresh
            else "Freshness status: unverified_current. If confidence is limited, say that the data may be outdated or not fully verified."
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
        from app.core.web import research_web

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
            _tl_fn(
                timeline,
                "tool_web_deep",
                "Углубленный веб-поиск",
                "done",
                "Дополнительная проверка источников",
            )
            return deep_context
    except Exception as exc:
        _tl_fn(timeline, "tool_web_deep", "Углубленный веб-поиск", "error", str(exc))
    return ""


def do_temporal_web_search_legacy(
    query: str,
    timeline: list,
    tool_results: list,
    temporal: dict[str, Any] | None = None,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    """Legacy single-pass temporal web search."""
    temporal = temporal or {}
    context = do_web_search_legacy(query, timeline, tool_results, tl=tl)
    web_result = get_web_search_result(tool_results)
    found = int(web_result.get("found", 0) or 0)
    fetched_pages = int(web_result.get("fetched_pages", 0) or 0)
    news_count = int(web_result.get("news", 0) or 0)
    engines_used = set(web_result.get("engines", []) or [])
    current_evidence_engines = {"tavily", "duckduckgo", "ddg-news"}
    has_current_evidence = bool(engines_used & current_evidence_engines) or news_count > 0
    deeper_search = False

    if temporal.get("requires_web") and temporal.get("reasoning_depth") == "deep":
        weak_coverage = found < 4 or fetched_pages < 2 or (
            temporal.get("freshness_sensitive") and not has_current_evidence
        )
        if weak_coverage:
            deep_context = _try_deep_research(query, temporal, timeline, tl=tl)
            if deep_context:
                deeper_search = True
                context = (
                    context + "\n\nДополнительный углубленный веб-поиск:\n" + deep_context
                    if context
                    else deep_context
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
        web_result.get("total_subqueries", len(web_result.get("subqueries", []) or [])) or 0
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
            deep_context = _try_deep_research(query, temporal, timeline, tl=tl)
            if deep_context:
                deeper_search = True
                context = (
                    context + "\n\nДополнительный углубленный веб-поиск:\n" + deep_context
                    if context
                    else deep_context
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
