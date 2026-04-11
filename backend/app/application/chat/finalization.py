from __future__ import annotations

from typing import Any

from app.application.chat.agent_os import emit_agent_os_event, record_agent_os_monitoring
from app.application.chat.stream_service import build_chat_meta, build_stream_done_event
from app.services.persona_service import observe_dialogue


def finalize_chat_success(
    *,
    history_service: Any,
    run_id: str,
    session_id: str,
    profile_name: str,
    model_name: str,
    route: str,
    user_input: str,
    answer_text: str,
    tools: list[str],
    temporal: dict[str, Any],
    web_plan: dict[str, Any],
    identity_guard: dict[str, Any] | None,
    provenance_guard: dict[str, Any] | None,
    duration_ms: int,
    streaming: bool,
    num_ctx: int,
    agent_id: str,
    source_agent_id: str,
    selected_tools: list[str] | None = None,
    cached: bool = False,
) -> dict[str, Any]:
    persona_meta = observe_dialogue(
        dialog_id=run_id,
        session_id=session_id or run_id,
        profile_name=profile_name,
        model_name=model_name,
        user_input=user_input,
        answer_text=answer_text,
        route=route,
        outcome_ok=True,
    )
    meta = build_chat_meta(
        model_name=model_name,
        profile_name=profile_name,
        route=route,
        tools=tools,
        run_id=run_id,
        temporal=temporal,
        web_plan=web_plan,
        persona=persona_meta,
        identity_guard=identity_guard,
        provenance_guard=provenance_guard,
        cached=cached,
    )
    history_service.finish_run(run_id, {"ok": True, "answer": answer_text, "meta": meta})
    record_agent_os_monitoring(
        agent_id=agent_id,
        run_id=run_id,
        route=route,
        model_name=model_name,
        ok=True,
        duration_ms=duration_ms,
        streaming=streaming,
        num_ctx=num_ctx,
        selected_tools=selected_tools if selected_tools is not None else tools,
    )
    emit_agent_os_event(
        event_type="agent.run.completed",
        source_agent_id=source_agent_id,
        payload={
            "run_id": run_id,
            "profile_name": profile_name,
            "route": route,
            "ok": True,
            "model_used": model_name,
            "duration_ms": duration_ms,
            "session_id": session_id,
            "streaming": streaming,
        },
    )
    return meta


def finalize_stream_success(
    *,
    history_service: Any,
    run_id: str,
    session_id: str,
    profile_name: str,
    model_name: str,
    route: str,
    user_input: str,
    full_text: str,
    tools: list[str],
    temporal: dict[str, Any],
    web_plan: dict[str, Any],
    identity_guard: dict[str, Any] | None,
    provenance_guard: dict[str, Any] | None,
    duration_ms: int,
    num_ctx: int,
    agent_id: str,
    source_agent_id: str,
    timeline: list[dict[str, Any]],
    selected_tools: list[str] | None = None,
    cached: bool = False,
) -> dict[str, Any]:
    meta = finalize_chat_success(
        history_service=history_service,
        run_id=run_id,
        session_id=session_id,
        profile_name=profile_name,
        model_name=model_name,
        route=route,
        user_input=user_input,
        answer_text=full_text,
        tools=tools,
        temporal=temporal,
        web_plan=web_plan,
        identity_guard=identity_guard,
        provenance_guard=provenance_guard,
        duration_ms=duration_ms,
        streaming=True,
        num_ctx=num_ctx,
        agent_id=agent_id,
        source_agent_id=source_agent_id,
        selected_tools=selected_tools,
        cached=cached,
    )
    return build_stream_done_event(full_text=full_text, meta=meta, timeline=timeline)
