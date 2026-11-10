"""John 2026-11-10: documentation stays readable through an admin or code task.

Once a run has executed a command or changed a file, the web is a reference for
that work: the loop marks its web calls, and the web skill then budgets every
documentation lookup on its own. A run that only searches and reads is not marked.
"""
from __future__ import annotations

import json

import pytest

from app.application.agent_kernel.runtime_context import WEB_REFERENCE_KEY, current_runtime_context
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.tools import _web


def _call(name: str, arguments: dict, call_id: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


SEARCH = ("web_search", {"queries": ["jellyfin api add media library"], "audience": "global", "mode": "quick"})


@pytest.mark.parametrize("first_tool,marked", [
    (("write_file", {"path": "notes.txt", "content": "план установки"}), True),
    (("run_bash", {"command": "echo ready"}), True),
    (("read_file", {"path": "input.txt"}), False),  # reading a file is not yet work on the task
    (None, False),  # a run that only searches
])
def test_web_call_is_marked_as_reference_only_after_work(tmp_path, monkeypatch, first_tool, marked):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "input.txt").write_text("данные", encoding="utf-8")
    seen: list[dict] = []

    def fake_web(project_root, action, options):
        seen.append(dict(current_runtime_context()))
        return {"ok": True, "text": "[1] Jellyfin API\n   https://example.org/api", "sources": []}

    monkeypatch.setattr(_web, "_call", fake_web)
    script = [first_tool, SEARCH] if first_tool else [SEARCH]
    turns = []

    def chat(**kwargs):
        turns.append(kwargs)
        index = len(turns) - 1
        if index < len(script):
            name, arguments = script[index]
            return {"message": {"content": "Делаю шаг.", "tool_calls": [_call(name, arguments, f"call-{index}")]}}
        return {"message": {"content": "Сделано: шаги выполнены.", "tool_calls": []}}

    events = list(stream_code_agent(
        user_message="Настрой сервис и сверься с документацией.", project_root=tmp_path, model="test-model",
        run_id=f"web-reference-{len(script)}-{marked}", auto_remember=False, chat_fn=chat,
        base_tools=["read_file", "write_file", "run_bash", "web_search", "web_fetch"], permission_mode="bypass",
    ))

    calls = [event for event in events if event["type"] == "tool_call"]
    assert [event["tool"] for event in calls] == [name for name, _ in script], json.dumps(calls, ensure_ascii=False)[:600]
    assert len(seen) == 1
    assert (seen[0].get(WEB_REFERENCE_KEY) == "1") is marked
    assert seen[0]["step"] == str(len(script))
