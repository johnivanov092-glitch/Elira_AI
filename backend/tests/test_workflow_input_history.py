from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import request_cancel, stream_code_agent, submit_workflow_response
from app.application.code_agent.delivery_session import build_continuation_kwargs, stream_resume_session
from app.application.code_agent.run_journal import RunJournal
from app.api.routes import code_agent_routes as routes


def _run_question(tmp_path: Path, monkeypatch, *, action: str, answer: object,
                  cancel_after_answer: bool = False, fail_after_answer: bool = False) -> list[dict]:
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    calls = 0

    def chat(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"message": {"content": "", "tool_calls": [{"function": {
                "name": "ask_user",
                "arguments": {"question": "Сколько ИБП и АКБ?"},
            }}]}}
        assert any(m.get("role") == "tool" for m in kwargs["messages"])
        if fail_after_answer:
            raise RuntimeError("provider unavailable after clarification")
        return {"message": {"content": "Ответ получен.", "tool_calls": []}}

    events = []
    for event in stream_code_agent(
        user_message="Уточни количество.",
        project_root=tmp_path,
        run_id="workflow-input-history",
        chat_fn=chat,
        auto_remember=False,
        permission_mode="bypass",
    ):
        events.append(event)
        if event["type"] == "workflow_request":
            assert submit_workflow_response(event["response_id"], action, {"answer": answer})
            if cancel_after_answer:
                assert request_cancel("workflow-input-history")
    return events


def test_accepted_workflow_answer_is_replayable_user_input(tmp_path, monkeypatch):
    answer = "1 ИБП, 40 АКБ; опт — закупка, розница — цена клиенту."
    events = _run_question(tmp_path, monkeypatch, action="accept", answer=answer)
    request = next(e for e in events if e["type"] == "workflow_request")
    tool = next(e for e in events if e["type"] == "tool_call" and e["tool"] == "ask_user")
    expected = {"request_id": request["response_id"], "question": "Сколько ИБП и АКБ?", "answer": answer}
    assert tool.get("workflow_input") == expected
    journal = RunJournal.load("workflow-input-history")
    assert journal.state.get("workflow_inputs") == [expected]
    stored = [json.loads(line) for line in journal.events_path.read_text(encoding="utf-8").splitlines()]
    assert next(e for e in stored if e["type"] == "tool_call")["workflow_input"] == expected


@pytest.mark.parametrize(("action", "answer"), [("decline", "1 ИБП"), ("accept", {"password": "hidden"})])
def test_unaccepted_or_invalid_answer_is_not_carried(tmp_path, monkeypatch, action, answer):
    events = _run_question(tmp_path, monkeypatch, action=action, answer=answer)
    tool = next(e for e in events if e["type"] == "tool_call" and e["tool"] == "ask_user")
    assert "workflow_input" not in tool
    assert not RunJournal.load("workflow-input-history").state.get("workflow_inputs")


def test_workflow_answer_redacts_secret_patterns_before_history(tmp_path, monkeypatch):
    events = _run_question(tmp_path, monkeypatch, action="accept", answer="1 ИБП; password=hidden-value")
    tool = next(e for e in events if e["type"] == "tool_call" and e["tool"] == "ask_user")
    assert tool.get("workflow_input", {}).get("answer") == "1 ИБП; password=[REDACTED]"
    assert "hidden-value" not in json.dumps(RunJournal.load("workflow-input-history").state.get("workflow_inputs"))


def test_accepted_answer_survives_public_resume_after_provider_failure(tmp_path, monkeypatch):
    answer = "1 ИБП, 40 АКБ; опт — закупка."
    events = _run_question(tmp_path, monkeypatch, action="accept", answer=answer, fail_after_answer=True)
    assert events[-1]["stop_reason"] == "error"
    assert RunJournal.load("workflow-input-history").state["resumable"] is True
    seen = []

    def continued_chat(**kwargs):
        seen.extend(kwargs["messages"])
        return {"message": {"content": "Уточнение сохранилось.", "tool_calls": []}}

    monkeypatch.setattr(routes, "stream_resume_session", lambda run_id: stream_resume_session(run_id, chat_fn=continued_chat))
    monkeypatch.setattr(routes, "_persona_observation", lambda **_: None)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.post("/api/code-agent/runs/workflow-input-history/resume")
    assert response.status_code == 200
    resumed = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert resumed[-1]["type"] == "done" and resumed[-1]["ok"] is True
    assert any(m["role"] == "user" and answer in m.get("content", "") for m in seen)
    assert not any(m["role"] != "user" and answer in m.get("content", "") for m in seen)


def test_accepted_answer_is_preserved_when_stop_races_response(tmp_path, monkeypatch):
    answer = "1 ИБП, 40 АКБ"
    events = _run_question(tmp_path, monkeypatch, action="accept", answer=answer, cancel_after_answer=True)
    assert events[-1]["type"] == "done"
    assert events[-1]["stop_reason"] == "cancelled"
    assert not any(e["type"] == "step_started" and e["step"] > 1 for e in events)
    inputs = RunJournal.load("workflow-input-history").state.get("workflow_inputs") or []
    assert len(inputs) == 1
    assert inputs[0]["answer"] == answer
    history = build_continuation_kwargs("workflow-input-history")["conversation_history"]
    assert any(m["role"] == "user" and answer in m["content"] for m in history)
