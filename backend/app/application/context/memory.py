from __future__ import annotations

import copy
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.application.context.rolling_summary import empty_rolling_summary

MAX_LEDGER_ENTRIES = 500
MAX_COMPRESSION_EVENTS = 200


def new_task_context(
    *,
    task_id: str = "",
    chat_id: str = "",
    active_context_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "task_id": task_id or uuid.uuid4().hex,
        "chat_id": chat_id,
        "created_at": now,
        "updated_at": now,
        "active_context_profile": dict(active_context_profile or {}),
        "live_messages": [],
        "rolling_summary": empty_rolling_summary(),
        "final_context_summary": {},
        "task_ledger": [],
        "pinned_items": [],
        "artifacts": [],
        "rag_references": [],
        "compression_events": [],
        "last_context_usage": {},
        "compression_mode": "auto",
    }


def _bounded_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)].rstrip() + " …[truncated]"


def add_ledger_entry(
    ledger: list[dict[str, Any]] | None,
    *,
    entry_type: str,
    action: str,
    reason: str = "",
    result: str = "",
    evidence: str = "",
    files: list[str] | None = None,
    commands: list[str] | None = None,
    errors: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    next_step: str = "",
) -> list[dict[str, Any]]:
    items = [dict(item) for item in (ledger or []) if isinstance(item, dict)]
    next_id = max((int(item.get("step_id") or 0) for item in items), default=0) + 1
    items.append({
        "step_id": next_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": str(entry_type or "tool_call"),
        "action": _bounded_text(action, 500),
        "reason": _bounded_text(reason, 1000),
        "result": _bounded_text(result),
        "evidence": _bounded_text(evidence),
        "files": [str(value) for value in (files or [])][:100],
        "commands": [_bounded_text(value, 1000) for value in (commands or [])][:50],
        "errors": [_bounded_text(value, 1000) for value in (errors or [])][:50],
        "metrics": dict(metrics or {}),
        "next_step": _bounded_text(next_step, 1000),
    })
    return items[-MAX_LEDGER_ENTRIES:]


def unpin_item(pinned_items: list[dict[str, Any]] | None, item_id: str) -> list[dict[str, Any]]:
    return [dict(item) for item in (pinned_items or []) if str(item.get("id") or "") != str(item_id)]


def log_compression_event(
    history: list[dict[str, Any]] | None,
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    items = [dict(item) for item in (history or []) if isinstance(item, dict)]
    payload = dict(event)
    payload.setdefault("compression_id", uuid.uuid4().hex)
    payload.setdefault("timestamp", int(time.time() * 1000))
    items.append(payload)
    return items[-MAX_COMPRESSION_EVENTS:]


def validate_after_compression(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    required_paths = (
        ("rolling_summary", "task_goal"),
        ("active_context_profile", "active_model"),
        ("active_context_profile", "ctx_size"),
        ("active_context_profile", "main_endpoint"),
        ("active_context_profile", "ocr_endpoint"),
        ("active_context_profile", "vision_endpoint"),
        ("active_context_profile", "embedding_endpoint"),
    )
    missing: list[str] = []
    changed: list[str] = []
    for parent, child in required_paths:
        before_value = (before.get(parent) or {}).get(child)
        after_value = (after.get(parent) or {}).get(child)
        if before_value and not after_value:
            missing.append(f"{parent}.{child}")
        elif before_value and after_value != before_value:
            changed.append(f"{parent}.{child}")

    before_pins = {str(item.get("id")) for item in before.get("pinned_items") or []}
    after_pins = {str(item.get("id")) for item in after.get("pinned_items") or []}
    if not before_pins.issubset(after_pins):
        missing.append("pinned_items")
    before_ledger = len(before.get("task_ledger") or [])
    after_ledger = len(after.get("task_ledger") or [])
    if after_ledger < before_ledger:
        missing.append("task_ledger")
    for field in (
        "known_errors", "next_steps", "important_files", "test_results",
        "decisions", "risks",
    ):
        before_values = {str(value) for value in (before.get("rolling_summary") or {}).get(field) or []}
        after_values = {str(value) for value in (after.get("rolling_summary") or {}).get(field) or []}
        if not before_values.issubset(after_values):
            missing.append(f"rolling_summary.{field}")
    return {"ok": not missing and not changed, "missing": missing, "changed": changed}


def rollback_compression(before: dict[str, Any], failed_after: dict[str, Any]) -> dict[str, Any]:
    restored = copy.deepcopy(before)
    restored["compression_rollback"] = {
        "timestamp": int(time.time() * 1000),
        "reason": validate_after_compression(before, failed_after),
    }
    return restored
