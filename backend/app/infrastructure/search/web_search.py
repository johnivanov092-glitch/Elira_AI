"""Web search infrastructure facade.

Public entrypoints stay in this module for compatibility and test patching.
Runtime logic is split across sibling helpers.
"""
from __future__ import annotations

from typing import Any

from app.infrastructure.search import web_query
from app.infrastructure.search import web_runtime
from app.infrastructure.search import web_temporal


TimelineAppender = Any
WEB_SKIP_FETCH_DOMAINS = web_runtime.WEB_SKIP_FETCH_DOMAINS


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
