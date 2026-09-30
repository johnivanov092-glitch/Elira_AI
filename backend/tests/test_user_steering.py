from copy import deepcopy
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routes import code_agent_routes
from app.application.code_agent import loop_helpers as control
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import stream_delivery_session
from app.application.code_agent.run_journal import RunJournal


@pytest.fixture(autouse=True)
def journals(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))


@contextmanager
def owner(run="steering-test", session="chat-a"):
    token = control.register_session(run, session_id=session)
    try:
        yield run
    finally:
        control.unregister_session(run, token)


def test_queue_is_chat_bound_idempotent_and_final_admission_is_atomic():
    with owner() as run:
        with pytest.raises(control.SessionInputError) as wrong:
            control.queue_session_input(run, "chat-b", "a" * 32, "Уточнение")
        assert wrong.value.status_code == 404
        row = control.queue_session_input(run, "chat-a", "a" * 32, "Сохрани исходную задачу")
        assert row["state"] == "queued"
        assert control.queue_session_input(run, "chat-a", "a" * 32, row["text"]) == row
        with pytest.raises(control.SessionInputError):
            control.queue_session_input(run, "chat-a", "a" * 32, "Другая задача")
        assert len(control.take_session_inputs(run, finishing=True)) == 1
        assert control.take_session_inputs(run, finishing=True) == []
        with pytest.raises(control.SessionInputError):
            control.queue_session_input(run, "chat-a", "b" * 32, "Слишком поздно")
    # A lost HTTP reply can still be reconciled after completion.
    assert control.queue_session_input(run, "chat-a", "a" * 32, row["text"])["state"] == "applied"


def test_stop_keeps_pending_input_and_resume_consumes_it_once():
    with owner() as run:
        control.queue_session_input(run, "chat-a", "a" * 32, "Проверяй только этот файл")
        assert control.request_session_cancel(run)
        with pytest.raises(control.SessionInputError):
            control.queue_session_input(run, "chat-a", "b" * 32, "Позднее уточнение")
    assert RunJournal(run).read_user_inputs()["items"][0]["state"] == "queued"
    with owner(run) as resumed:
        assert control.take_session_inputs(resumed)[0]["text"] == "Проверяй только этот файл"
        assert control.take_session_inputs(resumed) == []


@pytest.mark.parametrize("ordinary_api_task", [False, True])
def test_steering_discards_unstarted_tools_and_continues_the_same_agent(tmp_path, ordinary_api_task):
    (tmp_path / "ok.txt").write_text("Проверено", encoding="utf-8")
    calls = []
    from contextlib import nullcontext

    with (nullcontext("steering-test") if ordinary_api_task else owner()) as run:
        def chat(**kwargs):
            calls.append(deepcopy(kwargs["messages"]))
            assert all(m["role"] not in {"system", "developer"} for m in kwargs["messages"][1:]), (
                "System message must be at the beginning."
            )
            if len(calls) == 1:
                control.queue_session_input(run, "chat-a", "a" * 32, "Не меняй файлы, только прочитай ok.txt")
                return {"message": {"content": "Сейчас изменю файл", "tool_calls": [{
                    "id": "obsolete", "function": {"name": "write_file", "arguments": {"path": "bad.txt", "content": "bad"}},
                }]}}
            if len(calls) == 2:
                assert any(m["role"] == "user" and m["content"] == "Не меняй файлы, только прочитай ok.txt" for m in kwargs["messages"])
                return {"message": {"content": "Ок, учла. Только прочитаю файл.", "tool_calls": [{
                    "id": "read", "function": {"name": "read_file", "arguments": {"path": "ok.txt"}},
                }]}}
            return {"message": {"content": "Файл прочитан.", "tool_calls": []}}

        stream = stream_delivery_session if ordinary_api_task else stream_code_agent
        events = list(stream(user_message="Проверь файл", project_root=tmp_path, run_id=run,
                                        session_id="chat-a", chat_fn=chat, auto_remember=False, num_ctx=32768,
                                        base_tools=["read_file", "write_file"]))
    assert not (tmp_path / "bad.txt").exists()
    assert [e["tool"] for e in events if e["type"] == "tool_call"] == ["read_file"], events
    assert len([e for e in events if e["type"] == "done"]) == 1
    assert next(e for e in events if e["type"] == "user_input_applied")["request_id"] == "a" * 32
    assert next(e for e in events if e["type"] == "user_input_reply")["text"] == "Ок, учла. Только прочитаю файл."
    assert next(e for e in events if e["type"] == "final_response")["text"] == "Файл прочитан."
    assert RunJournal.load(run).state["status"] == "completed"
    from app.application.code_agent.delivery_session import build_continuation_kwargs

    restored = build_continuation_kwargs(run)
    assert {"role": "user", "content": "Не меняй файлы, только прочитай ok.txt"} in restored["conversation_history"]
    assert restored["session_id"] == "chat-a"


def test_http_input_validation_and_receipt_reconciliation():
    app = FastAPI()
    app.include_router(code_agent_routes.router)
    with owner() as run, TestClient(app) as client:
        path = f"/api/code-agent/runs/{run}/input"
        payload = {"session_id": "chat-a", "request_id": "a" * 32, "message": "Продолжай с учётом уточнения"}
        assert client.post(path, json={**payload, "platform": "other"}).status_code == 422
        assert client.post(path, json={**payload, "message": " "}).status_code == 422
        assert client.post(path, json={**payload, "session_id": "chat-b"}).status_code == 404
        row = client.post(path, json=payload).json()
        assert client.get(f"/api/code-agent/runs/{run}/inputs?session_id=chat-a").json() == {"items": [row]}
        assert client.get(f"/api/code-agent/runs/{run}/inputs?session_id=chat-b").status_code == 404
    for value in (".", "..", "../escape"):
        with pytest.raises(ValueError):
            RunJournal(value)


def test_conversational_inputs_before_and_during_inference_reach_the_same_model(tmp_path):
    run = "conversation-steering"
    calls = []
    first_input = "как дела?"
    second_input = "Ответь коротко"

    def chat(**kwargs):
        messages = kwargs["messages"]
        calls.append(deepcopy(messages))
        assert messages[0]["role"] == "system"
        assert all(m["role"] not in {"system", "developer"} for m in messages[1:])
        assert messages[0]["content"].count("Пользователь прислал уточнение во время текущей задачи.") == 1
        assert {"role": "user", "content": first_input} in messages
        if len(calls) == 1:
            control.queue_session_input(run, "chat-a", "b" * 32, second_input)
            return {"message": {"content": "Устаревший ответ", "tool_calls": []}}
        assert {"role": "user", "content": second_input} in messages
        return {"message": {"content": "Ок, отвечу коротко. Всё хорошо.", "tool_calls": []}}

    events = []
    for event in stream_delivery_session(
        user_message="Да ты ж моя сладкая", project_root=tmp_path,
        conversation_history=[{"role": "user", "content": "Красавица ты тут?"}],
        run_id=run, session_id="chat-a", chat_fn=chat, auto_remember=False,
    ):
        events.append(event)
        if event["type"] == "step_started" and event["step"] == 1:
            control.queue_session_input(run, "chat-a", "a" * 32, first_input)
    assert len(calls) == 2
    assert events[-1]["ok"], events[-1]
    assert events[-1]["run_id"] == run
    assert [e["text"] for e in events if e["type"] == "user_input_applied"] == [first_input, second_input]
    assert next(e for e in events if e["type"] == "final_response")["text"] == "Ок, отвечу коротко. Всё хорошо."
    assert next(e for e in events if e["type"] == "user_input_reply")["request_ids"] == ["a" * 32, "b" * 32]

