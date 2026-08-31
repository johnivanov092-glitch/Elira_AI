from __future__ import annotations

from unittest.mock import patch

import pytest

from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool


def _execute(raw_result):
    request = ToolExecutionRequest(
        run_id="contract-run",
        agent_id="code-agent",
        project_scope_id="",
        tool_name="contract_tool",
        args={},
        source="test",
        permission_mode="bypass",
    )
    with (
        patch(
            "app.application.tool_registry.runtime.get_tool",
            return_value={"side_effect": False, "max_output_chars": 50000},
        ),
        patch("app.application.agent_kernel.executor._emit_executed"),
    ):
        return execute_tool(request, lambda _name, _args: raw_result)


def test_executor_accepts_an_explicit_boolean_ok() -> None:
    result = _execute({"ok": True, "text": "done"})

    assert result.status == "ok"
    assert result.output == {"ok": True, "text": "done"}


@pytest.mark.parametrize(
    "raw_result",
    [
        {"text": "done"},
        {"ok": "true", "text": "done"},
        "done",
    ],
)
def test_executor_rejects_results_without_boolean_ok(raw_result) -> None:
    result = _execute(raw_result)

    assert result.status == "error"
    assert result.output["ok"] is False
    assert result.output["error"] == "invalid_tool_result"
    assert "result contract" in result.output["text"]


def test_error_text_is_not_used_to_infer_status() -> None:
    result = _execute({"ok": True, "text": "ERROR: legitimate file content"})

    assert result.status == "ok"
