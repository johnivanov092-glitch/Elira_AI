"""Pure helpers for source domains and dates captured at web ingest."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Multi-part public suffixes we resolve without the full PSL (bounded heuristic —
# invariant №9 says the domain check is an APPROXIMATION of independence).
_MULTI_TLDS = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "com.au", "org.au", "gov.au",
    "com.br", "com.cn", "com.tr", "co.in", "co.kr", "co.nz", "com.mx", "com.ua",
    "gov.ru", "org.ru", "com.sg", "co.za",
}


def registrable_domain(url: str) -> str:
    """eTLD+1 approximation: the registrable domain of a URL. Used to judge whether
    two evidence sources are INDEPENDENT (different registrable domains)."""
    host = (urlparse(url or "").hostname or "").lower().strip(".")
    if not host or host.replace(".", "").isdigit():
        return host                      # bare IP → itself
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    if last2 in _MULTI_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])      # e.g. bbc.co.uk
    return last2


_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# meta names/properties mapped to their date KIND (John's W5 review: dateModified
# is a MODIFIED date, not published; attribute order must not matter).
# og:-prefixed article times are a common real-world variant (live case: django
# weblog uses og:article:published_time) — same meaning, same kind.
_PUBLISHED_KEYS = {"article:published_time", "og:article:published_time", "date",
                   "dc.date", "datepublished", "publishdate", "pubdate", "sailthru.date"}
_MODIFIED_KEYS = {"article:modified_time", "og:article:modified_time", "last-modified",
                  "og:updated_time", "datemodified", "lastmod", "revised"}


_RU_MONTHS = {
    "янв": 1, "января": 1, "фев": 2, "февраля": 2, "мар": 3, "марта": 3,
    "апр": 4, "апреля": 4, "май": 5, "мая": 5, "июн": 6, "июня": 6,
    "июл": 7, "июля": 7, "авг": 8, "августа": 8, "сен": 9, "сентября": 9,
    "окт": 10, "октября": 10, "ноя": 11, "ноября": 11, "дек": 12, "декабря": 12,
}


def _parse_article_date(value: str) -> str:
    from datetime import date

    value = " ".join(str(value or "").split())
    iso = _ISO_RE.match(value)
    try:
        if iso:
            return date.fromisoformat(iso.group(0)).isoformat()
        match = re.fullmatch(
            r"(?:[А-Яа-яЁё]+,\s*)?(\d{1,2})\s+([А-Яа-яЁё]+)\.?\s+(\d{4})"
            r"(?:,?\s+\d{1,2}:\d{2}(?::\d{2})?)?", value,
        )
        if match:
            month = _RU_MONTHS.get(match.group(2).casefold())
            if month:
                return date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        pass
    return ""


def _article_published_date(soup) -> str:
    """Read explicitly typed article dates; never scan page-wide date strings."""
    import json

    candidates: set[str] = set()
    for tag in soup.select('[itemprop="datePublished"], time[pubdate], .post_news__date'):
        if tag.find_parent(["footer", "nav", "aside"]):
            continue
        value = tag.get("content") or tag.get("datetime") or tag.get_text(" ", strip=True)
        parsed = _parse_article_date(value)
        if parsed:
            candidates.add(parsed)
    for script in soup.find_all("script", attrs={"type": "application/ld+json"})[:16]:
        if script.find_parent(["footer", "nav", "aside"]):
            continue
        raw = script.string or script.get_text()
        if not raw or len(raw) > 131072:
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for obj in objects[:64]:
            if not isinstance(obj, dict):
                continue
            graph = obj.get("@graph")
            nodes = [obj] + (graph[:64] if isinstance(graph, list) else [])
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                kinds = node.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if not isinstance(kinds, list) or "NewsArticle" not in kinds:
                    continue
                parsed = _parse_article_date(node.get("datePublished", ""))
                if parsed:
                    candidates.add(parsed)
    return next(iter(candidates)) if len(candidates) == 1 else ""


def extract_dates(html: str, last_modified_header: str | None) -> dict:
    """Best-effort source dates: article/date meta tags in HTML + the HTTP
    Last-Modified header. Returns {"published"?, "modified"?} as ISO strings.
    Fail-open — no date is a normal, honest state (rendered as 'дата неизвестна').

    Parsed via BeautifulSoup so attribute ORDER is irrelevant (content-before-name
    is common on real pages) and published vs modified are typed correctly."""
    out: dict[str, str] = {}
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup.find_all("meta"):
            key = str(tag.get("property") or tag.get("name") or "").strip().lower()
            content = str(tag.get("content") or "")
            iso = _ISO_RE.search(content)
            if not key or not iso:
                continue
            if key in _MODIFIED_KEYS:
                out.setdefault("modified", iso.group(0))
            elif key in _PUBLISHED_KEYS:
                out.setdefault("published", iso.group(0))
        if "published" not in out:
            published = _article_published_date(soup)
            if published:
                out["published"] = published
    except Exception:
        pass   # fail-open: no dates is honest
    if last_modified_header:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(last_modified_header)
            if dt:
                out.setdefault("modified", dt.date().isoformat())  # meta wins over header
        except (TypeError, ValueError):
            pass
    return out
