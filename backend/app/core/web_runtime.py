from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List

from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - compatibility fallback
    from duckduckgo_search import DDGS

from .files import truncate_text
from .web_engines import (
    ENGINE_LABELS,
    ENGINE_PRIORITY,
    FINANCE_HIGH_CONFIDENCE_DOMAINS,
    KZ_LOCAL_NEWS_DOMAINS,
    clean_url,
    domain_matches,
    extract_domain,
    search_tavily,
    session,
)


logger = logging.getLogger(__name__)


def result_score(
    item: Dict[str, str],
    *,
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
) -> int:
    preferred = tuple(preferred_domains or ())
    href = item.get("href", "")
    domain = extract_domain(href)
    title = (item.get("title", "") or "").lower()
    body = (item.get("body", "") or "").lower()
    engine = (item.get("engine", "") or "").strip()
    haystack = f"{title} {body}".strip()
    score = 0

    if preferred and domain_matches(domain, preferred):
        score += 120

    if intent_kind == "geo_news":
        if local_first and domain_matches(domain, KZ_LOCAL_NEWS_DOMAINS):
            score += 90
        if engine == "ddg-news":
            score += 35
        if engine == "wikipedia":
            score -= 140
        if geo_scope and geo_scope.lower() in haystack:
            score += 18
        if any(token in haystack for token in ("происшеств", "кримин", "алматы", "астан", "казахстан")):
            score += 16

    elif intent_kind == "finance":
        if domain_matches(domain, FINANCE_HIGH_CONFIDENCE_DOMAINS):
            score += 95
        if engine == "wikipedia":
            score -= 160
        if any(token in haystack for token in ("usd", "kzt", "тенге", "доллар", "курс", "валют")):
            score += 18

    elif intent_kind == "historical":
        if engine == "wikipedia":
            score += 50

    if engine == "tavily":
        score += 8
    elif engine == "duckduckgo":
        score += 4

    return score


def rerank_results(
    results: Iterable[Dict[str, str]],
    *,
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
) -> List[Dict[str, str]]:
    return sorted(
        list(results),
        key=lambda item: (
            -result_score(
                item,
                intent_kind=intent_kind,
                geo_scope=geo_scope,
                local_first=local_first,
                preferred_domains=preferred_domains,
            ),
            ENGINE_PRIORITY.get(str(item.get("engine", "")).strip(), 99),
            str(item.get("title", "")).strip().lower(),
        ),
    )


def count_preferred_domain_hits(
    results: Iterable[Dict[str, str]],
    preferred_domains: Iterable[str] | None = None,
) -> int:
    preferred = tuple(preferred_domains or ())
    if not preferred:
        return 0
    return sum(1 for item in results if domain_matches(extract_domain(item.get("href", "")), preferred))


def dedupe_results(
    results: Iterable[Dict[str, str]],
    max_results: int | None = None,
) -> List[Dict[str, str]]:
    unique: list[Dict[str, str]] = []
    seen = set()
    for item in results:
        href = clean_url(item.get("href", ""))
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
        if max_results is not None and len(unique) >= max_results:
            break
    return unique


def search_news(
    query: str,
    max_results: int = 5,
    *,
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
) -> List[Dict[str, str]]:
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
    deduped = dedupe_results(results, max_results=None)
    reranked = rerank_results(
        deduped,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )
    return reranked[:max_results]


def search_web_runtime(
    query: str,
    *,
    max_results: int = 5,
    engines: Iterable[str] | None = None,
    per_engine: int | None = None,
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
    resolve_search_engines_func: Callable[[Iterable[str] | None], tuple[str, ...]],
    engine_funcs: dict[str, Callable[..., List[Dict[str, str]]]],
    logger_obj: logging.Logger,
) -> List[Dict[str, str]]:
    engine_list = list(resolve_search_engines_func(engines))
    per_engine = per_engine or max(3, max_results)
    combined: list[Dict[str, str]] = []

    for engine in engine_list:
        search_fn = engine_funcs.get(engine)
        if not search_fn:
            continue
        try:
            combined.extend(search_fn(query, max_results=per_engine))
        except Exception as exc:
            logger_obj.warning(
                "web search engine '%s' failed for query %r: %s",
                engine,
                query,
                exc,
            )

    dedupe_limit = max(max_results, per_engine * max(1, len(engine_list)))
    merged = dedupe_results(combined, max_results=dedupe_limit)
    reranked = rerank_results(
        merged,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )
    return reranked[:max_results]


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
        response = session().get(url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\n{2,}", "\n\n", soup.get_text("\n"))
        return truncate_text(text, 10000)
    except Exception as exc:
        return f"Ошибка чтения страницы: {exc}"


def tavily_research(query: str, max_results: int) -> List[Dict[str, str]]:
    try:
        return search_tavily(
            query,
            max_results=max_results,
            search_depth="advanced",
            include_raw_content=True,
        )
    except Exception:
        return []


def research_web_runtime(
    query: str,
    *,
    max_results: int = 5,
    pages_to_read: int = 3,
    engines: Iterable[str] | None = None,
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
    resolve_search_engines_func: Callable[[Iterable[str] | None], tuple[str, ...]],
    search_web_func: Callable[..., List[Dict[str, str]]],
    fetch_page_text_func: Callable[[str], str],
) -> str:
    engine_list = list(resolve_search_engines_func(engines))
    advanced_items: list[Dict[str, str]] = []
    if "tavily" in engine_list:
        advanced_items = tavily_research(query, max_results=min(max_results, pages_to_read))

    merged_results = dedupe_results(
        [
            *advanced_items,
            *search_web_func(
                query,
                max_results=max_results,
                engines=engine_list,
                intent_kind=intent_kind,
                geo_scope=geo_scope,
                local_first=local_first,
                preferred_domains=preferred_domains,
            ),
        ],
        max_results=max(max_results, pages_to_read * max(1, len(engine_list))),
    )
    merged_results = rerank_results(
        merged_results,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )[:max_results]
    to_fetch = [item for item in merged_results[:pages_to_read] if item.get("href")]

    page_texts: Dict[str, str] = {}
    for item in advanced_items:
        if item.get("raw_content"):
            page_texts[item["href"]] = item["raw_content"]

    remaining = [item for item in to_fetch if item.get("href") not in page_texts]
    if remaining:
        with ThreadPoolExecutor(max_workers=min(len(remaining), 5)) as executor:
            future_map = {
                executor.submit(fetch_page_text_func, item["href"]): item["href"]
                for item in remaining
            }
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
