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


SUPPORTED_SEARCH_ENGINES = ("searxng", "duckduckgo", "wikipedia")
DEFAULT_SEARCH_ENGINES = SUPPORTED_SEARCH_ENGINES
CURRENT_WORLD_ENGINES = {"searxng", "duckduckgo", "ddg-news"}
ENGINE_PRIORITY = {
    "searxng": 0,
    "duckduckgo": 1,
    "wikipedia": 2,
}
ENGINE_LABELS = {
    "searxng": "SearXNG",
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


def searxng_url() -> str:
    """Base URL of the self-hosted SearXNG metasearch (e.g.
    http://192.168.88.15:8003). Empty when unconfigured -> SearXNG is skipped
    and search degrades to the keyless DuckDuckGo/Wikipedia fallback."""
    return os.environ.get("SEARXNG_URL", "").strip().rstrip("/")


def engine_available(engine: str) -> bool:
    if engine == "searxng":
        return bool(searxng_url())
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
    searxng_enabled = bool(searxng_url())
    available = list(resolve_search_engines())

    primary = "searxng" if searxng_enabled else "duckduckgo"
    fallback = [engine for engine in available if engine != primary]
    degraded = not searxng_enabled
    warnings: list[str] = []

    if not searxng_enabled:
        warnings.append("SEARXNG_URL not configured; web search is running on DuckDuckGo only (no SearXNG metasearch).")

    return {
        "supported_engines": list(SUPPORTED_SEARCH_ENGINES),
        "available_engines": available,
        "primary_engine": primary,
        "fallback_engines": fallback,
        "api_keys_present": {
            "searxng": searxng_enabled,
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


def search_searxng(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Query the self-hosted SearXNG metasearch JSON API. SearXNG already
    aggregates Google/Bing/DuckDuckGo/Wikipedia upstream, so one call fans out
    across engines. Returns snippet-level results (no raw page content — the
    research path fetches full text from the top pages separately)."""
    base = searxng_url()
    if not base:
        raise RuntimeError("SEARXNG_URL is not configured")

    response = session().get(
        f"{base}/search",
        params={"q": query, "format": "json"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    results: list[Dict[str, str]] = []
    for item in payload.get("results", [])[:max_results]:
        href = clean_url(item.get("url", ""))
        if not href.startswith("http"):
            continue
        body = item.get("content") or ""
        results.append(
            {
                "title": (item.get("title") or "").strip(),
                "href": href,
                "body": truncate_text(str(body).strip(), 300),
                "engine": "searxng",
            }
        )
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
