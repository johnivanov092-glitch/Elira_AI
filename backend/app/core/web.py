"""web.py - search orchestration and page loading for Elira."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from .web_engines import (
    CURRENT_WORLD_ENGINES,
    DEFAULT_SEARCH_ENGINES,
    ENGINE_LABELS,
    ENGINE_PRIORITY,
    FINANCE_HIGH_CONFIDENCE_DOMAINS,
    KZ_LOCAL_NEWS_DOMAINS,
    SUPPORTED_SEARCH_ENGINES,
    clean_url as _clean_url,
    domain_matches as _domain_matches,
    extract_domain as _extract_domain,
    get_web_engine_status,
    resolve_search_engines,
    search_duckduckgo as _search_duckduckgo,
    search_tavily as _search_tavily,
    search_wikipedia as _search_wikipedia,
)
from .web_runtime import (
    count_preferred_domain_hits,
    dedupe_results as _dedupe_results,
    fetch_page_text,
    format_search_results,
    rerank_results as _rerank_results,
    research_web_runtime,
    search_news,
    search_web_runtime,
)

logger = logging.getLogger(__name__)


ENGINE_FUNCS = {
    "tavily": _search_tavily,
    "duckduckgo": _search_duckduckgo,
    "wikipedia": _search_wikipedia,
}


def search_web(
    query: str,
    max_results: int = 5,
    engines: Iterable[str] | None = None,
    per_engine: int | None = None,
    *,
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
) -> List[Dict[str, str]]:
    return search_web_runtime(
        query,
        max_results=max_results,
        engines=engines,
        per_engine=per_engine,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
        resolve_search_engines_func=resolve_search_engines,
        engine_funcs=ENGINE_FUNCS,
        logger_obj=logger,
    )


def research_web(
    query: str,
    max_results: int = 5,
    pages_to_read: int = 3,
    engines: Iterable[str] | None = None,
    *,
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
) -> str:
    return research_web_runtime(
        query,
        max_results=max_results,
        pages_to_read=pages_to_read,
        engines=engines,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
        resolve_search_engines_func=resolve_search_engines,
        search_web_func=search_web,
        fetch_page_text_func=fetch_page_text,
    )
