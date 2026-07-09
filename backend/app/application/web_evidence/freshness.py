"""W5 freshness + source-independence, layered on the ledger.

Two DETERMINISTIC signals over the corpus/ledger, plus one advisory:

  * corroboration — how many INDEPENDENT sources back a claim, approximated by
    distinct registrable domains (eTLD+1) across its VERIFIED evidence (invariant
    №9). Single-source claims are flagged honestly; ≥2 independent domains =
    corroborated. Deterministic.
  * freshness — the source's own date (published/modified meta, or the
    Last-Modified header) captured at ingest; a source older than STALE_YEARS is
    flagged as possibly outdated. The DATE is deterministic; "stale" is a
    threshold heuristic and shown as a caution, not a verdict.
  * conflict — ADVISORY: the model marks a claim `conflicted` via web_claim_add
    (invariant №8, NLI-class is never a runtime verdict). Surfaced in a dedicated
    section.

No network, no new provider — pure functions over stored metadata.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

STALE_YEARS = 3

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


_META_DATE_RE = re.compile(
    r'<meta[^>]+(?:property|name)\s*=\s*["\'](?:article:published_time|article:modified_time'
    r'|date|dc\.date|last-modified|og:updated_time|datePublished|dateModified)["\'][^>]*'
    r'content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE)
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def extract_dates(html: str, last_modified_header: str | None) -> dict:
    """Best-effort source dates: article/date meta tags in HTML + the HTTP
    Last-Modified header. Returns {"published"?, "modified"?} as ISO strings.
    Fail-open — no date is a normal, honest state (rendered as 'дата неизвестна')."""
    out: dict[str, str] = {}
    for m in _META_DATE_RE.finditer(html or ""):
        iso = _ISO_RE.search(m.group(1))
        if iso:
            out.setdefault("published", iso.group(0))
    if last_modified_header:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(last_modified_header)
            if dt:
                out["modified"] = dt.date().isoformat()
        except (TypeError, ValueError):
            pass
    return out


def _source_date(dates: dict) -> str | None:
    return (dates or {}).get("modified") or (dates or {}).get("published")


def is_stale(dates: dict, *, now: datetime | None = None) -> bool:
    """True when the source's newest known date is older than STALE_YEARS. Unknown
    date → NOT stale (we don't punish missing metadata; we just say 'дата неизвестна')."""
    d = _source_date(dates)
    if not d:
        return False
    try:
        when = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    ref = now or datetime.now(timezone.utc)
    return (ref - when).days > STALE_YEARS * 365


def freshness_note(dates: dict, *, now: datetime | None = None) -> str:
    """Human freshness annotation for a source line."""
    d = _source_date(dates)
    if not d:
        return "дата неизвестна"
    return (f"⚠ возможно устарел, {d}" if is_stale(dates, now=now) else f"дата {d}")


def corroboration(verified_domains: set[str]) -> tuple[str, str]:
    """(level, note) from the set of INDEPENDENT domains backing a claim's VERIFIED
    evidence. level ∈ {none, single, multi}."""
    n = len(verified_domains)
    if n == 0:
        return "none", "нет подтверждённого источника"
    if n == 1:
        return "single", f"один источник ({next(iter(verified_domains))}) — не перекрёстно подтверждено"
    return "multi", f"перекрёстно: {n} независимых домена ({', '.join(sorted(verified_domains))})"
