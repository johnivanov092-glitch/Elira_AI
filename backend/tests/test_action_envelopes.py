"""Tests for D3 — structured action envelopes (app.application.code_agent.action_envelopes).

Covers the schemas, the strict parser, embedded-envelope extraction, the
tool-request validator (the hot-path hook), and the env-flag gate. The
agent-loop integration is exercised indirectly: with the flag OFF the gate
helper returns False (so the loop's hot path is untouched), and with it ON
the validator accepts/rejects exactly the calls the loop would dispatch.
"""
from __future__ import annotations

import pytest

from app.application.code_agent import action_envelopes as ae


KNOWN = {"run_bash", "read_file", "write_file"}


# ── Gate ────────────────────────────────────────────────────────────────


def test_gate_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELIRA_ACTION_ENVELOPES", raising=False)
    assert ae.envelopes_enabled() is False


@pytest.mark.parametrize("val", ["1", "on", "true", "yes", "ON", "  Yes  "])
def test_gate_truthy_values(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("ELIRA_ACTION_ENVELOPES", val)
    assert ae.envelopes_enabled() is True


@pytest.mark.parametrize("val", ["0", "off", "false", "no", "", "maybe"])
def test_gate_falsy_values(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("ELIRA_ACTION_ENVELOPES", val)
    assert ae.envelopes_enabled() is False


# ── Schemas ─────────────────────────────────────────────────────────────


def test_plan_envelope_valid() -> None:
    env = ae.PlanEnvelope(steps=["read file", "edit", "verify"])
    assert env.kind == "plan"
    assert env.steps[0] == "read file"


def test_plan_envelope_rejects_empty_steps() -> None:
    with pytest.raises(Exception):
        ae.PlanEnvelope(steps=[])


def test_tool_request_envelope_valid() -> None:
    env = ae.ToolRequestEnvelope(tool="run_bash", arguments={"cmd": "ls"})
    assert env.kind == "tool_request"
    assert env.tool == "run_bash"
    assert env.arguments == {"cmd": "ls"}


def test_tool_request_defaults_empty_args() -> None:
    env = ae.ToolRequestEnvelope(tool="read_file")
    assert env.arguments == {}


def test_action_envelope_tool_optional() -> None:
    thought = ae.ActionEnvelope(intent="think about it")
    assert thought.tool is None
    acted = ae.ActionEnvelope(intent="run it", tool="run_bash", arguments={"cmd": "pwd"})
    assert acted.tool == "run_bash"


def test_final_result_envelope_valid() -> None:
    env = ae.FinalResultEnvelope(text="done")
    assert env.kind == "final_result"


def test_blocker_envelope_valid() -> None:
    env = ae.BlockerEnvelope(reason="missing path", needs=["target file"])
    assert env.kind == "blocker"
    assert env.needs == ["target file"]


def test_extra_fields_forbidden() -> None:
    # A hallucinated extra field must fail strict construction.
    with pytest.raises(Exception):
        ae.ToolRequestEnvelope(tool="run_bash", arguments={}, bogus=1)  # type: ignore[call-arg]


# ── parse_envelope ──────────────────────────────────────────────────────


def test_parse_envelope_from_dict() -> None:
    env = ae.parse_envelope({"kind": "final_result", "text": "ok"})
    assert isinstance(env, ae.FinalResultEnvelope)


def test_parse_envelope_from_json_string() -> None:
    env = ae.parse_envelope('{"kind": "tool_request", "tool": "run_bash", "arguments": {}}')
    assert isinstance(env, ae.ToolRequestEnvelope)


def test_parse_envelope_unknown_kind_returns_none() -> None:
    assert ae.parse_envelope({"kind": "nonsense", "x": 1}) is None


def test_parse_envelope_missing_kind_returns_none() -> None:
    assert ae.parse_envelope({"tool": "run_bash"}) is None


def test_parse_envelope_extra_field_returns_none() -> None:
    # extra="forbid" → strict parse fails → None (route to repair/fallback).
    assert ae.parse_envelope({"kind": "final_result", "text": "ok", "x": 1}) is None


def test_parse_envelope_bad_json_returns_none() -> None:
    assert ae.parse_envelope("{not json") is None


def test_parse_envelope_non_dict_returns_none() -> None:
    assert ae.parse_envelope(42) is None
    assert ae.parse_envelope(["a"]) is None


# ── extract_envelope ────────────────────────────────────────────────────


def test_extract_envelope_from_prose() -> None:
    content = 'Sure, here you go:\n{"kind": "final_result", "text": "hi"}\nThanks!'
    env = ae.extract_envelope(content)
    assert isinstance(env, ae.FinalResultEnvelope)
    assert env.text == "hi"


def test_extract_envelope_none_when_absent() -> None:
    assert ae.extract_envelope("just a plain sentence, no json here") is None


def test_extract_envelope_empty_string() -> None:
    assert ae.extract_envelope("") is None


# ── validate_tool_request (the hot-path hook) ───────────────────────────


def test_validate_tool_request_accepts_known_tool() -> None:
    call = {"function": {"name": "run_bash", "arguments": {"cmd": "ls"}}}
    env = ae.validate_tool_request(call, KNOWN)
    assert isinstance(env, ae.ToolRequestEnvelope)
    assert env.tool == "run_bash"
    assert env.arguments == {"cmd": "ls"}


def test_validate_tool_request_rejects_unknown_tool() -> None:
    call = {"function": {"name": "rm_rf_slash", "arguments": {}}}
    assert ae.validate_tool_request(call, KNOWN) is None


def test_validate_tool_request_parses_string_arguments() -> None:
    # Local models often send arguments as a JSON string, not a dict.
    call = {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
    env = ae.validate_tool_request(call, KNOWN)
    assert env is not None
    assert env.arguments == {"path": "a.py"}


def test_validate_tool_request_bad_string_args_become_empty() -> None:
    call = {"function": {"name": "read_file", "arguments": "not json"}}
    env = ae.validate_tool_request(call, KNOWN)
    assert env is not None
    assert env.arguments == {}


def test_validate_tool_request_missing_function_returns_none() -> None:
    assert ae.validate_tool_request({"name": "run_bash"}, KNOWN) is None


def test_validate_tool_request_non_dict_returns_none() -> None:
    assert ae.validate_tool_request("nope", KNOWN) is None  # type: ignore[arg-type]


def test_validate_tool_request_non_str_name_returns_none() -> None:
    call = {"function": {"name": 123, "arguments": {}}}
    assert ae.validate_tool_request(call, KNOWN) is None


# ── repair instruction ──────────────────────────────────────────────────


def test_repair_instruction_is_actionable() -> None:
    # Must name the structured shape so the model can self-correct in one shot.
    assert "tool_request" in ae.REPAIR_INSTRUCTION
    assert "[internal correction]" in ae.REPAIR_INSTRUCTION
