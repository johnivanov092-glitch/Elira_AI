"""Structured media attached to an agent answer.

The model chooses *when* to run an image search. This module owns the narrow
runtime contract that turns successful search results into safe, bounded cards
for the UI; model prose is never parsed for image URLs.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

from app.application.web.ssrf_guard import check_ssrf


MAX_ANSWER_MEDIA = 6


def _safe_remote_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url or check_ssrf(url):
        return ""
    return url


def _source_label(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")[:120]
    except Exception:
        return ""


def image_media_from_search_results(
    sources: Iterable[Mapping[str, Any]],
    *,
    limit: int = MAX_ANSWER_MEDIA,
) -> list[dict[str, str]]:
    """Build safe image cards from existing SearXNG/DDG result fields."""
    cards: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    safe_limit = max(1, min(int(limit), MAX_ANSWER_MEDIA))

    for item in sources:
        source_url = _safe_remote_url(item.get("href") or item.get("url"))
        if not source_url:
            continue
        image_url = _safe_remote_url(item.get("thumbnail_src") or item.get("thumbnail"))
        if not image_url:
            image_url = _safe_remote_url(item.get("img_src") or item.get("image"))
        if not image_url:
            continue
        key = (image_url, source_url)
        if key in seen:
            continue
        seen.add(key)
        title = str(item.get("title") or "Изображение").strip()[:240] or "Изображение"
        cards.append({
            "type": "image",
            "url": image_url,
            "source_url": source_url,
            "title": title,
            "source": _source_label(source_url),
        })
        if len(cards) >= safe_limit:
            break
    return cards


def merge_answer_media(
    current: Iterable[Mapping[str, Any]] | None,
    incoming: Iterable[Mapping[str, Any]] | None,
    *,
    limit: int = MAX_ANSWER_MEDIA,
) -> list[dict[str, str]]:
    """Normalize and de-duplicate media collected across multiple tool calls."""
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    safe_limit = max(1, min(int(limit), MAX_ANSWER_MEDIA))
    for item in [*(current or []), *(incoming or [])]:
        if str(item.get("type") or "") != "image":
            continue
        image_url = _safe_remote_url(item.get("url"))
        source_url = _safe_remote_url(item.get("source_url"))
        if not image_url or not source_url:
            continue
        key = (image_url, source_url)
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "type": "image",
            "url": image_url,
            "source_url": source_url,
            "title": str(item.get("title") or "Изображение").strip()[:240] or "Изображение",
            "source": str(item.get("source") or _source_label(source_url)).strip()[:120],
        })
        if len(merged) >= safe_limit:
            break
    return merged
