from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class CachedStreamHit:
    full_text: str
    done_event: dict[str, Any]


def build_stream_phase_event(
    *,
    phase: str,
    message: str | None = None,
    full_text: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {"token": "", "done": False, "phase": phase}
    if message is not None:
        event["message"] = message
    if full_text is not None:
        event["full_text"] = full_text
    return event


def build_selected_tools_phase_event(selected_tools: list[str]) -> dict[str, Any] | None:
    if "web_search" in selected_tools:
        return build_stream_phase_event(phase="searching", message="Ищу...")
    if selected_tools:
        return build_stream_phase_event(phase="tools", message="Собираю контекст...")
    return None


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


def prepare_cached_stream_hit(
    *,
    cached_text: str,
    raw_user_input: str,
    timeline: list[dict[str, Any]],
    append_timeline_func: Any,
    apply_identity_guard_func: Any,
    apply_provenance_guard_func: Any,
    finalize_stream_success_func: Any,
    history_service: Any,
    run_id: str,
    session_id: str,
    profile_name: str,
    model_name: str,
    route: str,
    temporal: dict[str, Any],
    web_plan: dict[str, Any],
    num_ctx: int,
    agent_id: str,
    source_agent_id: str,
    selected_tools: list[str],
    started_at: float,
    monotonic_now_func: Any,
) -> CachedStreamHit:
    append_timeline_func(timeline, "cache_hit", "Кэш", "ok", "Ответ из кэша")
    identity_guard = apply_identity_guard_func(raw_user_input, cached_text, timeline)
    full_text = identity_guard.get("text", cached_text)
    provenance_guard = apply_provenance_guard_func(raw_user_input, full_text, timeline)
    full_text = provenance_guard.get("text", full_text)
    duration_ms = int((monotonic_now_func() - started_at) * 1000)
    done_event = finalize_stream_success_func(
        history_service=history_service,
        run_id=run_id,
        session_id=session_id,
        profile_name=profile_name,
        model_name=model_name,
        route=route,
        user_input=raw_user_input,
        full_text=full_text,
        tools=[],
        temporal=temporal,
        web_plan=web_plan,
        identity_guard=identity_guard,
        provenance_guard=provenance_guard,
        duration_ms=duration_ms,
        num_ctx=num_ctx,
        agent_id=agent_id,
        source_agent_id=source_agent_id,
        timeline=timeline,
        selected_tools=selected_tools,
        cached=True,
    )
    return CachedStreamHit(full_text=full_text, done_event=done_event)
