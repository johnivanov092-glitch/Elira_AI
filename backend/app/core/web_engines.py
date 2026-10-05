from __future__ import annotations

import os
from typing import Dict, Iterable, List
from urllib.parse import parse_qs, urlparse

import requests

from .files import truncate_text
from .redaction import redact_text


SUPPORTED_SEARCH_ENGINES = ("searxng",)
DEFAULT_SEARCH_ENGINES = SUPPORTED_SEARCH_ENGINES
CURRENT_WORLD_ENGINES = {"searxng"}
ENGINE_PRIORITY = {
    "searxng": 0,
    "duckduckgo": 1,
    "wikipedia": 2,
}
ENGINE_LABELS = {
    "searxng": "SearXNG",
    "duckduckgo": "DuckDuckGo",
    "wikipedia": "Wikipedia",
    "ddg-news": "DuckDuckGo News",
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


class SearchResults(list[Dict[str, str]]):
    """Keep the list API while carrying upstream diagnostics through ranking."""

    def __init__(self, rows: Iterable[Dict[str, str]] = (), *,
                 engine_warnings: Iterable[Dict[str, str]] = ()):
        super().__init__(rows)
        self.engine_warnings = [dict(warning) for warning in engine_warnings]


class SearchUnavailable(RuntimeError):
    """Transport failure with the same structured diagnostics as partial results."""

    def __init__(self, message: str, *, engine_warnings: Iterable[Dict[str, str]] = ()):
        super().__init__(message)
        self.engine_warnings = [dict(warning) for warning in engine_warnings]


def search_warning_text(warnings: Iterable[Dict[str, str]]) -> str:
    rows = list(warnings)
    if not rows:
        return ""
    summary = "; ".join(f"{item['engine']}: {item['error']}" for item in rows[:3])
    return f"WARNING: incomplete SearXNG engine coverage ({len(rows)} engine failures); {summary}"


def _engine_warnings(payload: dict) -> list[Dict[str, str]]:
    rows = payload.get("unresponsive_engines", [])
    if (not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != 2
            or any(not isinstance(value, str) or not value.strip() for value in row) for row in rows)):
        raise RuntimeError("Invalid SearXNG response: expected unresponsive_engines name/error pairs")
    return [{"engine": redact_text(row[0].strip())[:100], "error": redact_text(row[1].strip())[:240]}
            for row in rows]


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
    # parse_qs unwraps the redirect once. Decoding the destination again would
    # turn escaped delimiters into path/query separators and change its meaning.
    return url


def extract_domain(url: str) -> str:
    try:
        return urlparse(clean_url(url)).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def domain_matches(domain: str, expected: Iterable[str]) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in expected)


def searxng_url() -> str:
    """Base URL of the self-hosted SearXNG metasearch (e.g.
    http://192.168.88.15:8003). Missing configuration fails search explicitly."""
    return os.environ.get("SEARXNG_URL", "").strip().rstrip("/")


def engine_available(engine: str) -> bool:
    return engine == "searxng" and bool(searxng_url())


def resolve_search_engines(engines: Iterable[str] | None = None) -> tuple[str, ...]:
    """The sole client backend; legacy preferences cannot activate adapters."""
    return DEFAULT_SEARCH_ENGINES


def get_web_engine_status() -> dict:
    configured = bool(searxng_url())
    return {
        "supported_engines": list(SUPPORTED_SEARCH_ENGINES),
        "available_engines": ["searxng"] if configured else [],
        "primary_engine": "searxng",
        "fallback_engines": [],
        "api_keys_present": {"searxng": configured},
        "degraded_mode": not configured,
        "warnings": [] if configured else ["SEARXNG_URL not configured; web search is unavailable."],
    }


def _is_cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text or "")


def search_searxng(
    query: str,
    max_results: int = 5,
    *,
    time_range: str | None = None,
    categories: str | None = None,
    pageno: int | None = None,
) -> List[Dict[str, str]]:
    """Query the self-hosted SearXNG metasearch JSON API. SearXNG already
    owns its upstream engine selection; this client uses only that endpoint. Returns snippet-level results (no raw page content — the
    research path fetches full text from the top pages separately).

    Optional tuning (all backward-compatible — omitted means SearXNG default):
      - the query language is auto-set to ``ru`` for Cyrillic queries (better
        Russian relevance) and left to SearXNG otherwise (so English/technical
        queries are not degraded);
      - ``time_range`` (day|week|month|year) filters by recency;
      - ``categories`` (e.g. ``news``) selects engine categories.
    """
    base = searxng_url()
    if not base:
        raise RuntimeError("SEARXNG_URL is not configured")

    params: dict[str, str] = {"q": query, "format": "json"}
    if _is_cyrillic(query):
        params["language"] = "ru"
    if time_range in ("day", "week", "month", "year"):
        params["time_range"] = time_range
    if categories:
        params["categories"] = categories
    if pageno and int(pageno) > 1:
        params["pageno"] = str(int(pageno))   # W6: SearXNG result pagination

    response = session().get(
        f"{base}/search",
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise RuntimeError("Invalid SearXNG response: expected a results array")
    warnings = _engine_warnings(payload)

    results: list[Dict[str, str]] = []
    # SearXNG's leading rows can be unrelated or violate an explicit site
    # constraint. Rank a bounded candidate pool before applying the output cap.
    candidate_limit = max(max_results, min(100, max_results * 4))
    for item in payload.get("results", [])[:candidate_limit]:
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
                **({"date": str(item.get("publishedDate") or item.get("date"))}
                   if item.get("publishedDate") or item.get("date") else {}),
                **({"filter_time_range": time_range} if "time_range" in params else {}),
                **({"filter_categories": categories} if categories else {}),
                **(
                    {"img_src": clean_url(item.get("img_src", ""))}
                    if item.get("img_src")
                    else {}
                ),
                **(
                    {"thumbnail_src": clean_url(item.get("thumbnail_src", ""))}
                    if item.get("thumbnail_src")
                    else {}
                ),
            }
        )
    # The news category may contain only one failing upstream. A single bounded
    # general-category attempt uses the same SearXNG instance and exact query /
    # period. Page N remains page N of the requested category, without fallback.
    if categories == "news" and not (pageno and pageno > 1) and (
        warnings or len(results) < min(max_results, 2)
    ):
        try:
            general = search_searxng(query, max_results=max_results,
                                     time_range=time_range, categories="general")
            warnings.extend(general.engine_warnings)
            seen = {row["href"] for row in results}
            results.extend(row for row in general if row["href"] not in seen)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            warnings.extend(getattr(exc, "engine_warnings", []) or [{
                "engine": "SearXNG general", "error": redact_text(str(exc))[:240],
            }])
        warnings = [dict(pair) for pair in dict.fromkeys(tuple(sorted(row.items())) for row in warnings)]
    if not results and warnings:
        raise SearchUnavailable("SearXNG upstream search failed: " + search_warning_text(warnings),
                                engine_warnings=warnings)
    # Lazy import avoids the runtime/adapter import cycle. All callers use the
    # existing ranker; a category fallback does not get its own retrieval path.
    from .web_runtime import filter_site_results, rerank_results

    ranked = rerank_results(filter_site_results(query, results), query=query)
    return SearchResults(ranked[:max_results], engine_warnings=warnings)


def re_sub_html(snippet: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", snippet)
