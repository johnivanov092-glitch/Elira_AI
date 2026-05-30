"""DuckDuckGo HTML scraper — lightweight fallback web search for Ollama agent.

Uses raw urllib (no library dependency) to scrape DuckDuckGo HTML results
and optionally fetch page text. Used by the project-brain Ollama agent as a
self-contained web context source independent of the main search stack.
"""
from __future__ import annotations

import html
import re
from urllib import parse as urlparse
from urllib import request as urlrequest


def _clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _search_web(query: str, limit: int = 5) -> list[dict[str, str]]:
    url = f"https://html.duckduckgo.com/html/?q={urlparse.quote_plus(query[:300])}"
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            html_text = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return []
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.S,
    )
    results = []
    for match in pattern.finditer(html_text):
        href = html.unescape(match.group("href"))
        title = _clean_html_text(match.group("title"))
        snippet = _clean_html_text(match.group("snippet"))
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/l/?uddg=" in href:
            parsed = urlparse.urlparse(href)
            query_params = urlparse.parse_qs(parsed.query)
            href = urlparse.unquote(query_params.get("uddg", [href])[0])
        if href.startswith("http"):
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _fetch_web_page_text(url: str) -> str:
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""
            raw = response.read().decode("utf-8", errors="ignore")
            return _clean_html_text(raw)[:6000]
    except Exception:
        return ""


def collect_web_context(query: str) -> list[dict[str, str]]:
    """Search DuckDuckGo and fetch page text for top results."""
    results = _search_web(query, limit=4)
    return [
        {
            "title": item["title"],
            "url": item["url"],
            "snippet": item["snippet"],
            "page_text": _fetch_web_page_text(item["url"]),
        }
        for item in results[:3]
    ]
