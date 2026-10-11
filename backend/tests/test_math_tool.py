"""Base tool math: one call into the MATH skill CLI (owner decision 2026-10-11)."""
from __future__ import annotations

import json

from app.application.agent_kernel.executor import ToolExecutionRequest, permission_mode_auto_approves
from app.application.agent_kernel.impact_policy import tool_call_is_change
from app.application.code_agent.capabilities import ALL_BUILTIN_TOOLS
from app.application.code_agent.tool_policy import BASE_TOOLS
from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.code_agent.tools._dispatch import build_tool_dispatch
from app.application.code_agent.tools._math import tool_math
from app.application.tool_registry.builtins import _build_native_code_agent_tools

VAT = {"operation": "vat_add", "amount": "125 000 тг", "vat_percent": "16"}


def test_math_is_a_visible_read_only_base_tool(tmp_path):
    schema = next(s["function"] for s in build_tool_schemas() if s["function"]["name"] == "math")
    assert schema["parameters"]["required"] == ["request"]
    assert "math" in BASE_TOOLS and "math" in ALL_BUILTIN_TOOLS
    assert "math" in build_tool_dispatch(tmp_path)
    spec = next(s for s in _build_native_code_agent_tools() if s["name"] == "math")
    assert spec["side_effect"] is False and spec["permission"] == "auto"


def test_only_plot_is_a_change_for_workflow():
    assert tool_call_is_change("math", {"request": VAT}) is False
    assert tool_call_is_change("math", {"request": json.dumps(VAT)}) is False
    assert tool_call_is_change("math", {"request": "{broken"}) is False
    assert tool_call_is_change("math", {"request": {"operation": "plot", "kind": "normal"}}) is True

    def ask(args):
        return permission_mode_auto_approves(ToolExecutionRequest(
            run_id="r", agent_id="code", project_scope_id="p", tool_name="math",
            args=args, source="code_agent", permission_mode="ask",
        ))
    assert ask({"request": VAT}) is True
    assert ask({"request": {"operation": "plot", "kind": "normal"}}) is False


def test_math_runs_the_skill_cli_in_one_call(tmp_path):
    result = tool_math(tmp_path, request=VAT)
    assert result["ok"] is True
    assert result["result"]["vat_amount"] == "20000.00" and result["result"]["total"] == "145000.00"
    assert '"total": "145000.00"' in result["text"] and "explanation:" in result["text"]
    solved = tool_math(tmp_path, request={"operation": "solve", "expression": "2*x+y=10;x-y=2"})
    answer = solved["result"]["solutions"][0]
    assert solved["ok"] is True and answer["x"]["exact"] == "4" and answer["y"]["exact"] == "2"


def test_math_accepts_json_text_and_flat_arguments(tmp_path):
    as_text = tool_math(tmp_path, request=json.dumps(VAT, ensure_ascii=False))
    flat = tool_math(tmp_path, **VAT)
    assert as_text["ok"] is True and flat["ok"] is True
    assert as_text["result"] == flat["result"]


def test_math_errors_say_what_to_send(tmp_path):
    broken = tool_math(tmp_path, request="{operation: vat_add")
    assert broken["ok"] is False and "not valid JSON" in broken["text"] and '"operation"' in broken["text"]
    missing = tool_math(tmp_path, request={"amount": "1"})
    assert missing["ok"] is False and "operation" in missing["text"]
    unknown = tool_math(tmp_path, request={"operation": "vat_add", "amount": "1"})
    assert unknown["ok"] is False and unknown["text"].startswith("ERROR")
