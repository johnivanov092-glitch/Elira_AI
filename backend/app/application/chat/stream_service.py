"""Chat stream response helpers.

Extracted from agents_service.py — shared meta construction,
cached-token streaming, and stream completion payload assembly.
"""
from __future__ import annotations

from typing import Any, Iterator


def build_chat_meta(
    *,
    model_name: str,
    profile_name: str,
    route: str,
    tools: list[str],
    run_id: str,
    temporal: dict[str, Any],
    web_plan: dict[str, Any],
    persona: dict[str, Any] | None = None,
    identity_guard: dict[str, Any] | None = None,
    provenance_guard: dict[str, Any] | None = None,
    cached: bool = False,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "model_name": model_name,
        "profile_name": profile_name,
        "route": route,
        "tools": tools,
        "run_id": run_id,
        "temporal": temporal,
        "web_plan": web_plan,
        "identity_guard": identity_guard if (identity_guard or {}).get("changed") else None,
        "provenance_guard": provenance_guard if (provenance_guard or {}).get("changed") else None,
    }
    if persona is not None:
        meta["persona"] = persona
    if cached:
        meta["cached"] = True
    return meta


def iter_text_stream_events(text: str) -> Iterator[dict[str, Any]]:
    words = text.split(" ")
    for index, word in enumerate(words):
        yield {"token": word if index == 0 else " " + word, "done": False}


def build_stream_done_event(
    *,
    full_text: str,
    meta: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "token": "",
        "done": True,
        "full_text": full_text,
        "meta": meta,
        "timeline": timeline,
    }
