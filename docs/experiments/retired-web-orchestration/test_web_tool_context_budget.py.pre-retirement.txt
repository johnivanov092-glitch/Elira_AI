"""Web text budgets must survive the canonical executor-to-model boundary."""
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.loop_helpers import TOOL_RESULT_LLM_LIMIT, WEB_TOOL_RESULT_LLM_LIMIT


@pytest.mark.parametrize("tool,arguments,budget", [
    ("web_search", {"query": "documentation"}, WEB_TOOL_RESULT_LLM_LIMIT),
    ("web_fetch", {"url": "https://example.org/documentation"}, WEB_TOOL_RESULT_LLM_LIMIT),
    ("read_file", {"path": "documentation.txt"}, TOOL_RESULT_LLM_LIMIT),
])
@pytest.mark.parametrize("size", [24000, 40000])
def test_tool_text_reaches_next_model_turn_with_its_budget(tmp_path, tool, arguments, budget, size):
    body = "DOCUMENT_START\n" + "x" * (size - 30) + "\nDOCUMENT_END"
    calls = []
    responses = iter([
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": tool, "arguments": arguments,
        }}]}},
        {"message": {"content": "Прочитано.", "tool_calls": []}},
    ])

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        return next(responses)

    with patch("app.application.code_agent.tools._dispatch.tool_" + tool,
               return_value={"ok": True, "text": body}):
        events = list(stream_code_agent(
            user_message="Прочитай документацию.", project_root=tmp_path,
            chat_fn=chat, permission_mode="bypass", auto_remember=False,
            num_ctx=65536,
        ))

    assert len(calls) >= 2
    content = next(message["content"] for message in calls[1]
                   if message.get("role") == "tool" and message.get("name") == tool)
    assert len(content) <= budget + 200
    assert "DOCUMENT_START" in content and "DOCUMENT_END" in content
    if len(body) <= budget:
        assert content == body
    else:
        assert "truncated" in content.lower()
        assert budget - 200 <= len(content)
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True
