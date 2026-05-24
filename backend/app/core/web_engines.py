from __future__ import annotations

import os
from typing import Dict, Iterable, List
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - compatibility fallback
    from duckduckgo_search import DDGS

from .files import truncate_text


SUPPORTED_SEARCH_ENGINES = ("tavily", "duckduckgo", "wikipedia")
DEFAULT_SEARCH_ENGINES = SUPPORTED_SEARCH_ENGINES
CURRENT_WORLD_ENGINES = {"tavily", "duckduckgo", "ddg-news"}
ENGINE_PRIORITY = {
    "tavily": 0,
    "duckduckgo": 1,
    "wikipedia": 2,
}
ENGINE_LABELS = {
    "tavily": "Tavily",
    "duckduckgo": "DuckDuckGo",
    "wikipedia": "Wikipedia",
    "ddg-news": "DDG News",
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


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        }
    )
    return client


def clean_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("/url?"):
        try:
            query = parse_qs(urlparse(url).query)
            url = query.get("q", [url])[0]
        except Exception:
            pass
    return unquote(url)


def extract_domain(url: str) -> str:
    try:
        return urlparse(clean_url(url)).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def domain_matches(domain: str, expected: Iterable[str]) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in expected)


def tavily_api_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def engine_available(engine: str) -> bool:
    if engine == "tavily":
        return bool(tavily_api_key())
    return engine in {"duckduckgo", "wikipedia"}


def resolve_search_engines(engines: Iterable[str] | None = None) -> tuple[str, ...]:
    requested = list(engines or DEFAULT_SEARCH_ENGINES)
    resolved: list[str] = []
    for engine in requested:
        if engine not in SUPPORTED_SEARCH_ENGINES:
            continue
        if not engine_available(engine):
            continue
        resolved.append(engine)
    if "duckduckgo" not in resolved:
        resolved.append("duckduckgo")
    if "wikipedia" not in resolved:
        resolved.append("wikipedia")
    deduped: list[str] = []
    for engine in resolved:
        if engine not in deduped:
            deduped.append(engine)
    return tuple(deduped)


def get_web_engine_status() -> dict:
    tavily_enabled = bool(tavily_api_key())
    available = list(resolve_search_engines())

    if tavily_enabled:
        primary = "tavily"
    else:
        primary = "duckduckgo"

    fallback = [engine for engine in available if engine != primary]
    degraded = not tavily_enabled
    warnings: list[str] = []

    if not tavily_enabled:
        warnings.append("TAVILY_API_KEY not configured; deep research is running without Tavily.")

    return {
        "supported_engines": list(SUPPORTED_SEARCH_ENGINES),
        "available_engines": available,
        "primary_engine": primary,
        "fallback_engines": fallback,
        "api_keys_present": {
            "tavily": tavily_enabled,
        },
        "degraded_mode": degraded,
        "warnings": warnings,
    }


def search_duckduckgo(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results: list[Dict[str, str]] = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=max_results):
            results.append(
                {
                    "title": item.get("title", ""),
                    "href": clean_url(item.get("href", "")),
                    "body": item.get("body", ""),
                    "engine": "duckduckgo",
                }
            )
    return results


def search_tavily(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_raw_content: bool = False,
) -> List[Dict[str, str]]:
    api_key = tavily_api_key()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not configured")

    response = session().post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": include_raw_content,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    results: list[Dict[str, str]] = []
    for item in payload.get("results", [])[:max_results]:
        href = clean_url(item.get("url", ""))
        if not href.startswith("http"):
            continue
        body = item.get("content") or item.get("snippet") or ""
        row = {
            "title": (item.get("title") or "").strip(),
            "href": href,
            "body": truncate_text(str(body).strip(), 300),
            "engine": "tavily",
        }
        raw_content = item.get("raw_content")
        if include_raw_content and raw_content:
            row["raw_content"] = truncate_text(str(raw_content).strip(), 12000)
        results.append(row)
    return results


def search_wikipedia(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results: list[Dict[str, str]] = []
    for lang in ("ru", "en"):
        if len(results) >= max_results:
            break
        try:
            response = session().get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": min(max_results, 5),
                    "format": "json",
                    "utf8": 1,
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("query", {}).get("search", []):
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                href = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                if any(existing["title"] == title for existing in results):
                    continue
                results.append(
                    {
                        "title": f"{title} (Wikipedia {lang.upper()})",
                        "href": href,
                        "body": re_sub_html(snippet),
                        "engine": "wikipedia",
                    }
                )
                if len(results) >= max_results:
                    break
        except Exception:
            continue
    return results


def re_sub_html(snippet: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", snippet)
