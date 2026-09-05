"""Pure provenance records for the existing web tools and run journal.

A record proves where an excerpt came from, never that a model's claim follows
from it. No network, database, model calls, or independent source registry.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from app.core.redaction import redact_text

SOURCE_PATTERN = re.compile(r"\[\[source:([a-zA-Z0-9_-]{1,80})\]\]")
MAX_SOURCES = 128
EXCERPT_CHARS = 1500
_STATUSES = frozenset({"discovered", "fetched", "excerpt", "failed"})


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_ids(text: str) -> list[str]:
    # Quoted examples/code are ordinary text, not citation requests.
    prose = re.sub(r"```[\s\S]*?(?:```|$)|`[^`\n]*`", "", text or "")
    return list(dict.fromkeys(SOURCE_PATTERN.findall(prose)))


def _identity(record: dict[str, Any]) -> str:
    fields = {key: record.get(key) for key in (
        "origin_run_id", "tool", "url", "content_hash", "excerpt_hash",
        "doc_id", "chunk_id", "offset", "status",
    )}
    return "w_" + digest(json.dumps(fields, sort_keys=True, ensure_ascii=False))[:20]


def make_source(
    *, run_id: str, tool: str, url: str, status: str,
    quote: str = "", title: str = "", fetched_at: float | None = None,
    content_hash: str = "", doc_id: str = "", chunk_id: int | None = None,
    offset: int | None = None, quote_verified: bool = False, error: str = "",
) -> dict[str, Any]:
    clean_url = redact_text(str(url or "").strip())[:4096]
    try:
        parsed = urlsplit(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return {}
    except ValueError:
        return {}
    if status not in _STATUSES:
        return {}
    raw_quote = str(quote or "")[:EXCERPT_CHARS]
    safe_quote = redact_text(raw_quote)
    record = {
        "origin_run_id": str(run_id), "tool": str(tool), "url": clean_url,
        "status": status, "title": redact_text(str(title or ""))[:300],
        "fetched_at": fetched_at, "content_hash": str(content_hash),
        "doc_id": str(doc_id), "chunk_id": chunk_id, "offset": offset,
        "quote": safe_quote, "excerpt_hash": digest(safe_quote) if safe_quote else "",
        "quote_verified": bool(quote_verified and safe_quote == raw_quote),
        "presented": False, "claim_support": "not_assessed",
        "error": redact_text(str(error or ""))[:300],
    }
    record["id"] = _identity(record)
    return record


def valid_source(value: Any) -> bool:
    """Reject malformed/modified persisted records instead of rebinding an ID."""
    if not isinstance(value, dict) or not isinstance(value.get("status"), str) or value["status"] not in _STATUSES:
        return False
    if not isinstance(value.get("quote"), str) or len(value["quote"]) > EXCERPT_CHARS:
        return False
    if value.get("excerpt_hash") != (digest(value["quote"]) if value["quote"] else ""):
        return False
    fetched_at = value.get("fetched_at")
    if fetched_at is not None and (type(fetched_at) not in (int, float) or not math.isfinite(fetched_at)):
        return False
    for key in ("chunk_id", "offset"):
        item = value.get(key)
        if item is not None and (type(item) is not int or item < 0):
            return False
    for key, limit in (("origin_run_id", 200), ("tool", 100), ("url", 4096),
                       ("content_hash", 64), ("doc_id", 200), ("title", 300), ("error", 300)):
        if not isinstance(value.get(key), str) or len(value[key]) > limit:
            return False
    if type(value.get("quote_verified")) is not bool or type(value.get("presented")) is not bool:
        return False
    try:
        parsed = urlsplit(value["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return False
    except ValueError:
        return False
    return value.get("id") == _identity(value)


def merge_sources(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        if group is None or isinstance(group, (str, bytes, dict)):
            continue
        try:
            group = iter(group)
        except TypeError:
            continue
        for source in group:
            if valid_source(source):
                merged[source["id"]] = dict(source)
    return list(merged.values())[-MAX_SOURCES:]


def format_source(source: dict[str, Any]) -> str:
    header = f"{source['title'] or source['url']}\nURL: {source['url']}"
    if source["status"] == "excerpt":
        return f"[[source:{source['id']}]] {header}\n{source['quote']}"
    if source["status"] == "failed":
        return f"{header}\nERROR: {source['error'] or 'page unavailable'}"
    if source["status"] == "fetched":
        return f"{header}\ndoc_id={source['doc_id']}; страница сохранена, текст не предъявлен. Используй web_query."
    return f"{header}\nНайдено поиском; страница ещё не прочитана."


def excerpt_sources(*, run_id: str, tool: str, url: str, text: str, fetched_at: float) -> list[dict[str, Any]]:
    body_hash = digest(text)
    return [record for start in range(0, len(text), EXCERPT_CHARS) if (record := make_source(
        run_id=run_id, tool=tool, url=url, status="excerpt", fetched_at=fetched_at,
        content_hash=body_hash, quote=text[start:start + EXCERPT_CHARS],
        offset=start, quote_verified=True,
    ))][:MAX_SOURCES]
