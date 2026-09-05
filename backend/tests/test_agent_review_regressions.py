"""End-to-end runtime regressions from the full agent review (fake model only)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent import agent_loop, delivery_session
from app.application.code_agent.run_journal import RunJournal, sanitize_event
from app.application.code_agent.taskspec import derive_task_spec


QUERY = "Исправь код проекта.\nКритерии готовности:\n- Создан файл result.txt\n- Проверен результат"


def test_output_redaction_preserves_workflow_control_data():
    event = {"type": "workflow_request", "schema": {"x-elira-secret-kind": "ssh_password",
             "x-elira-existing-secret-ref": "credential:opaque-id", "properties": {"secret_ref": {"type": "string"}}}}
    assert sanitize_event(event) == event


def test_recent_tool_output_can_roll_over_and_finish(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "source.txt").write_text("abcdefghij " * 3000, encoding="utf-8")
    calls = []
    def chat(**kwargs):
        if not kwargs.get("tools"):
            return {"message": {"content": "Read source.txt; no mutations.", "tool_calls": []}}
        calls.append(kwargs)
        if len(calls) == 1:
            return {"message": {"content": "", "tool_calls": [{"id": "read", "function": {
                "name": "read_file", "arguments": {"path": "source.txt"}}}]}}
        return {"message": {"content": "Source is read.", "tool_calls": []}}
    events = list(delivery_session.stream_delivery_session(
        user_message="Read source.txt.\nSuccess criteria:\n- Source is read", project_root=tmp_path,
        run_id="recoverable", chat_fn=chat, num_ctx=8192, base_tools=["read_file"], auto_remember=False,
    ))
    assert any(e["type"] == "delivery_continuing" for e in events)
    assert events[-1]["stop_reason"] == "answer"
    assert len(calls) == 2
    assert len([event for event in events if event["type"] == "final_response"]) == 1


def test_unshrinkable_context_finishes_without_delivery_rollover(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    schemas = [{"type": "function", "function": {"name": "capability_load", "description": "external schema " * 30000,
                "parameters": {"type": "object", "properties": {}}}}]
    monkeypatch.setattr(agent_loop, "build_runtime_tool_registry", lambda *a, **k: SimpleNamespace(collect_schemas=lambda: schemas))
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return {"message": {"content": "summary", "tool_calls": []}}
    events = []
    try:
        for event in delivery_session.stream_delivery_session(
            user_message=QUERY, memory_query=QUERY, project_root=tmp_path, run_id="oversized",
            chat_fn=chat, num_ctx=8192, auto_remember=False,
        ):
            events.append(event)
            if event["type"] == "delivery_continuing":
                delivery_session.request_session_cancel("oversized")
        assert not any(e["type"] == "delivery_continuing" for e in events)
        assert events[-1]["stop_reason"] == "error"
        assert events[-1]["error_code"] == "context_budget_exceeded"
        assert not any(event["type"] == "final_response" for event in events)
        assert events[-1]["resumable"] is False
        state = RunJournal.load("oversized").state
        assert state["request"]["user_message"] == QUERY
        assert len(calls) <= 1
    finally:
        delivery_session.request_session_cancel("oversized")


def test_fresh_slice_that_still_cannot_start_does_not_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    boundaries = []
    def chat(**kwargs):
        assert not kwargs.get("tools"), "oversized input must not reach the execution model"
        return {"message": {"content": "summary", "tool_calls": []}}
    events = []
    for event in delivery_session.stream_delivery_session(
        user_message=QUERY + "\nContext: " + "kept input " * 12000, project_root=tmp_path,
        run_id="no-progress", chat_fn=chat, num_ctx=4096, auto_remember=False,
        base_tools=["capability_load"],  # Isolate oversized input from schema size.
    ):
        events.append(event)
        if event["type"] == "delivery_continuing":
            boundaries.append(event)
            if len(boundaries) > 1:
                delivery_session.request_session_cancel("no-progress")
    assert len(boundaries) == 1
    assert events[-1]["stop_reason"] == "error"
    assert events[-1]["resumable"] is False


@pytest.mark.parametrize("legacy", [False, True])
def test_delivery_goal_and_contract_survive_resume_without_polluting_user_text(tmp_path, monkeypatch, legacy):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    journal = RunJournal("goal")
    req = {"user_message": QUERY, "project_root": str(tmp_path), "memory_query": QUERY}
    if legacy:
        req["user_message"] = delivery_session.DELIVERY_CONTRACT_NOTE + QUERY
    else:
        req["task_instructions"] = delivery_session.DELIVERY_CONTRACT_NOTE
    journal.start(req, {})
    journal.finish()
    kwargs = delivery_session.build_continuation_kwargs("goal")
    assert kwargs["task_instructions"] == delivery_session.DELIVERY_CONTRACT_NOTE
    assert kwargs["memory_query"] == QUERY
    source = next(m["content"] for m in kwargs["conversation_history"] if m["role"] == "user")
    assert source == QUERY
    assert derive_task_spec(source, tmp_path).goal == "Исправь код проекта."


def test_read_file_secrets_are_redacted_in_sse_and_both_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    canaries = ["audit-fake-bearer", "audit-fake-key", "audit-fake-password"]
    (tmp_path / "sample.txt").write_text(
        f"Visible output\nAuthorization: Bearer {canaries[0]}\nAPI_KEY={canaries[1]}\n--password {canaries[2]}", encoding="utf-8")
    replies = iter([
        {"message": {"content": "", "tool_calls": [{"id": "read", "function": {"name": "read_file", "arguments": {"path": "sample.txt"}}}]}},
        {"message": {"content": "Read complete.", "tool_calls": []}},
    ])
    events = list(agent_loop.stream_code_agent(
        user_message="Read sample.txt", memory_query="Read sample.txt", project_root=tmp_path, run_id="redaction",
        base_tools=["read_file"], chat_fn=lambda **kwargs: next(replies), auto_remember=False,
    ))
    run_dir = tmp_path / "runs" / "redaction"
    surfaces = [json.dumps(events), (run_dir / "events.jsonl").read_text(encoding="utf-8"),
                (run_dir / "logs" / "commands.jsonl").read_text(encoding="utf-8")]
    for surface in surfaces:
        assert "Visible output" in surface
        assert all(canary not in surface for canary in canaries), [e for e in events if any(c in json.dumps(e) for c in canaries)]
    assert events[-1]["ok"] is True
