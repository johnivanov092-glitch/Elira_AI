"""
Compatibility wrapper over app.core.web.
Keeps older tool/service callers working while the real source of truth
for search orchestration lives in core/web.py.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from app.core.web import ENGINE_LABELS, format_search_results, research_web as core_research_web
from app.core.web import search_web as core_search_web


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


def research_web(query: str, max_results: int = 8):
    return core_research_web(query, max_results=max_results)
