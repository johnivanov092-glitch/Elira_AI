from __future__ import annotations

from app.application.agent_kernel.impact_policy import (
    ASK,
    AUTO,
    SafetyEvidence,
    decide_approval,
    evidence_for_tool_call,
    shell_command_is_high_impact,
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
