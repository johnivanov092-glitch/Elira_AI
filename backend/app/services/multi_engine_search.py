"""
multi_engine_search.py — мульти-поисковый движок v2.

Фиксы:
  • ddgs (новое имя пакета) с fallback на duckduckgo_search
  • Таймаут 5 сек (было 8)
  • По умолчанию 3 быстрых движка (duckduckgo, google, bing)
  • Yandex и Yahoo — опционально
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_TIMEOUT = 5
_MAX_WORKERS = 5

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.9",
}


# ── DuckDuckGo ──────────────────────────────────────────────

def _search_duckduckgo(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    DDGS = None
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        return [{"title": r.get("title",""), "url": r.get("href","") or r.get("url",""), "snippet": r.get("body",""), "engine": "duckduckgo"} for r in raw if r.get("href") or r.get("url")]
    except Exception as e:
        logger.warning(f"DuckDuckGo: {e}")
        return []


# ── Google ──────────────────────────────────────────────────

def _search_google(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    try:
        resp = requests.get(f"https://www.google.com/search?q={quote_plus(query)}&num={max_results}&hl=ru", headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for g in soup.select("div.g, div[data-sokoban-container]"):
            a = g.select_one("a[href]")
            if not a or not a["href"].startswith("http"):
                continue
            h = g.select_one("h3")
            title = h.get_text(strip=True) if h else ""
            snippet = ""
            for sel in ["div.VwiC3b", "span.aCOpRe", "div[data-sncf]", "div.IsZvec"]:
                s = g.select_one(sel)
                if s:
                    snippet = s.get_text(strip=True)
                    break
            if title or snippet:
                results.append({"title": title, "url": a["href"], "snippet": snippet, "engine": "google"})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning(f"Google: {e}")
        return []


# ── Yandex ──────────────────────────────────────────────────

def _search_yandex(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    try:
        resp = requests.get(f"https://yandex.kz/search/?text={quote_plus(query)}&lr=162", headers={**_HEADERS, "User-Agent": _HEADERS["User-Agent"].replace("Chrome", "YaBrowser/24.6.0.0 Chrome")}, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for item in soup.select("li.serp-item, div.serp-item"):
            a = item.select_one("a[href]")
            if not a:
                continue
            href = a.get("href", "")
            if "yandex" in href and "clck" in href:
                parsed = parse_qs(urlparse(href).query)
                href = unquote(parsed.get("url", parsed.get("l", [""]))[0]) if parsed.get("url") or parsed.get("l") else href
            if not href.startswith("http"):
                continue
            title = ""
            for sel in ["h2", "div.OrganicTitle-LinkText", "span.OrganicTitleContentSpan"]:
                t = item.select_one(sel)
                if t:
                    title = t.get_text(strip=True)
                    break
            if not title:
                title = a.get_text(strip=True)
            snippet = ""
            for sel in ["div.OrganicText", "div.text-container", "span.OrganicTextContentSpan"]:
                s = item.select_one(sel)
                if s:
                    snippet = s.get_text(strip=True)
                    break
            if title or snippet:
                results.append({"title": title, "url": href, "snippet": snippet, "engine": "yandex"})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning(f"Yandex: {e}")
        return []


# ── Bing ────────────────────────────────────────────────────

def _search_bing(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    try:
        resp = requests.get(f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}", headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a[href]")
            if not a or not a["href"].startswith("http"):
                continue
            title = a.get_text(strip=True)
            snippet = ""
            p = li.select_one("div.b_caption p, p.b_lineclamp2, p.b_lineclamp3")
            if p:
                snippet = p.get_text(strip=True)
            if title or snippet:
                results.append({"title": title, "url": a["href"], "snippet": snippet, "engine": "bing"})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning(f"Bing: {e}")
        return []


# ── Yahoo ───────────────────────────────────────────────────

def _search_yahoo(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    try:
        resp = requests.get(f"https://search.yahoo.com/search?p={quote_plus(query)}&n={max_results}", headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for item in soup.select("div.algo, div.dd"):
            a = item.select_one("a[href]")
            if not a:
                continue
            href = a.get("href", "")
            if "yahoo.com/RU=" in href or "r.search.yahoo.com" in href:
                m = re.search(r"RU=([^/]+)/", href)
                if m:
                    href = unquote(m.group(1))
            if not href.startswith("http"):
                continue
            h = item.select_one("h3, h3.title a")
            title = h.get_text(strip=True) if h else a.get_text(strip=True)
            snippet = ""
            for sel in ["p.lh-l", "div.compText", "span.fc-falcon"]:
                s = item.select_one(sel)
                if s:
                    snippet = s.get_text(strip=True)
                    break
            if title or snippet:
                results.append({"title": title, "url": href, "snippet": snippet, "engine": "yahoo"})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning(f"Yahoo: {e}")
        return []


# ── Оркестратор ─────────────────────────────────────────────

_ENGINE_REGISTRY = {
    "duckduckgo": _search_duckduckgo,
    "google": _search_google,
    "yandex": _search_yandex,
    "bing": _search_bing,
    "yahoo": _search_yahoo,
}

# По умолчанию 3 быстрых движка — меньше задержка
DEFAULT_ENGINES = ["duckduckgo", "google", "bing"]
ALL_ENGINES = ["duckduckgo", "google", "yandex", "bing", "yahoo"]


def multi_engine_search(
    query: str,
    engines: list[str] | None = None,
    max_results_per_engine: int = 5,
    max_total: int = 12,
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "query": query, "results": [], "engines_used": [], "engines_failed": [], "count": 0}

    engines = engines or DEFAULT_ENGINES
    engines_used = []
    engines_failed = []
    all_results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=min(len(engines), _MAX_WORKERS)) as executor:
        futures = {}
        for name in engines:
            func = _ENGINE_REGISTRY.get(name)
            if func:
                futures[executor.submit(func, query, max_results_per_engine)] = name

        for future in as_completed(futures, timeout=_TIMEOUT + 3):
            name = futures[future]
            try:
                results = future.result(timeout=1)
                if results:
                    all_results.extend(results)
                    engines_used.append(name)
                else:
                    engines_failed.append(name)
            except Exception:
                engines_failed.append(name)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in all_results:
        url = (item.get("url") or "").rstrip("/").lower()
        if url and url not in seen:
            seen.add(url)
            unique.append(item)

    return {
        "ok": bool(unique),
        "query": query,
        "results": unique[:max_total],
        "engines_used": engines_used,
        "engines_failed": engines_failed,
        "count": len(unique[:max_total]),
    }
