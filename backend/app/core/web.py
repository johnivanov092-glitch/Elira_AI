"""web.py - search orchestration and page loading for Elira."""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - compatibility fallback
    from duckduckgo_search import DDGS

from .files import truncate_text

logger = logging.getLogger(__name__)


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


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        }
    )
    return session


def _clean_url(url: str) -> str:
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


def _dedupe_results(results: Iterable[Dict[str, str]], max_results: int) -> List[Dict[str, str]]:
    ordered = sorted(
        results,
        key=lambda item: (
            ENGINE_PRIORITY.get(str(item.get("engine", "")).strip(), 99),
            str(item.get("title", "")).strip().lower(),
        ),
    )

    unique: list[Dict[str, str]] = []
    seen = set()
    for item in ordered:
        href = _clean_url(item.get("href", ""))
        title = (item.get("title", "") or "").strip()
        body = (item.get("body", "") or "").strip()
        engine = (item.get("engine", "") or "").strip()
        key = href or f"{title}|{body}"
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "title": title,
                "href": href,
                "body": body,
                "engine": engine,
            }
        )
        if len(unique) >= max_results:
            break
    return unique


def _tavily_api_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def _engine_available(engine: str) -> bool:
    if engine == "tavily":
        return bool(_tavily_api_key())
    return engine in {"duckduckgo", "wikipedia"}


def resolve_search_engines(engines: Iterable[str] | None = None) -> tuple[str, ...]:
    requested = list(engines or DEFAULT_SEARCH_ENGINES)
    resolved: list[str] = []
    for engine in requested:
        if engine not in SUPPORTED_SEARCH_ENGINES:
            continue
        if not _engine_available(engine):
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
    tavily_enabled = bool(_tavily_api_key())
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


def _search_duckduckgo(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results: list[Dict[str, str]] = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=max_results):
            results.append(
                {
                    "title": item.get("title", ""),
                    "href": _clean_url(item.get("href", "")),
                    "body": item.get("body", ""),
                    "engine": "duckduckgo",
                }
            )
    return results


def _search_tavily(query: str, max_results: int = 5, search_depth: str = "basic", include_raw_content: bool = False) -> List[Dict[str, str]]:
    api_key = _tavily_api_key()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not configured")

    response = _session().post(
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
        href = _clean_url(item.get("url", ""))
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


def _search_wikipedia(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results: list[Dict[str, str]] = []
    for lang in ("ru", "en"):
        if len(results) >= max_results:
            break
        try:
            response = _session().get(
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
                snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
                href = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                if any(existing["title"] == title for existing in results):
                    continue
                results.append(
                    {
                        "title": f"{title} (Wikipedia {lang.upper()})",
                        "href": href,
                        "body": snippet,
                        "engine": "wikipedia",
                    }
                )
                if len(results) >= max_results:
                    break
        except Exception:
            continue
    return results


ENGINE_FUNCS = {
    "tavily": _search_tavily,
    "duckduckgo": _search_duckduckgo,
    "wikipedia": _search_wikipedia,
}


def search_news(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results: list[Dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.news(query, max_results=max_results):
                href = item.get("url") or item.get("href") or ""
                if not href.startswith("http"):
                    continue
                results.append(
                    {
                        "title": item.get("title", ""),
                        "href": href,
                        "body": item.get("body", ""),
                        "date": item.get("date", ""),
                        "source": item.get("source", ""),
                        "engine": "ddg-news",
                    }
                )
    except Exception:
        return []
    return results


def search_web(
    query: str,
    max_results: int = 5,
    engines: Iterable[str] | None = None,
    per_engine: int | None = None,
) -> List[Dict[str, str]]:
    engine_list = list(resolve_search_engines(engines))
    per_engine = per_engine or max(3, max_results)
    combined: list[Dict[str, str]] = []

    for engine in engine_list:
        search_fn = ENGINE_FUNCS.get(engine)
        if not search_fn:
            continue
        try:
            combined.extend(search_fn(query, max_results=per_engine))
        except Exception as exc:
            logger.warning(
                "web search engine '%s' failed for query %r: %s",
                engine,
                query,
                exc,
            )

    merged = _dedupe_results(combined, max_results=max_results)
    return merged[:max_results]


def format_search_results(results: List[Dict[str, str]]) -> str:
    return "\n\n".join(
        f"[{index}] {item.get('title', '')}\n"
        f"Поисковик: {ENGINE_LABELS.get(item.get('engine', ''), item.get('engine', '') or '-')}\n"
        f"Ссылка: {item.get('href', '')}\n"
        f"Описание: {item.get('body', '')}"
        for index, item in enumerate(results, start=1)
    )


def fetch_page_text(url: str) -> str:
    try:
        response = _session().get(url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\n{2,}", "\n\n", soup.get_text("\n"))
        return truncate_text(text, 10000)
    except Exception as exc:
        return f"Ошибка чтения страницы: {exc}"


def _tavily_research(query: str, max_results: int) -> List[Dict[str, str]]:
    try:
        return _search_tavily(
            query,
            max_results=max_results,
            search_depth="advanced",
            include_raw_content=True,
        )
    except Exception:
        return []


def research_web(
    query: str,
    max_results: int = 5,
    pages_to_read: int = 3,
    engines: Iterable[str] | None = None,
) -> str:
    engine_list = list(resolve_search_engines(engines))
    advanced_items: list[Dict[str, str]] = []
    if "tavily" in engine_list:
        advanced_items = _tavily_research(query, max_results=min(max_results, pages_to_read))

    merged_results = _dedupe_results(
        [*advanced_items, *search_web(query, max_results=max_results, engines=engine_list)],
        max_results=max_results,
    )
    to_fetch = [item for item in merged_results[:pages_to_read] if item.get("href")]

    page_texts: Dict[str, str] = {}
    for item in advanced_items:
        if item.get("raw_content"):
            page_texts[item["href"]] = item["raw_content"]

    remaining = [item for item in to_fetch if item.get("href") not in page_texts]
    if remaining:
        with ThreadPoolExecutor(max_workers=min(len(remaining), 5)) as executor:
            future_map = {executor.submit(fetch_page_text, item["href"]): item["href"] for item in remaining}
            for future in as_completed(future_map):
                href = future_map[future]
                try:
                    page_texts[href] = future.result()
                except Exception as exc:
                    page_texts[href] = f"Ошибка: {exc}"

    parts = ["Результаты веб-исследования:"]
    for index, item in enumerate(merged_results[:pages_to_read], start=1):
        href = item.get("href", "")
        parts.extend(
            [
                f"\n=== Источник {index} ===",
                f"Поисковик: {ENGINE_LABELS.get(item.get('engine', ''), item.get('engine', '') or '-')}",
                f"Заголовок: {item.get('title', '')}",
                f"Ссылка: {href}",
                f"Описание: {item.get('body', '')}",
            ]
        )
        if href and href in page_texts:
            parts.extend(["Текст страницы:", page_texts[href]])

    return truncate_text("\n".join(parts), 22000)
