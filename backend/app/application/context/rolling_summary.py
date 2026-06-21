from __future__ import annotations

import re
from typing import Any


ROLLING_SUMMARY_FIELDS = (
    "task_goal",
    "current_state",
    "decisions",
    "active_models",
    "active_endpoints",
    "server_profile",
    "completed_steps",
    "open_issues",
    "known_errors",
    "important_files",
    "test_results",
    "next_steps",
    "risks",
)

_URL_RE = re.compile(r"https?://[^\s)\]>'\"]+")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\r\n<>|\"]+")
_UNIX_PATH_RE = re.compile(r"(?<!\w)/(?:[\w.-]+/)+[\w.-]+")


def empty_rolling_summary() -> dict[str, Any]:
    return {
        "task_goal": "",
        "current_state": "",
        "decisions": [],
        "active_models": [],
        "active_endpoints": [],
        "server_profile": {},
        "completed_steps": [],
        "open_issues": [],
        "known_errors": [],
        "important_files": [],
        "test_results": [],
        "next_steps": [],
        "risks": [],
    }


def _unique(values: list[Any], limit: int = 100) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out[-limit:]


def _message_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def generate_rolling_summary(
    messages: list[dict[str, Any]],
    *,
    active_context_profile: dict[str, Any] | None = None,
    task_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = empty_rolling_summary()
    user_messages = [str(m.get("content") or "").strip() for m in messages if m.get("role") == "user"]
    summary["task_goal"] = next((text for text in reversed(user_messages) if text), "")[:2000]
    profile = dict(active_context_profile or {})
    summary["server_profile"] = profile
    summary["active_models"] = _unique([profile.get("active_model"), profile.get("model_alias")])
    summary["active_endpoints"] = _unique([
        profile.get("main_endpoint"),
        profile.get("embedding_endpoint"),
        profile.get("ocr_endpoint"),
        profile.get("vision_endpoint"),
    ])

    text = _message_text(messages)
    summary["important_files"] = _unique(_WINDOWS_PATH_RE.findall(text) + _UNIX_PATH_RE.findall(text))
    summary["active_endpoints"] = _unique(summary["active_endpoints"] + _URL_RE.findall(text))

    ledger = list(task_ledger or [])
    for entry in ledger:
        entry_type = str(entry.get("type") or "")
        result = str(entry.get("result") or "").strip()
        action = str(entry.get("action") or "").strip()
        evidence = str(entry.get("evidence") or "").strip()
        if entry_type == "decision":
            summary["decisions"].append(result or action)
        elif entry_type in {"test", "verification"}:
            summary["test_results"].append(result or evidence or action)
        elif entry_type == "error":
            summary["known_errors"].append(result or action)
        elif entry_type in {"tool_call", "file_change", "config_change", "compression", "final"}:
            summary["completed_steps"].append(action or result)
        summary["important_files"].extend(str(value) for value in (entry.get("files") or []))
        next_step = str(entry.get("next_step") or "").strip()
        if next_step:
            summary["next_steps"].append(next_step)
        for error in entry.get("errors") or []:
            summary["known_errors"].append(str(error))

    for key in (
        "decisions", "completed_steps", "open_issues", "known_errors",
        "important_files", "test_results", "next_steps", "risks",
    ):
        summary[key] = _unique(summary[key])
    summary["current_state"] = summary["completed_steps"][-1] if summary["completed_steps"] else ""
    return summary


def update_rolling_summary(
    existing: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    merged = empty_rolling_summary()
    current = dict(existing or {})
    for field in ROLLING_SUMMARY_FIELDS:
        if field == "server_profile":
            merged[field] = {**dict(current.get(field) or {}), **dict(update.get(field) or {})}
        elif isinstance(merged[field], list):
            merged[field] = _unique(list(current.get(field) or []) + list(update.get(field) or []))
        else:
            merged[field] = str(update.get(field) or current.get(field) or "")
    return merged


def validate_rolling_summary(summary: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in ROLLING_SUMMARY_FIELDS if field not in summary]
    invalid_lists = [
        field for field in ROLLING_SUMMARY_FIELDS
        if isinstance(empty_rolling_summary()[field], list) and not isinstance(summary.get(field), list)
    ]
    return {"ok": not missing and not invalid_lists, "missing": missing, "invalid_lists": invalid_lists}
