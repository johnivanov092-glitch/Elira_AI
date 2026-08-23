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
