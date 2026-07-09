"""W4-lite — bounded sitemap discovery (NO BFS link-following).

Discovers URLs from a site's sitemap.xml so the model can selectively
web_fetch(store) the ones it wants — the low-risk slice of "crawl a site" without
following links. Hard bounds (contract §4): GET only; SSRF re-checked on EVERY
URL and EVERY redirect; the discovery NEVER leaves the requested registrable
domain (eTLD+1); robots.txt respected; per-call limits on URLs, bytes and time.

This tool ONLY discovers (returns loc + lastmod). Ingestion stays the model's
choice via web_fetch(store) — discovery and ingestion are separate.
"""
from __future__ import annotations

import re
import time
import html as _html
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from app.application.web_evidence.freshness import registrable_domain

_MAX_URLS = 50
_MAX_SITEMAP_BYTES = 4 * 1024 * 1024
_MAX_CHILD_SITEMAPS = 5      # a sitemapindex fans out to child sitemaps — bounded
_MAX_REDIRECTS = 5
_TIME_BUDGET_S = 20.0
_UA = "EliraBot"

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
# pair <url><loc>..</loc><lastmod>..</lastmod></url> loosely (order-tolerant)
_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.IGNORECASE | re.DOTALL)
_LASTMOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)", re.IGNORECASE)
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _get(
    url: str,
    *,
    deadline: float,
    max_bytes: int = _MAX_SITEMAP_BYTES,
    site_domain: str | None = None,
) -> dict:
    """GET with manual redirects + per-hop SSRF re-check (contract §4). Returns
    {ok, final_url, text} or {ok:False, error}. text-ish only."""
    import requests
    from app.application.web.ssrf_guard import check_ssrf
    from app.application.code_agent.tools._run import active_server_ports
    from app.application.web_evidence.politeness import politeness_wait
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        if time.monotonic() > deadline:
            return {"ok": False, "error": "time budget exceeded"}
        reason = check_ssrf(current, allow_loopback_ports=active_server_ports())
        if reason:
            return {"ok": False, "error": f"SSRF blocked — {reason}"}
        # W6 politeness, deadline-aware: a polite wait that would overshoot the
        # budget aborts instead of sleeping; the request gets the REMAINING
        # budget (capped at 10s), so the 20s discovery budget holds for real.
        if politeness_wait(current, deadline=deadline) is None:
            return {"ok": False, "error": "time budget exceeded"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"ok": False, "error": "time budget exceeded"}
        try:
            resp = requests.get(current, timeout=min(10.0, remaining), allow_redirects=False,
                                stream=True,
                                headers={"User-Agent": _UA, "Accept": "application/xml,text/plain"})
        except requests.RequestException as exc:
            return {"ok": False, "error": f"fetch failed: {exc}"}
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location") or ""
            resp.close()
            if not loc:
                return {"ok": False, "error": "redirect without Location"}
            nxt = urljoin(current, loc)     # SSRF re-checked at loop top
            if site_domain and registrable_domain(nxt) != site_domain:
                return {"ok": False, "error": "off-domain redirect"}
            current = nxt
            continue
        if resp.status_code != 200:
            resp.close()
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        body = resp.raw.read(max_bytes + 1, decode_content=True)
        resp.close()
        if len(body) > max_bytes:
            return {"ok": False, "error": "sitemap exceeds size cap"}
        return {"ok": True, "final_url": resp.url or current,
                "text": body.decode("utf-8", errors="replace")}
    return {"ok": False, "error": "too many redirects"}


def _robots(base: str, deadline: float, site_domain: str) -> RobotFileParser:
    """robots.txt parser for the site (SSRF-checked fetch). Fail-open to ALLOW —
    standard behaviour when robots is unreachable."""
    rp = RobotFileParser()
    parsed = urlparse(base)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    got = _get(robots_url, deadline=deadline, max_bytes=512 * 1024, site_domain=site_domain)
    if got.get("ok"):
        rp.parse(got["text"].splitlines())
    else:
        rp.allow_all = True
    return rp


def _sitemaps_from_robots(text: str) -> list[str]:
    return [m.group(1).strip() for m in
            re.finditer(r"(?im)^\s*sitemap:\s*(\S+)", text or "")]


def _resolve_sitemap_urls(url: str, deadline: float, site_domain: str | None = None) -> tuple[list[str], str]:
    """Where the sitemap(s) live: an explicit .xml, else robots.txt Sitemap:
    directives, else /sitemap.xml. Returns (sitemap_urls, note)."""
    if url.rstrip("/").lower().endswith(".xml"):
        return [url], "явный sitemap"
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    got = _get(robots_url, deadline=deadline, max_bytes=512 * 1024, site_domain=site_domain)
    if got.get("ok"):
        sms = _sitemaps_from_robots(got["text"])
        if sms:
            return sms, "из robots.txt"
    return [f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"], "по умолчанию /sitemap.xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if _local(str(child.tag)) == name:
            return (child.text or "").strip()
    return ""


def _parse_loose(xml: str) -> tuple[list[dict], list[str]]:
    """Best-effort fallback for malformed sitemap XML."""
    if re.search(r"<sitemapindex", xml, re.IGNORECASE):
        return [], [_html.unescape(u.strip()) for u in _LOC_RE.findall(xml)]
    entries = []
    for block in _URL_BLOCK_RE.findall(xml):
        loc = _LOC_RE.search(block)
        if not loc:
            continue
        lm = _LASTMOD_RE.search(block)
        iso = _ISO_RE.search(lm.group(1)) if lm else None
        entries.append({"loc": _html.unescape(loc.group(1).strip()), "lastmod": iso.group(0) if iso else None})
    if not entries:   # flat <loc> list without <url> wrappers
        entries = [{"loc": _html.unescape(u.strip()), "lastmod": None} for u in _LOC_RE.findall(xml)]
    return entries, []


def _parse(xml: str) -> tuple[list[dict], list[str]]:
    """Parse a sitemap: (url entries, child sitemap urls). urlset → entries;
    sitemapindex → children."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return _parse_loose(xml)
    if _local(str(root.tag)) == "sitemapindex":
        children = []
        for node in root.iter():
            if _local(str(node.tag)) == "sitemap":
                loc = _child_text(node, "loc")
                if loc:
                    children.append(loc)
        return [], children
    entries = []
    for node in root.iter():
        if _local(str(node.tag)) != "url":
            continue
        loc = _child_text(node, "loc")
        if not loc:
            continue
        lm = _child_text(node, "lastmod")
        iso = _ISO_RE.search(lm) if lm else None
        entries.append({"loc": loc, "lastmod": iso.group(0) if iso else None})
    if not entries:   # flat <loc> list without <url> wrappers
        entries = [{"loc": u.strip(), "lastmod": None} for u in _LOC_RE.findall(xml)]
    return entries, []


def discover(url: str, *, max_urls: int = _MAX_URLS,
             contains: str = "") -> dict:
    """Bounded sitemap discovery. Returns {ok, sitemap, urls:[{loc,lastmod}],
    note, skipped}. NEVER leaves the requested registrable domain; robots-
    respected; SSRF-checked; time/URL/byte-bounded."""
    max_urls = max(1, min(int(max_urls), _MAX_URLS))
    deadline = time.monotonic() + _TIME_BUDGET_S
    site_domain = registrable_domain(url)
    if not site_domain:
        return {"ok": False, "error": "не удалось определить домен"}

    sitemap_urls, note = _resolve_sitemap_urls(url, deadline, site_domain=site_domain)
    robots = _robots(url, deadline, site_domain)
    seen: set[str] = set()
    out: list[dict] = []
    skipped = {"off_domain": 0, "robots": 0, "dup": 0}
    queue = list(sitemap_urls)
    children_used = 0
    used_sitemap = sitemap_urls[0] if sitemap_urls else ""

    while queue and len(out) < max_urls and time.monotonic() < deadline:
        sm = queue.pop(0)
        if registrable_domain(sm) != site_domain:      # a sitemap off-domain is ignored
            continue
        got = _get(sm, deadline=deadline, site_domain=site_domain)
        if not got.get("ok"):
            continue
        entries, children = _parse(got["text"])
        for ch in children:
            if children_used < _MAX_CHILD_SITEMAPS and registrable_domain(ch) == site_domain:
                queue.append(ch)
                children_used += 1
        for e in entries:
            loc = e["loc"]
            if not loc.startswith(("http://", "https://")):
                continue
            if registrable_domain(loc) != site_domain:
                skipped["off_domain"] += 1
                continue
            if loc in seen:
                skipped["dup"] += 1
                continue
            if contains and contains.lower() not in loc.lower():
                continue
            try:
                if not robots.can_fetch(_UA, loc):
                    skipped["robots"] += 1
                    continue
            except Exception:
                pass
            seen.add(loc)
            out.append(e)
            if len(out) >= max_urls:
                break

    return {"ok": True, "sitemap": used_sitemap, "note": note,
            "urls": out, "count": len(out),
            "skipped": {k: v for k, v in skipped.items() if v}}
