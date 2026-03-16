from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

from .search_config import (
    BAD_PATH_PARTS,
    COMMUNITY_DOMAINS,
    DOC_PATH_HINTS,
    OFFICIAL_HINTS,
    PREFERRED_DOC_DOMAINS,
)

try:
    from duckduckgo_search import DDGS
except Exception:
    DDGS = None


DEFAULT_SEARCH_ENGINES = ["duckduckgo", "google", "bing", "yandex"]


class WebService:
    def search(self, query: str, max_results: int = 8) -> Dict[str, Any]:
        normalized_query = (query or "").strip()
        preferred_domains = self._preferred_domains(normalized_query)
        engine_links = self._engine_links(normalized_query)
        results = self._run_duckduckgo(normalized_query, max_results=max_results)

        scored: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        seen_urls = set()

        for item in results:
            source = self._normalize_result(item)
            url = source.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            source["score"] = self._score_source(source, normalized_query, preferred_domains)
            source["is_official_candidate"] = self._is_preferred_domain(url, preferred_domains)
            source["is_community"] = self._is_community_domain(url)
            source["rejected_reason"] = self._reject_reason(source, normalized_query, preferred_domains)

            if source["rejected_reason"]:
                rejected.append(source)
            else:
                scored.append(source)

        scored.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        useful = scored[:max_results]
        official = [s for s in useful if s.get("is_official_candidate")]
        community = [s for s in useful if s.get("is_community")]
        warnings: List[str] = []

        if not useful:
            warnings.append("search returned no useful sources")
        if self._is_official_request(normalized_query) and not official:
            warnings.append("official docs were not found in ranked useful sources")

        return {
            "ok": True,
            "status": "ok" if useful else "empty",
            "query": normalized_query,
            "engines": DEFAULT_SEARCH_ENGINES,
            "engine_links": engine_links,
            "preferred_domains": preferred_domains,
            "useful_results_count": len(useful),
            "official_results_count": len(official),
            "community_results_count": len(community),
            "sources": useful,
            "official_sources": official,
            "community_sources": community,
            "rejected_sources": rejected[:10],
            "warnings": warnings,
            "summary": self._build_summary(normalized_query, useful, official, engine_links),
        }

    def _run_duckduckgo(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        if not query or DDGS is None:
            return []
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results * 3))
        except Exception:
            return []

    def _normalize_result(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": item.get("title") or "",
            "url": item.get("href") or item.get("url") or "",
            "snippet": item.get("body") or item.get("snippet") or "",
            "engine": "duckduckgo",
            "engine_label": "DuckDuckGo",
        }

    def _preferred_domains(self, query: str) -> List[str]:
        q = query.lower()
        matches: List[str] = []
        for key, domains in PREFERRED_DOC_DOMAINS.items():
            if key in q:
                matches.extend(domains)
        return list(dict.fromkeys(matches))

    def _engine_links(self, query: str) -> List[Dict[str, Any]]:
        encoded = quote_plus(query)
        return [
            {"name": "google", "label": "Google", "url": f"https://www.google.com/search?q={encoded}"},
            {"name": "bing", "label": "Bing", "url": f"https://www.bing.com/search?q={encoded}"},
            {"name": "yandex", "label": "Yandex", "url": f"https://yandex.kz/search/?text={encoded}"},
            {"name": "duckduckgo", "label": "DuckDuckGo", "url": f"https://duckduckgo.com/?q={encoded}"},
        ]

    def _is_official_request(self, query: str) -> bool:
        q = query.lower()
        return any(h in q for h in OFFICIAL_HINTS)

    def _is_preferred_domain(self, url: str, preferred_domains: List[str]) -> bool:
        host = (urlparse(url).netloc or "").lower()
        return any(domain in host for domain in preferred_domains)

    def _is_community_domain(self, url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        return any(domain in host for domain in COMMUNITY_DOMAINS)

    def _reject_reason(self, source: Dict[str, Any], query: str, preferred_domains: List[str]) -> Optional[str]:
        url = source.get("url", "")
        title = (source.get("title", "") or "").lower()
        snippet = (source.get("snippet", "") or "").lower()
        parsed = urlparse(url)
        path = (parsed.path or "").lower()

        if not parsed.scheme.startswith("http"):
            return "non-http-url"
        if any(bad in path for bad in BAD_PATH_PARTS):
            return "bad-path"
        if self._is_official_request(query) and self._is_community_domain(url):
            return "community-result-for-official-request"

        if preferred_domains and self._is_official_request(query):
            host = (parsed.netloc or "").lower()
            host_ok = any(d in host for d in preferred_domains)
            doc_like = any(h in path for h in DOC_PATH_HINTS) or any(h in title or h in snippet for h in DOC_PATH_HINTS)
            if not host_ok and not doc_like:
                return "not-doc-like-for-official-request"
        return None

    def _score_source(self, source: Dict[str, Any], query: str, preferred_domains: List[str]) -> float:
        url = source.get("url", "")
        title = (source.get("title", "") or "").lower()
        snippet = (source.get("snippet", "") or "").lower()
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
        q = query.lower()

        score = 0.0
        if self._is_preferred_domain(url, preferred_domains):
            score += 100.0
        if any(h in path for h in DOC_PATH_HINTS):
            score += 25.0
        if any(h in title for h in DOC_PATH_HINTS):
            score += 15.0
        if any(h in snippet for h in DOC_PATH_HINTS):
            score += 10.0

        for token in [t for t in q.split() if len(t) > 2]:
            if token in title:
                score += 3.0
            if token in snippet:
                score += 1.0
            if token in host:
                score += 2.0

        if self._is_community_domain(url):
            score -= 35.0
        if host.startswith("github.com") and "/blob/" not in path and "/tree/" not in path and "readme" not in path:
            score -= 15.0

        return score

    def _build_summary(self, query: str, useful: List[Dict[str, Any]], official: List[Dict[str, Any]], engine_links: List[Dict[str, Any]]) -> str:
        if official:
            best = official[0]
            return f"Найдены официальные источники по запросу: {query}. Лучший источник: {best.get('url')}."
        if useful:
            best = useful[0]
            return f"Найдены релевантные источники по запросу: {query}. Лучший источник: {best.get('url')}."
        if engine_links:
            return "Полезные web-результаты не найдены автоматически, доступны ссылки поиска по системам."
        return "Полезные web-результаты не найдены."


_web_service = WebService()

def search_web(query: str, max_results: int = 8):
    return _web_service.search(query, max_results=max_results)

def research_web(query: str, max_results: int = 8):
    result = _web_service.search(query, max_results=max_results)
    return result.get("sources", [])

def research_web_bundle(query: str, max_results: int = 8):
    result = _web_service.search(query, max_results=max_results)
    return {
        "ok": bool(result.get("sources")),
        "query": query,
        "successful_runs": result.get("useful_results_count", 0),
        "sources": result.get("sources", []),
        "warnings": result.get("warnings", []),
        "summary": result.get("summary", ""),
        "engine_links": result.get("engine_links", []),
        "official_sources": result.get("official_sources", []),
    }
