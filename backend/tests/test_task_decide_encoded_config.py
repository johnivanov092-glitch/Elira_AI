import json

import pytest

from app.application.code_agent.tools._runtime_control import tool_runtime_control


def test_double_encoded_declaration_keeps_normal_validation(tmp_path):
    config = {"disposition": "one_off", "reason": "Исследование по первичным источникам"}
    plain = tool_runtime_control(tmp_path, operation="task_decide", config=config)
    encoded = tool_runtime_control(tmp_path, operation="task_decide", config=json.dumps(config, ensure_ascii=False))
    assert encoded == plain and encoded["ok"]


@pytest.mark.parametrize("config", ['[]', '"text"', '17', '{bad', '{"disposition":"invented"}'])
def test_invalid_encoded_declaration_fails_closed(tmp_path, config):
    result = tool_runtime_control(tmp_path, operation="task_decide", config=config)
    assert not result["ok"] and result["status"] == "failed"
    assert result["error"]["retryable"] is False


def test_execution_config_is_not_coerced_after_approval(tmp_path):
    result = tool_runtime_control(tmp_path, operation="result_verify", config='{"command":"never execute"}')
    assert not result["ok"] and result["error"]["message"] == "config must be an object"
