"""D3 — Full structured action envelopes (gated, OFF by default).

A typed contract for what a model turn may be: a *plan*, a single *action*,
a *tool-request*, a *tool-result*, a *final-result*, or a *blocker*. The
agent loop already recovers tool calls that local models emit as inline
JSON/XML (see ``inline_tool_calls.py``); this module sits one layer above
that as an *opt-in* strict validator with a deterministic fallback.

Design constraints (DEFERRED_TRACK.md → D3):
  * Pydantic schemas for plan / action / tool-request / tool-result /
    final-result / blocker.
  * Strict parse → at most ONE repair retry → deterministic fallback.
  * A fast chat path with no mandatory JSON.

The "fast chat path" is the *default*: the whole envelope layer is gated
behind ``ELIRA_ACTION_ENVELOPES`` (off by default — same pattern as the D1
remote-MCP and D2 LSP flags). When the flag is off, ``envelopes_enabled()``
returns False and the agent loop's hot path is byte-for-byte unchanged, so
conversational turns never pay for JSON validation. When on, the loop runs
recovered tool calls through ``validate_tool_request`` and, on a malformed
envelope, asks the model to re-send *once* before falling back to the
existing inline-recovery behaviour.

This is a leaf module: it imports nothing from ``agent_loop`` (so it stays
unit-testable in isolation and introduces no import cycle).
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.application.code_agent.inline_tool_calls import _iter_json_object_substrings
from app.application.feature_flags import flag_enabled


# ── Gate ────────────────────────────────────────────────────────────────
# OFF by default. The envelope layer only engages when the operator opts in;
# the conversational/chat hot path is unaffected otherwise. The flag is
# resolved through the shared feature-flags layer: an explicit
# ``ELIRA_ACTION_ENVELOPES`` env override still wins, otherwise the persisted
# (UI-toggleable) ``data/feature_flags.json`` value is used.


def envelopes_enabled() -> bool:
    return flag_enabled("action_envelopes")


# ── Envelope schemas ────────────────────────────────────────────────────
# Each turn the model may emit exactly one of these. ``kind`` is the
# discriminator. Unknown fields are rejected (extra="forbid") so a typo or a
# hallucinated shape fails the strict parse and routes to repair/fallback
# rather than silently passing a half-formed action through.


class PlanEnvelope(BaseModel):
    """A short ordered plan before acting. Advisory — never executed."""

    model_config = {"extra": "forbid"}
    kind: Literal["plan"] = "plan"
    steps: list[str] = Field(..., min_length=1, description="Ordered plan steps")


class ToolRequestEnvelope(BaseModel):
    """A request to run one tool. The only envelope that drives execution."""

    model_config = {"extra": "forbid"}
    kind: Literal["tool_request"] = "tool_request"
    tool: str = Field(..., min_length=1, description="Tool name")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ActionEnvelope(BaseModel):
    """A free-form single action with optional structured tool request.

    Lets a model narrate one action and (optionally) attach the concrete
    tool call that performs it. When ``tool`` is present this is treated
    like a tool-request for dispatch; when absent it is just a thought.
    """

    model_config = {"extra": "forbid"}
    kind: Literal["action"] = "action"
    intent: str = Field(..., min_length=1, description="What this action does")
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultEnvelope(BaseModel):
    """A tool-result the model is reporting back (mirrors what the loop
    feeds in). Validated mostly so a model can be asked to echo results in
    a stable shape; the loop produces the authoritative result itself."""

    model_config = {"extra": "forbid"}
    kind: Literal["tool_result"] = "tool_result"
    tool: str = Field(..., min_length=1)
    ok: bool = True
    output: str = ""


class FinalResultEnvelope(BaseModel):
    """The terminal answer for the turn."""

    model_config = {"extra": "forbid"}
    kind: Literal["final_result"] = "final_result"
    text: str = Field(..., min_length=1, description="Answer to the user")


class BlockerEnvelope(BaseModel):
    """The model cannot proceed and is reporting why (missing input, denied
    permission, ambiguous request). Terminal, like final-result."""

    model_config = {"extra": "forbid"}
    kind: Literal["blocker"] = "blocker"
    reason: str = Field(..., min_length=1, description="Why progress is blocked")
    needs: list[str] = Field(default_factory=list, description="What would unblock")


Envelope = (
    PlanEnvelope
    | ToolRequestEnvelope
    | ActionEnvelope
    | ToolResultEnvelope
    | FinalResultEnvelope
    | BlockerEnvelope
)

_ENVELOPE_BY_KIND: dict[str, type[BaseModel]] = {
    "plan": PlanEnvelope,
    "tool_request": ToolRequestEnvelope,
    "action": ActionEnvelope,
    "tool_result": ToolResultEnvelope,
    "final_result": FinalResultEnvelope,
    "blocker": BlockerEnvelope,
}


# ── Strict parse ────────────────────────────────────────────────────────


def parse_envelope(raw: Any) -> BaseModel | None:
    """Strictly parse one envelope from a dict or a JSON string.

    Returns the validated model, or None if `raw` is not a single
    well-formed envelope (missing/unknown ``kind``, extra fields, bad
    types). Callers treat None as "route to repair, then fallback".
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    model = _ENVELOPE_BY_KIND.get(kind) if isinstance(kind, str) else None
    if model is None:
        return None
    try:
        return model.model_validate(raw)
    except ValidationError:
        return None


def extract_envelope(content: str) -> BaseModel | None:
    """Recover the first well-formed envelope embedded in model text.

    Local models wrap JSON in prose or code fences. We scan every balanced
    top-level JSON object (reusing the inline-tool-call scanner) and return
    the first one that validates as an envelope. None if there isn't one.
    """
    if not content:
        return None
    for chunk in _iter_json_object_substrings(content):
        env = parse_envelope(chunk)
        if env is not None:
            return env
    return None


# ── Tool-request validation (the hot-path hook) ─────────────────────────


def validate_tool_request(
    call: dict[str, Any],
    known_tools: set[str],
) -> ToolRequestEnvelope | None:
    """Validate one recovered tool call against the strict tool-request
    envelope.

    ``call`` is shaped like an OpenAI tool call —
    ``{"function": {"name": ..., "arguments": {...}}}`` — which is exactly
    what ``_extract_inline_tool_calls`` and ``message.tool_calls`` produce.
    Returns a validated ``ToolRequestEnvelope`` (tool is a known tool, args
    is a dict), or None if the call is malformed or names an unknown tool.
    """
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict):
        return None
    name = fn.get("name")
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            args = {}
    if not isinstance(args, dict):
        args = {}
    if not isinstance(name, str) or name not in known_tools:
        return None
    try:
        return ToolRequestEnvelope(tool=name, arguments=args)
    except ValidationError:
        return None


# Single source of truth for the one-shot repair nudge the loop injects when a
# turn looks like it tried to call a tool but produced a malformed envelope.
REPAIR_INSTRUCTION = (
    "[internal correction] Your last tool call was malformed. Re-send it once "
    "as a single JSON object on its own line, exactly:\n"
    '{"kind": "tool_request", "tool": "<one of the available tools>", '
    '"arguments": { ... }}\n'
    "No prose, no code fences, no extra fields. If you do not need a tool, "
    "answer the user directly in plain text instead."
)
