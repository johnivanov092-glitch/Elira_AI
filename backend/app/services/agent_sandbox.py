"""Agent sandbox compatibility facade."""

from __future__ import annotations

from app.application.agent_registry.sandbox import (
    SandboxPolicyError,
    _PROFILE_AGENT_HINTS,
    _make_error,
    _normalize_tool_names,
    evaluate_preflight,
    preflight_or_raise,
    resolve_effective_agent_id,
)

__all__ = [
    "SandboxPolicyError",
    "_PROFILE_AGENT_HINTS",
    "_make_error",
    "_normalize_tool_names",
    "evaluate_preflight",
    "preflight_or_raise",
    "resolve_effective_agent_id",
]
