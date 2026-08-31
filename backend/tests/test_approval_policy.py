from __future__ import annotations

from unittest.mock import patch

from app.application.agent_kernel.impact_policy import (
    ASK,
    AUTO,
    SafetyEvidence,
    decide_approval,
    evidence_for_tool_call,
    shell_command_is_high_impact,
    tool_call_is_change,
)
from app.application.agent_kernel.executor import ToolExecutionRequest, permission_mode_auto_approves


def test_permission_modes_have_one_workflow_contract() -> None:
    ordinary = SafetyEvidence(impact="material")
    dangerous = SafetyEvidence(impact="high")

    assert decide_approval("ask", "local", ordinary) == ASK
    assert decide_approval("accept_edits", "local", ordinary) == AUTO
    assert decide_approval("accept_edits", "local", dangerous) == ASK
    assert decide_approval("bypass", "local", dangerous) == AUTO


def test_read_only_call_never_needs_workflow_approval() -> None:
    unknown = SafetyEvidence()
    for mode in ("ask", "accept_edits", "bypass"):
        assert decide_approval(mode, "local", unknown, is_change=False) == AUTO


def test_project_corpus_status_is_read_only_but_indexing_is_a_change() -> None:
    assert not tool_call_is_change("runtime_control", {"operation": "project_status"})
    assert tool_call_is_change("runtime_control", {"operation": "project_index"})


def test_bypass_does_not_depend_on_registry_or_classifier() -> None:
    request = ToolExecutionRequest(
        run_id="run",
        agent_id="code-agent",
        project_scope_id="",
        tool_name="unknown_tool",
        args={},
        source="workflow",
        permission_mode="bypass",
    )
    assert permission_mode_auto_approves(request) is True


def test_high_impact_shell_detection_only_classifies_workflow_impact() -> None:
    assert shell_command_is_high_impact("Remove-Item -Recurse C:\\temp\\cache")
    assert not shell_command_is_high_impact("npm run build")
    assert evidence_for_tool_call("run_bash", {"command": "npm run build"}).impact == "material"


def _local_ask_request(tool_name: str, args: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        run_id="run",
        agent_id="code-agent",
        project_scope_id="",
        tool_name=tool_name,
        args=args,
        source="workflow",
        permission_mode="ask",
    )


def test_browser_permission_is_classified_from_actions() -> None:
    passive = _local_ask_request("browser", {"url": "https://example.com"})
    interactive = _local_ask_request(
        "browser",
        {
            "url": "https://example.com",
            "actions": [{"fill": "Email", "value": "user@example.com"}],
        },
    )
    with patch(
        "app.application.tool_registry.runtime.get_tool",
        return_value={"side_effect": False},
    ):
        assert permission_mode_auto_approves(passive) is True
        assert permission_mode_auto_approves(interactive) is False


def test_computer_permission_is_classified_from_action() -> None:
    screenshot = _local_ask_request("computer", {"action": "screenshot"})
    click = _local_ask_request("computer", {"action": "left_click", "x": 10, "y": 20})
    with patch(
        "app.application.tool_registry.runtime.get_tool",
        return_value={"side_effect": True},
    ):
        assert permission_mode_auto_approves(screenshot) is True
        assert permission_mode_auto_approves(click) is False
