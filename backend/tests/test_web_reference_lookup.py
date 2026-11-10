"""John 2026-11-10: documentation stays readable through an admin or code task.

Once a run has executed a command or changed a file, the web is a reference for
that work: the loop marks its web calls with the number of the documentation
lookup they belong to, and the web skill budgets every lookup on its own. A run
that only searches and reads is not marked.
"""
from __future__ import annotations

import json

import pytest

from app.application.agent_kernel.runtime_context import WEB_REFERENCE_KEY, current_runtime_context
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.tools import _web

SEARCH = ("web_search", {"queries": ["jellyfin api add media library"], "audience": "global", "mode": "quick"})
WORK = ("run_bash", {"command": "echo ready"})
NOTE = ("write_file", {"path": "notes.txt", "content": "план установки"})
READ = ("read_file", {"path": "input.txt"})
# The model composes this address itself, so the address rule refuses it before the web skill runs.
INVENTED = ("web_fetch", {"urls": ["https://docs.example.invalid/made-up-page"]})


def _run(tmp_path, monkeypatch, script: list[tuple[str, dict]], run_id: str) -> tuple[list[dict], list[dict]]:
    """Run the real loop with a scripted model: one tool call per step, then the answer."""
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "input.txt").write_text("данные", encoding="utf-8")
    seen: list[dict] = []

    def fake_web(project_root, action, options):
        seen.append(dict(current_runtime_context()))
        return {"ok": True, "text": "[1] Jellyfin API\n   https://example.org/api", "sources": []}

    monkeypatch.setattr(_web, "_call", fake_web)
    turns = []

    def chat(**kwargs):
        turns.append(kwargs)
        index = len(turns) - 1
        if index < len(script):
            name, arguments = script[index]
            return {"message": {"content": "Делаю шаг.", "tool_calls": [{
                "id": f"call-{index}", "type": "function", "function": {"name": name, "arguments": arguments}}]}}
        return {"message": {"content": "Сделано: шаги выполнены.", "tool_calls": []}}

    events = list(stream_code_agent(
        user_message="Настрой сервис и сверься с документацией.", project_root=tmp_path, model="test-model",
        run_id=run_id, auto_remember=False, chat_fn=chat,
        base_tools=["read_file", "write_file", "run_bash", "web_search", "web_fetch"], permission_mode="bypass",
    ))
    calls = [event for event in events if event["type"] == "tool_call"]
    assert [event["tool"] for event in calls] == [name for name, _ in script], json.dumps(calls, ensure_ascii=False)[:600]
    return seen, calls


@pytest.mark.parametrize("first_tool,marked", [
    (NOTE, True),
    (WORK, True),
    (READ, False),  # reading a file is not yet work on the task
    (None, False),  # a run that only searches
])
def test_web_call_is_marked_as_reference_only_after_work(tmp_path, monkeypatch, first_tool, marked):
    script = [first_tool, SEARCH] if first_tool else [SEARCH]
    seen, _ = _run(tmp_path, monkeypatch, script, f"web-reference-{len(script)}-{marked}")

    assert len(seen) == 1 and seen[0]["step"] == str(len(script))
    # The mark is the step at which this documentation lookup began.
    assert seen[0].get(WEB_REFERENCE_KEY) == (str(len(script)) if marked else None)


def test_lookup_number_changes_only_after_a_step_without_web_calls(tmp_path, monkeypatch):
    #          step: 1     2       3       4     5       6     7     8
    script = [WORK, SEARCH, SEARCH, WORK, SEARCH, WORK, WORK, SEARCH]
    seen, _ = _run(tmp_path, monkeypatch, script, "web-reference-numbers")

    assert [context["step"] for context in seen] == ["2", "3", "5", "8"]
    assert [context[WEB_REFERENCE_KEY] for context in seen] == ["2", "2", "5", "8"]


def test_refused_web_call_keeps_the_lookup_open(tmp_path, monkeypatch):
    # Run e60512e7 (sandbox, 2026-11-10): an invented address was refused at step 6
    # and the next call was taken for a new lookup. A refused call is still a web step.
    script = [WORK, SEARCH, INVENTED, SEARCH]
    seen, calls = _run(tmp_path, monkeypatch, script, "web-reference-refused")

    assert calls[2]["ok"] is False and calls[2]["error"] == "url_not_from_sources"
    assert [context["step"] for context in seen] == ["2", "4"]  # the refused fetch never reached the skill
    assert [context[WEB_REFERENCE_KEY] for context in seen] == ["2", "2"]
