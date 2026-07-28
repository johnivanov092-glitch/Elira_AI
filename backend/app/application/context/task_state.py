from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from typing import Any

from app.application.context.memory import (
    add_ledger_entry,
    log_compression_event,
    new_task_context,
    rollback_compression,
    validate_after_compression,
)
from app.application.context.profile import get_active_context_profile
from app.application.context.rolling_summary import generate_rolling_summary, update_rolling_summary
from app.application.context.usage import get_context_usage


def _session_store():
    from app.application.code_agent import sessions

    return sessions


def _normalise_state(session: dict[str, Any]) -> dict[str, Any]:
    raw = session.get("context_state") if isinstance(session.get("context_state"), dict) else {}
    if "rolling_summary" not in raw:
        state = new_task_context(task_id=str(session.get("id") or ""), chat_id=str(session.get("id") or ""))
        if raw.get("ctx_size"):
            state["last_context_usage"] = dict(raw)
    else:
        state = {**new_task_context(task_id=str(session.get("id") or ""), chat_id=str(session.get("id") or "")), **raw}
    state["task_ledger"] = list(session.get("task_ledger") or state.get("task_ledger") or [])
    state["pinned_items"] = list(session.get("pinned_items") or state.get("pinned_items") or [])
    state["compression_events"] = list(session.get("compression_events") or state.get("compression_events") or [])
    return state


def save_task_context(session_id: str, task_context: dict[str, Any]) -> dict[str, Any] | None:
    payload = copy.deepcopy(task_context)
    payload["updated_at"] = int(time.time() * 1000)
    return _session_store().update_session(session_id, {
        "context_state": payload,
        "task_ledger": list(payload.get("task_ledger") or []),
        "pinned_items": list(payload.get("pinned_items") or []),
        "compression_events": list(payload.get("compression_events") or []),
    })


def load_task_context(session_id: str) -> dict[str, Any] | None:
    session = _session_store().get_session(session_id)
    return _normalise_state(session) if session else None


def get_task_context_state(session_id: str) -> dict[str, Any] | None:
    """UI-facing alias for the persisted task context state."""
    return load_task_context(session_id)


def restore_chat_state(session_id: str) -> dict[str, Any] | None:
    session = _session_store().get_session(session_id)
    if not session:
        return None
    return {"turns": list(session.get("turns") or []), "task_context": _normalise_state(session)}


def update_task_context(session_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    current = load_task_context(session_id)
    if current is None:
        return None
    merged = {**current, **copy.deepcopy(patch), "updated_at": int(time.time() * 1000)}
    saved = save_task_context(session_id, merged)
    return _normalise_state(saved) if saved else None


def _turn_messages(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for turn in turns:
        kind = str(turn.get("kind") or "")
        if kind == "user" and str(turn.get("text") or "").strip():
            messages.append({"role": "user", "content": str(turn["text"]), "_msg_id": str(turn.get("id") or "")})
        elif kind == "agent" and str(turn.get("text") or "").strip():
            messages.append({"role": "assistant", "content": str(turn["text"]), "_msg_id": str(turn.get("id") or "")})
    return messages


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compress_now(session_id: str) -> dict[str, Any]:
    session = _session_store().get_session(session_id)
    if not session:
        raise KeyError(session_id)
    before = _normalise_state(session)
    messages = _turn_messages(list(session.get("turns") or []))
    profile = get_active_context_profile(
        str(session.get("model") or "local-model"),
        ctx_size=None,
        fresh=True,
    )
    usage_before = get_context_usage(
        messages,
        ctx_size=int(profile["ctx_size"]),
        reserved_output_tokens=int(profile["reserved_output_tokens"]),
        reserved_system_tokens=int(profile["reserved_system_tokens"]),
        safety_margin_tokens=int(profile["safety_margin_tokens"]),
    )
    generated = generate_rolling_summary(
        messages,
        active_context_profile=profile,
        task_ledger=list(before.get("task_ledger") or []),
    )
    after = copy.deepcopy(before)
    after["active_context_profile"] = profile
    after["rolling_summary"] = update_rolling_summary(before.get("rolling_summary"), generated)
    after["live_messages"] = messages[-8:]
    usage_after = get_context_usage(
        [
            {
                "role": "system",
                "content": json.dumps(after["rolling_summary"], ensure_ascii=False),
                "context_category": "rolling_summary",
            },
            *after["live_messages"],
        ],
        ctx_size=int(profile["ctx_size"]),
        reserved_output_tokens=int(profile["reserved_output_tokens"]),
        reserved_system_tokens=int(profile["reserved_system_tokens"]),
        safety_margin_tokens=int(profile["safety_margin_tokens"]),
    )
    after["last_context_usage"] = usage_after
    after["final_context_summary"] = after["rolling_summary"]

    event = {
        "compression_id": uuid.uuid4().hex,
        "timestamp": int(time.time() * 1000),
        "tokens_before": int(usage_before["current_tokens"]),
        "tokens_after": int(usage_after["current_tokens"]),
        "compression_ratio": 0.0,
        "messages_compressed": max(0, len(messages) - len(after["live_messages"])),
        "blocks_preserved": len(after["live_messages"]) + len(after.get("pinned_items") or []),
        "blocks_summarized": max(0, len(messages) - len(after["live_messages"])),
        "blocks_referenced_as_artifacts": len(after.get("artifacts") or []),
        "rolling_summary_before_hash": _hash(before.get("rolling_summary") or {}),
        "rolling_summary_after_hash": _hash(after["rolling_summary"]),
        "ledger_entries_added": 1,
        "trigger_reason": "manual",
        "validation_ok": True,
    }
    event["compression_ratio"] = round(event["tokens_after"] / max(event["tokens_before"], 1), 4)
    after["task_ledger"] = add_ledger_entry(
        list(after.get("task_ledger") or []),
        entry_type="compression",
        action="Manual context compression",
        result=f"{event['tokens_before']} → {event['tokens_after']} estimated tokens",
        metrics={"tokens_before": event["tokens_before"], "tokens_after": event["tokens_after"]},
    )
    after["compression_events"] = log_compression_event(after.get("compression_events"), event)
    validation = validate_after_compression(before, after)
    event["validation_ok"] = bool(validation["ok"])
    if not validation["ok"]:
        return {
            "ok": False,
            "reason": validation,
            "task_context": rollback_compression(before, after),
            "event": event,
        }
    save_task_context(session_id, after)
    return {"ok": True, "task_context": after, "event": event}
