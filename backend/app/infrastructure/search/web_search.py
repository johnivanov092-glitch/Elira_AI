"""Web search infrastructure facade.

Public entrypoints stay in this module for compatibility and test patching.
Runtime logic is split across sibling helpers.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from app.core.web import ENGINE_LABELS, format_search_results
from app.core.web import research_web as core_research_web
from app.core.web import search_web as core_search_web
from app.infrastructure.search import web_query
from app.infrastructure.search import web_runtime
from app.infrastructure.search import web_temporal


TimelineAppender = Any
WEB_SKIP_FETCH_DOMAINS = web_runtime.WEB_SKIP_FETCH_DOMAINS


def search_web(query: str, max_results: int = 8) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {
            "ok": False,
            "query": query,
            "sources": [],
            "engines_used": [],
            "context": "",
            "count": 0,
            "engine_links": [],
        }

    sources = core_search_web(query, max_results=max_results)
    engines_used = list({item.get("engine", "") for item in sources if item.get("engine")})
    context = format_search_results(sources[:6]) if sources else ""

    engine_links = [
        {"name": "Tavily", "url": "https://app.tavily.com/"},
        {"name": "DuckDuckGo", "url": f"https://duckduckgo.com/?q={quote_plus(query)}"},
        {"name": "Wikipedia", "url": f"https://en.wikipedia.org/w/index.php?search={quote_plus(query)}"},
    ]

    return {
        "ok": bool(sources),
        "query": query,
        "sources": sources,
        "engines_used": [ENGINE_LABELS.get(engine, engine) for engine in engines_used],
        "count": len(sources),
        "context": context,
        "engine_links": engine_links,
    }


def research_web(query: str, max_results: int = 8) -> str:
    return core_research_web(query, max_results=max_results)


def clean_query(query: str) -> str:
    return web_query.clean_query(query)


def fetch_page_text(url: str, max_chars: int = 4000) -> str:
    return web_runtime.fetch_page_text(url, max_chars=max_chars)


def count_hits_for_domains(items: list[dict], preferred_domains: tuple[str, ...]) -> int:
    return web_runtime.count_hits_for_domains(items, preferred_domains)


def get_web_search_result(tool_results: list[dict]) -> dict[str, Any]:
    return web_runtime.get_web_search_result(tool_results)


def is_strict_web_only_query(user_input: str) -> bool:
    return web_query.is_strict_web_only_query(user_input)


def _default_tl(timeline: list, step: str, title: str, status: str, detail: str) -> None:
    web_runtime.default_tl(timeline, step, title, status, detail)


def build_single_web_subquery_context(subquery: dict[str, Any]) -> dict[str, Any]:
    return web_runtime.build_single_web_subquery_context(subquery)


def do_web_search_legacy(
    query: str,
    timeline: list,
    tool_results: list,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    return web_runtime.do_web_search_legacy(
        query,
        timeline,
        tool_results,
        clean_query_func=clean_query,
        tl=tl,
    )


def do_web_search(
    query: str,
    timeline: list,
    tool_results: list,
    web_plan: dict[str, Any] | None = None,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    return web_runtime.do_web_search(
        query,
        timeline,
        tool_results,
        web_plan=web_plan,
        clean_query_func=clean_query,
        build_single_web_subquery_context_func=build_single_web_subquery_context,
        tl=tl,
    )


def do_temporal_web_search_legacy(
    query: str,
    timeline: list,
    tool_results: list,
    temporal: dict[str, Any] | None = None,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    return web_temporal.do_temporal_web_search_legacy(
        query,
        timeline,
        tool_results,
        temporal=temporal,
        do_web_search_legacy_func=do_web_search_legacy,
        get_web_search_result_func=get_web_search_result,
        clean_query_func=clean_query,
        tl=tl,
    )


def do_temporal_web_search(
    query: str,
    timeline: list,
    tool_results: list,
    temporal: dict[str, Any] | None = None,
    web_plan: dict[str, Any] | None = None,
    *,
    tl: TimelineAppender | None = None,
) -> str:
    return web_temporal.do_temporal_web_search(
        query,
        timeline,
        tool_results,
        temporal=temporal,
        web_plan=web_plan,
        do_web_search_func=do_web_search,
        get_web_search_result_func=get_web_search_result,
        clean_query_func=clean_query,
        tl=tl,
    )
