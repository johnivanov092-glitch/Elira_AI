from __future__ import annotations

from unittest.mock import patch

import pytest

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


@pytest.mark.parametrize("mode", ["ask", "accept_edits", "bypass"])
@pytest.mark.parametrize("tool,args", [
    ("mcp", {"action": "tools"}),
    ("mcp", {"action": "list"}),
    ("telegram", {"action": "messages"}),
    ("itops_registry", {"action": "mikrotik_list"}),
    ("memory", {"action": "search"}),
    ("recall", {"action": "status"}),
])
def test_runtime_discovery_and_reads_do_not_pause(mode, tool, args):
    request = _local_ask_request(tool, args)
    request.permission_mode = mode
    with patch("app.application.tool_registry.runtime.get_tool", return_value={"side_effect": True}):
        assert permission_mode_auto_approves(request)


@pytest.mark.parametrize("mode,expected", [("ask", False), ("accept_edits", False), ("bypass", True)])
def test_background_command_has_same_approval_as_foreground(mode, expected):
    for name in ("run_bash", "run_server"):
        request = _local_ask_request(name, {"action": "start", "kind": "job", "command": "git reset --hard"})
        request.permission_mode = mode
        with patch("app.application.tool_registry.runtime.get_tool", return_value={"side_effect": True}):
            assert permission_mode_auto_approves(request) is expected


@pytest.mark.parametrize("action", ["list", "logs"])
def test_background_job_inspection_is_read_only(action):
    request = _local_ask_request("run_server", {"action": action})
    with patch("app.application.tool_registry.runtime.get_tool", return_value={"side_effect": True}):
        assert permission_mode_auto_approves(request)


def test_background_start_and_stop_keep_ordinary_local_permission():
    for args in ({"action": "start", "command": "npm run dev"}, {"action": "stop", "pid": 123}):
        request = _local_ask_request("run_server", args)
        with patch("app.application.tool_registry.runtime.get_tool", return_value={"side_effect": True}):
            assert not permission_mode_auto_approves(request)
            request.permission_mode = "accept_edits"
            assert permission_mode_auto_approves(request)


def test_mcp_server_lifecycle_is_ordinary_local_work_but_config_changes_ask():
    for args, accept_edits in (({"action": "start", "server_id": "atlas"}, True),
                               ({"action": "restart", "server_id": "atlas"}, True),
                               ({"action": "add", "server_id": "x", "config": {"command": "x"}}, False),
                               ({"action": "remove", "server_id": "atlas"}, False)):
        request = _local_ask_request("mcp", args)
        with patch("app.application.tool_registry.runtime.get_tool", return_value={"side_effect": True}):
            assert not permission_mode_auto_approves(request)
            request.permission_mode = "accept_edits"
            assert permission_mode_auto_approves(request) is accept_edits


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
    assert not tool_call_is_change("recall", {"action": "status"})
    assert not tool_call_is_change("recall", {"query": "x"})
    assert tool_call_is_change("recall", {"action": "index"})
    assert tool_call_is_change("memory", {"action": "add"})
    assert tool_call_is_change("telegram", {"action": "send"})


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




def test_desktop_skill_uses_normal_shell_approval() -> None:
    request = _local_ask_request("run_bash", {"command": 'python computer.py left_click --x 10 --y 20'})
    with patch("app.application.tool_registry.runtime.get_tool", return_value={"side_effect": True}):
        assert permission_mode_auto_approves(request) is False
    request.permission_mode = "bypass"
    assert permission_mode_auto_approves(request) is True
