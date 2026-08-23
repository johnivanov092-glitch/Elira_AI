from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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
    session,
)


logger = logging.getLogger(__name__)


# ── Source confidence (epistemic layer) ──────────────────────────────────
# Deterministic, code-side classification of each search result into one of
# three tiers. The LLM only narrates these labels — it never decides them and
# is forbidden (via the prompt) from upgrading a tier or inventing facts. This
# is what keeps "found vs verified" honest instead of leaving it to a 4B model.

# Broadly reputable sources, in addition to the topic-specific high-confidence
# sets in web_engines. Kept deliberately small and conservative.
REPUTABLE_DOMAINS = (
    "wikipedia.org",
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "bloomberg.com",
    "ft.com",
    "nytimes.com",
    "theguardian.com",
    "nature.com",
    "arxiv.org",
    "github.com",
    "stackoverflow.com",
    "who.int",
    "europa.eu",
)

# How recent a dated source must be (days) to count as "fresh".
FRESH_WINDOW_DAYS = 45
SEARCH_ENGINE_DIVERSITY_SCORE_WINDOW = 20

_SEARCH_TOKEN_RE = re.compile(r"[0-9a-zа-яё]{2,}", re.IGNORECASE)

CONFIDENCE_LABELS: Dict[str, str] = {
    "verified": "✅ проверено/актуально",
    "plausible": "🟡 правдоподобно",
    "unverified": "⚠️ не подтверждено",
}


def is_trusted_domain(domain: str) -> bool:
    if not domain:
        return False
    # Government / academic TLDs (incl. country forms like gov.uk, edu.au).
    if domain.endswith((".gov", ".edu", ".int")) or ".gov." in domain or ".edu." in domain:
        return True
    return domain_matches(
        domain,
        REPUTABLE_DOMAINS + FINANCE_HIGH_CONFIDENCE_DOMAINS + KZ_LOCAL_NEWS_DOMAINS,
    )


def parse_result_date(raw: str) -> datetime | None:
    """Best-effort parse of a result's date into an aware UTC datetime.
    Returns None when absent or unparseable (most non-news web pages)."""
    text = (raw or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def classify_confidence(
    item: Dict[str, str],
    *,
    all_results: Iterable[Dict[str, str]] = (),
    now: datetime | None = None,
    fresh_days: int = FRESH_WINDOW_DAYS,
) -> str:
    """Classify one result into 'verified' | 'plausible' | 'unverified'.

    - verified: trusted domain AND (fresh dated OR corroborated by ≥2 trusted domains)
    - plausible: trusted domain, OR corroborated, OR at least has a date
    - unverified: untrusted, undated, uncorroborated
    """
    now = now or datetime.now(timezone.utc)
    trusted = is_trusted_domain(extract_domain(item.get("href", "")))

    parsed = parse_result_date(item.get("date", ""))
    fresh = parsed is not None and 0 <= (now - parsed).days <= fresh_days

    trusted_domains = {
        d
        for r in all_results
        if (d := extract_domain(r.get("href", ""))) and is_trusted_domain(d)
    }
    corroborated = len(trusted_domains) >= 2

    if trusted and (fresh or corroborated):
        return "verified"
    if trusted or corroborated or parsed is not None:
        return "plausible"
    return "unverified"


def result_score(
    item: Dict[str, str],
    *,
    query: str = "",
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

    query_tokens = set(_SEARCH_TOKEN_RE.findall((query or "").casefold()))
    if query_tokens:
        title_tokens = set(_SEARCH_TOKEN_RE.findall(title))
        body_tokens = set(_SEARCH_TOKEN_RE.findall(body))
        matched = query_tokens & (title_tokens | body_tokens)
        title_matched = query_tokens & title_tokens
        coverage = len(matched) / len(query_tokens)
        score += round(coverage * 80)
        score += len(title_matched) * 8
        score += len((query_tokens & body_tokens) - title_matched) * 2
        normalized_query = " ".join(_SEARCH_TOKEN_RE.findall((query or "").casefold()))
        if normalized_query and normalized_query in " ".join(_SEARCH_TOKEN_RE.findall(haystack)):
            score += 40

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

    if engine == "searxng":
        score += 8
    elif engine == "duckduckgo":
        score += 4

    return score


def rerank_results(
    results: Iterable[Dict[str, str]],
    *,
    query: str = "",
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
                query=query,
                intent_kind=intent_kind,
                geo_scope=geo_scope,
                local_first=local_first,
                preferred_domains=preferred_domains,
            ),
            ENGINE_PRIORITY.get(str(item.get("engine", "")).strip(), 99),
            str(item.get("title", "")).strip().lower(),
        ),
    )


def _select_diverse_results(
    results: List[Dict[str, str]],
    *,
    max_results: int,
    query: str = "",
    intent_kind: str = "",
    geo_scope: str = "",
    local_first: bool = False,
    preferred_domains: Iterable[str] | None = None,
) -> List[Dict[str, str]]:
    """Keep competitive fallback engines represented without promoting junk."""
    if max_results <= 1 or len(results) <= 1:
        return results[:max_results]

    def _score(item: Dict[str, str]) -> int:
        return result_score(
            item,
            query=query,
            intent_kind=intent_kind,
            geo_scope=geo_scope,
            local_first=local_first,
            preferred_domains=preferred_domains,
        )

    best_score = _score(results[0])
    competitive_floor = best_score - SEARCH_ENGINE_DIVERSITY_SCORE_WINDOW
    selected: list[Dict[str, str]] = []
    selected_ids: set[int] = set()
    engines_seen: set[str] = set()

    for item in results:
        engine = str(item.get("engine") or "").strip()
        if engine in engines_seen or _score(item) < competitive_floor:
            continue
        selected.append(item)
        selected_ids.add(id(item))
        engines_seen.add(engine)
        if len(selected) >= max_results:
            return selected

    for item in results:
        if id(item) in selected_ids:
            continue
        selected.append(item)
        if len(selected) >= max_results:
            break
    return selected


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
                **{
                    key: str(item.get(key) or "").strip()
                    for key in ("img_src", "thumbnail_src")
                    if item.get(key)
                },
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
    raise_errors: bool = False,
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
        if raise_errors:
            raise
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
    time_range: str | None = None,
    categories: str | None = None,
) -> List[Dict[str, str]]:
    engine_list = list(resolve_search_engines_func(engines))
    per_engine = per_engine or max(3, max_results)
    combined: list[Dict[str, str]] = []
    # SearXNG accepts time_range / categories; DuckDuckGo and Wikipedia do not,
    # so the extras are passed ONLY to SearXNG and ONLY when set (a no-arg engine
    # mock in tests is never handed kwargs it cannot take).
    searxng_extra = {
        k: v for k, v in (("time_range", time_range), ("categories", categories)) if v
    }

    for engine in engine_list:
        search_fn = engine_funcs.get(engine)
        if not search_fn:
            continue
        try:
            if engine == "searxng" and searxng_extra:
                combined.extend(search_fn(query, max_results=per_engine, **searxng_extra))
            elif engine in {"duckduckgo", "wikipedia"} and categories == "images":
                combined.extend(search_fn(query, max_results=per_engine, categories="images"))
            else:
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
        query=query,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )
    return _select_diverse_results(
        reranked,
        max_results=max_results,
        query=query,
        intent_kind=intent_kind,
        geo_scope=geo_scope,
        local_first=local_first,
        preferred_domains=preferred_domains,
    )


def format_search_results(results: List[Dict[str, str]]) -> str:
    materialized = list(results)
    return "\n\n".join(
        f"[{index}] {item.get('title', '')}  — {CONFIDENCE_LABELS[classify_confidence(item, all_results=materialized)]}\n"
        f"Поисковик: {ENGINE_LABELS.get(item.get('engine', ''), item.get('engine', '') or '-')}\n"
        f"Ссылка: {item.get('href', '')}\n"
        f"Описание: {item.get('body', '')}"
        for index, item in enumerate(materialized, start=1)
    )


def fetch_page_text(url: str) -> str:
    from app.application.web.ssrf_guard import check_ssrf
    ssrf_reason = check_ssrf(url)
    if ssrf_reason:
        return f"Ошибка чтения страницы: некорректный URL — {ssrf_reason}"

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
    # SearXNG and DuckDuckGo return snippet-level results only (no raw page
    # content), so there is no advanced fast-path here — full text comes from
    # fetching the top pages below.
    advanced_items: list[Dict[str, str]] = []

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
