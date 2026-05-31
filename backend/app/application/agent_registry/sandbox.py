from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.monitoring.runtime import (
    count_agent_runs_last_hour,
    ensure_agent_limit,
    record_sandbox_block,
)


_PROFILE_AGENT_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("универс", "universal", "general", "default", "обыч"), "builtin-universal"),
    (("исследоват", "research", "researcher"), "builtin-researcher"),
    (("програм", "coder", "developer", "programmer"), "builtin-programmer"),
    (("аналит", "analyst", "analysis"), "builtin-analyst"),
    (("сократ", "socrat", "socratic", "teacher"), "builtin-socrat"),
    (("оркестр", "orchestrator", "planner"), "builtin-orchestrator"),
    (("ревью", "reviewer", "review"), "builtin-reviewer"),
]


def _normalize_tool_names(selected_tools: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: set[str] = set()
    tools: list[str] = []
    for item in selected_tools or []:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        tools.append(name)
    return tools


def resolve_effective_agent_id(
    *,
    agent_id: str | None = None,
    profile_name: str | None = None,
    registry_agent: dict[str, Any] | None = None,
) -> str:
    explicit = str(agent_id or "").strip()
    if explicit:
        return explicit

    registry_id = str((registry_agent or {}).get("id") or "").strip()
    if registry_id:
        return registry_id

    normalized = str(profile_name or "").strip().casefold()
    if normalized:
        for hints, builtin_id in _PROFILE_AGENT_HINTS:
            if any(hint in normalized for hint in hints):
                return builtin_id

    return "builtin-universal"


@dataclass(slots=True)
class SandboxPolicyError(RuntimeError):
    message: str
    agent_id: str
    reason: str
    details: dict[str, Any]

    def __str__(self) -> str:
        return self.message


def _make_error(
    *,
    agent_id: str,
    reason: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> SandboxPolicyError:
    return SandboxPolicyError(
        message=message,
        agent_id=agent_id,
        reason=reason,
        details=dict(details or {}),
    )


def evaluate_preflight(
    *,
    agent_id: str,
    num_ctx: int = 0,
    selected_tools: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    normalized_agent_id = str(agent_id or "").strip() or "builtin-universal"
    limit = ensure_agent_limit(normalized_agent_id)
    tools = _normalize_tool_names(selected_tools)

    max_context_tokens = int(limit.get("max_context_tokens", 0) or 0)
    if max_context_tokens > 0 and int(num_ctx or 0) > max_context_tokens:
        raise _make_error(
            agent_id=normalized_agent_id,
            reason="context_limit_exceeded",
            message=f"Agent sandbox blocked run: context window {int(num_ctx or 0)} exceeds limit {max_context_tokens}.",
            details={
                "num_ctx": int(num_ctx or 0),
                "max_context_tokens": max_context_tokens,
            },
        )

    allowed_tools = {str(item or "").strip() for item in limit.get("allowed_tools", []) if str(item or "").strip()}
    if allowed_tools:
        disallowed = [tool for tool in tools if tool not in allowed_tools]
        if disallowed:
            raise _make_error(
                agent_id=normalized_agent_id,
                reason="tool_not_allowed",
                message=f"Agent sandbox blocked run: tool policy rejected {', '.join(disallowed)}.",
                details={
                    "selected_tools": tools,
                    "disallowed_tools": disallowed,
                    "allowed_tools": sorted(allowed_tools),
                },
            )

    max_runs_per_hour = int(limit.get("max_runs_per_hour", 0) or 0)
    if max_runs_per_hour > 0:
        recent_runs = count_agent_runs_last_hour(normalized_agent_id)
        if recent_runs >= max_runs_per_hour:
            raise _make_error(
                agent_id=normalized_agent_id,
                reason="rate_limit_exceeded",
                message=f"Agent sandbox blocked run: hourly run limit reached ({recent_runs}/{max_runs_per_hour}).",
                details={
                    "recent_runs": recent_runs,
                    "max_runs_per_hour": max_runs_per_hour,
                },
            )

    return {
        "ok": True,
        "agent_id": normalized_agent_id,
        "limit": limit,
        "selected_tools": tools,
    }


def preflight_or_raise(
    *,
    agent_id: str,
    num_ctx: int = 0,
    selected_tools: list[str] | tuple[str, ...] | None = None,
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    route: str = "",
    streaming: bool = False,
) -> dict[str, Any]:
    try:
        result = evaluate_preflight(
            agent_id=agent_id,
            num_ctx=num_ctx,
            selected_tools=selected_tools,
        )
        result["route"] = route
        result["streaming"] = bool(streaming)
        return result
    except SandboxPolicyError as exc:
        details = {
            **exc.details,
            "route": route,
            "streaming": bool(streaming),
        }
        record_sandbox_block(
            agent_id=exc.agent_id,
            reason=exc.reason,
            run_id=run_id,
            workflow_id=workflow_id,
            step_id=step_id,
            details=details,
        )
        raise
