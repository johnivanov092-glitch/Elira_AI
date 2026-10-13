"""Characterize facade and ownership boundaries before the mechanical split."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import threading

import pytest

from _runtime_roles import base_system, runtime_text, user_texts
from app.api.routes import code_agent_routes
from app.application.code_agent import agent_loop, delivery_session, model_turn, run_control
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent import tools
from app.application.code_agent.tools import _run
from app.application.event_bus import runtime as event_bus
from app.application.workflows import db_path as workflow_db_path, store
from app.core import release_runtime


@pytest.fixture(autouse=True)
def private_run_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "journals"))
    original_db = workflow_db_path.get_workflow_db_path()
    workflow_db_path.set_workflow_db_path(tmp_path / "workflow.db")
    monkeypatch.setattr(event_bus, "DB_PATH", tmp_path / "events.db")
    store.init_db(db_path=workflow_db_path.get_workflow_db_path())
    event_bus._init_db()
    try:
        yield
    finally:
        workflow_db_path.set_workflow_db_path(original_db)


def response(text="", calls=()):
    return {"message": {"content": text, "tool_calls": list(calls)}}


def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


# John 2026-10-12: only the Web tools have a repeat limit. Everything below runs
# without a call counter — the fake model ends each scenario with a text answer.

def test_repeated_reads_run_without_a_limit(tmp_path):
    (tmp_path / "state.txt").write_text("checked", encoding="utf-8")
    run_id = "contracts-repeat-free"
    model_messages = []

    def chat(**kwargs):
        model_messages.append(deepcopy(kwargs["messages"]))
        if len(model_messages) == 8:
            return response("Файл прочитан: checked.")
        return response(calls=[call("read_file", path="state.txt")])

    events = list(agent_loop.stream_code_agent(
        user_message="Прочитай state.txt.", project_root=tmp_path, run_id=run_id,
        chat_fn=chat, base_tools=["read_file"], auto_remember=False, permission_mode="bypass",
    ))
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert len(receipts) == 7 and all(receipt["ok"] for receipt in receipts)
    assert not any("3 раза" in str(receipt["result"]) for receipt in receipts)
    assert events[-1]["stop_reason"] == "answer"
    assert RunJournal.load(run_id).state["command_progress"]["calls"] == {}


@pytest.mark.parametrize("interleave", [False, True], ids=["consecutive", "interleaved"])
def test_repeated_read_only_denials_never_dispatch(tmp_path, monkeypatch, interleave):
    run_id = "contracts-repeat-read-only-denied"
    model_messages = []
    repeated_call = call("write_file", path="forbidden.txt", content="write")
    attempted_calls = []
    for index in range(6 if interleave else 5):
        if interleave and index:
            attempted_calls.append(call("run_bash", command=f"denied-command-{index}"))
        attempted_calls.append(repeated_call)

    def no_dispatch(*args, **kwargs):
        pytest.fail("read-only denial must not reach the executor")

    def chat(**kwargs):
        model_messages.append(deepcopy(kwargs["messages"]))
        if len(model_messages) > len(attempted_calls):
            return response("Изменения в режиме чтения запрещены.")
        return response(calls=[attempted_calls[len(model_messages) - 1]])

    monkeypatch.setattr(agent_loop, "_kernel_exec", no_dispatch)
    events = list(agent_loop.stream_code_agent(
        user_message="Только изучи проект.", project_root=tmp_path, run_id=run_id,
        chat_fn=chat, base_tools=["write_file", "run_bash"], auto_remember=False,
        permission_mode="bypass", read_only=True,
    ))
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert len(receipts) == len(attempted_calls)
    assert all(event["error"] == "delegate_read_only" for event in receipts)
    assert all(event["dispatched"] is False and event["state_changed"] is False for event in receipts)
    assert events[-1]["stop_reason"] == "answer"
    assert not (tmp_path / "forbidden.txt").exists()


def test_repeated_persistence_denials_never_dispatch(tmp_path, monkeypatch):
    run_id = "contracts-repeat-persistence-denied"
    model_messages = []

    def no_dispatch(*args, **kwargs):
        pytest.fail("task persistence denial must not reach the executor")

    def chat(**kwargs):
        model_messages.append(deepcopy(kwargs["messages"]))
        if len(model_messages) <= 3:
            return response(calls=[call("memory", action="add", text="Запрещённый факт.")])
        return response("Долговременная память не изменена.")

    monkeypatch.setattr(agent_loop, "_kernel_exec", no_dispatch)
    events = list(agent_loop.stream_code_agent(
        user_message="Не сохраняй информацию в память.", project_root=tmp_path,
        run_id=run_id, chat_fn=chat, base_tools=["memory"], auto_remember=False,
        permission_mode="bypass",
    ))
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert len(receipts) == 3
    assert all(event["error"] == "task_persistence_denied" for event in receipts)
    assert all(event["dispatched"] is False and event["execution_status"] == "rejected"
               for event in receipts)
    assert events[-1]["stop_reason"] == "answer"


def test_recovered_read_paths_keep_working_on_repeat(tmp_path):
    nested = tmp_path / "folder"
    nested.mkdir()
    (nested / "state_report.txt").write_text("checked", encoding="utf-8")
    model_messages = []

    def chat(**kwargs):
        model_messages.append(deepcopy(kwargs["messages"]))
        if len(model_messages) == 1:
            return response(calls=[call("glob", pattern="**/state*.txt")])
        if len(model_messages) == 7:
            return response("Прочитано: checked.")
        return response(calls=[call("read_file", path="folder/state.txt")])

    events = list(agent_loop.stream_code_agent(
        user_message="Найди и прочитай state.txt.", project_root=tmp_path,
        chat_fn=chat, base_tools=["glob", "read_file"], auto_remember=False,
        permission_mode="bypass",
    ))
    receipts = [event for event in events if event["type"] == "tool_call" and event["tool"] == "read_file"]
    assert len(receipts) == 5 and all(event["ok"] for event in receipts)
    assert receipts[0]["arguments"]["path"] == "folder/state_report.txt"
    assert events[-1]["stop_reason"] == "answer"


@pytest.mark.parametrize(("name", "arguments"), [
    ("ask_user", {"question": "Как продолжить?"}),
    ("workflow_request", {"kind": "input", "message": "Как продолжить?", "schema": {}}),
    ("workflow_request", {"kind": "elevation", "message": "Нет программы."}),
])
def test_special_workflow_calls_are_not_counted(tmp_path, monkeypatch, name, arguments):
    run_id = "contracts-repeat-workflow"
    model_messages = []

    def no_dispatch(*args, **kwargs):
        pytest.fail("special Workflow must not reach the tool executor")

    def chat(**kwargs):
        model_messages.append(deepcopy(kwargs["messages"]))
        if len(model_messages) == 7:
            return response("Без ответа продолжаю по имеющимся данным.")
        return response(calls=[call(name, **arguments)])

    monkeypatch.setattr(agent_loop, "_kernel_exec", no_dispatch)
    events = []
    for event in agent_loop.stream_code_agent(
        user_message="Уточни дальнейшие действия.", project_root=tmp_path,
        run_id=run_id, chat_fn=chat, auto_remember=False, permission_mode="bypass",
    ):
        events.append(event)
        if event["type"] == "workflow_request":
            assert agent_loop.submit_workflow_response(event["response_id"], "decline", {})
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert len(receipts) == 6
    assert not any(event["ok"] for event in receipts)
    assert not any(event.get("error") == "repeated_tool_call" for event in receipts)
    assert events[-1]["stop_reason"] == "answer"
    assert RunJournal.load(run_id).state["command_progress"]["calls"] == {}


def test_runtime_workflow_receipts_are_not_counted(tmp_path, monkeypatch):
    from app.application.agent_kernel.executor import ToolExecutionResult

    model_messages = []
    executions = []

    def execute(request, **kwargs):
        executions.append(request)
        return ToolExecutionResult(status="ok", output={
            "ok": False, "status": "needs_input", "text": "Требуются данные.",
            "request": {"kind": "input", "message": "Введи данные.", "schema": {}},
        })

    def chat(**kwargs):
        model_messages.append(deepcopy(kwargs["messages"]))
        if len(model_messages) == 7:
            return response("Данных нет — продолжить нечем.")
        return response(calls=[call("read_file", path="state.txt")])

    monkeypatch.setattr(agent_loop, "_kernel_exec", execute)
    events = []
    for event in agent_loop.stream_code_agent(
        user_message="Прочитай state.txt.", project_root=tmp_path, chat_fn=chat,
        base_tools=["read_file"], auto_remember=False, permission_mode="bypass",
    ):
        events.append(event)
        if event["type"] == "workflow_request":
            assert agent_loop.submit_workflow_response(event["response_id"], "decline", {})
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert len(receipts) == 6 and len(executions) == 6
    assert not any(event.get("error") == "repeated_tool_call" for event in receipts)
    assert events[-1]["stop_reason"] == "answer"


@pytest.mark.parametrize(("name", "arguments", "waiting_status"), [
    ("ask_user", {"question": "Как продолжить?"}, "needs_input"),
    ("workflow_request", {"kind": "input", "message": "Введи данные."}, "needs_input"),
    ("run_bash", {"command": "echo forbidden"}, "waiting_approval"),
])
def test_paused_workflow_calls_resume_without_a_count(tmp_path, monkeypatch, name, arguments, waiting_status):
    run_id = "contracts-repeat-paused-workflow"

    def chat(**kwargs):
        return response(calls=[call(name, **arguments)])

    monkeypatch.setattr("app.application.code_agent.tools._dispatch.tool_run_bash",
                        lambda *args, **kwargs: pytest.fail("paused approval reached handler"))
    kwargs = dict(user_message="Уточни дальнейшие действия.", project_root=tmp_path,
                  run_id=run_id, chat_fn=chat, auto_remember=False,
                  permission_mode="ask", pause_for_workflow_request=True)
    for _ in range(7):
        events = list(agent_loop.stream_code_agent(**kwargs))
        assert events[-1]["stop_reason"] == "workflow_request"
        state = RunJournal.load(run_id).state
        assert state["command_progress"]["calls"] == {}
        receipt = next(event for event in events if event["type"] == "tool_call")
        assert receipt["execution_status"] == waiting_status and receipt["dispatched"] is False
        assert "3 раза" not in str(receipt["result"])
        kwargs = build_continuation_kwargs(run_id, chat_fn=chat)
        kwargs["pause_for_workflow_request"] = True


@pytest.mark.parametrize("raw_args", [[], None, False, 0, "[1]", "null", "false", "{bad JSON"])
def test_invalid_native_argument_shapes_are_rejected_without_coercion(tmp_path, monkeypatch, raw_args):
    from app.application.agent_kernel.executor import ToolExecutionResult

    executions = []
    calls = 0

    def execute(request, **kwargs):
        executions.append(request)
        return ToolExecutionResult(status="error", output={"ok": False, "text": "Unexpected execution."})

    def chat(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            return response("Не удалось вызвать project_map.")
        return response(calls=[{"function": {"name": "project_map", "arguments": raw_args}}])

    monkeypatch.setattr(agent_loop, "_kernel_exec", execute)
    events = list(agent_loop.stream_code_agent(
        user_message="Изучи проект.", project_root=tmp_path, chat_fn=chat,
        base_tools=["project_map"], auto_remember=False, permission_mode="ask",
        pause_for_workflow_request=True,
    ))
    assert not executions
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert len(receipts) == 3
    assert all(event["error"] == "invalid_tool_arguments" and event["dispatched"] is False
               for event in receipts)
    assert not any(event["type"] in {"tool_started", "workflow_request"} for event in events)
    assert events[-1]["stop_reason"] == "answer"


def test_encoded_native_arguments_and_foreign_coercion_keep_existing_contract(tmp_path, monkeypatch):
    from app.application.agent_kernel.executor import ToolExecutionResult

    (tmp_path / "note.txt").write_text("checked", encoding="utf-8")
    replies = iter([
        response(calls=[{"function": {"name": "read_file", "arguments": '{"path":"note.txt"}'}}]),
        response("Прочитано."),
    ])
    events = list(agent_loop.stream_code_agent(
        user_message="Прочитай note.txt.", project_root=tmp_path, chat_fn=lambda **kwargs: next(replies),
        base_tools=["read_file"], auto_remember=False, permission_mode="bypass",
    ))
    assert next(event for event in events if event["type"] == "tool_call")["ok"]
    seen = []

    def execute(request, **kwargs):
        seen.append((request.tool_name, request.args))
        return ToolExecutionResult(status="ok", output={"ok": True, "text": "Provider result."})

    monkeypatch.setattr(agent_loop, "_kernel_exec", execute)
    replies = iter([
        response(calls=[{"function": {"name": "paper_search__search_arxiv", "arguments": ["legacy"]}}]),
        response("Результат получен."),
    ])
    list(agent_loop.stream_code_agent(
        user_message="Используй внешний инструмент.", project_root=tmp_path,
        chat_fn=lambda **kwargs: next(replies), auto_remember=False, permission_mode="bypass",
    ))
    assert seen == [("paper_search__search_arxiv", {})]


@pytest.mark.parametrize(("name", "arguments", "read_only"), [
    ("ask_user", {"question": "Как продолжить?", "unexpected": True}, False),
    ("ask_user", {"question": 3}, False),
    ("ask_user", {}, False),
    ("ask_user", {"question": "Как продолжить?", "options": [3]}, False),
    ("workflow_request", {"kind": "unknown", "message": "Ввод."}, False),
    ("workflow_request", {"kind": "input", "message": 3}, False),
    ("workflow_request", {"kind": "input", "message": "Ввод.", "schema": []}, False),
    ("workflow_request", {"kind": "input", "message": "Ввод.", "unexpected": True}, False),
    ("workflow_request", {"message": "Ввод."}, False),
    ("write_file", {"path": "bad.txt", "content": 3}, True),
    ("memory", {"action": "add", "text": 3}, False),
])
def test_invalid_calls_are_observed_before_workflow_and_policy_denials(
    tmp_path, monkeypatch, name, arguments, read_only,
):
    model_messages = []

    def no_dispatch(*args, **kwargs):
        pytest.fail("invalid call reached executor")

    def chat(**kwargs):
        model_messages.append(deepcopy(kwargs["messages"]))
        if len(model_messages) == 4:
            return response("Вызов не прошёл проверку аргументов.")
        return response(calls=[call(name, **arguments)])

    monkeypatch.setattr(agent_loop, "_kernel_exec", no_dispatch)
    events = list(agent_loop.stream_code_agent(
        user_message="Не сохраняй информацию; только изучи задачу.", project_root=tmp_path,
        chat_fn=chat, auto_remember=False, permission_mode="ask", read_only=read_only,
        pause_for_workflow_request=True,
    ))
    assert not any(event["type"] in {"workflow_request", "tool_started"} for event in events)
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert len(receipts) == 3
    assert all(event["error"] == "invalid_tool_arguments" and event["argument_errors"]
               for event in receipts)
    assert all(event["dispatched"] is False and event["state_changed"] is False for event in receipts)
    assert "Пример аргументов" in receipts[0]["result"]
    assert events[-1]["stop_reason"] == "answer"


def test_stream_and_sync_facades_preserve_results_order_and_dispatch(tmp_path, monkeypatch):
    (tmp_path / "state.txt").write_text("checked", encoding="utf-8")
    original_execute = agent_loop._kernel_exec
    dispatches = []

    def execute(*args, **kwargs):
        dispatches.append((args, kwargs))
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(agent_loop, "_kernel_exec", execute)

    def scenario(run_id, sync):
        replies = iter([
            response(calls=[call("read_file", path="state.txt")]),
            response(calls=[call("read_file", path="absent.txt")]),
            response("state.txt прочитан; absent.txt отсутствует."),
        ])
        model_calls = []

        def chat(**kwargs):
            model_calls.append(deepcopy(kwargs["messages"]))
            return next(replies)

        kwargs = dict(user_message="Прочитай state.txt и absent.txt.",
                      project_root=tmp_path, run_id=run_id, chat_fn=chat,
                      base_tools=["read_file"], auto_remember=False,
                      permission_mode="bypass")
        before = len(dispatches)
        if sync:
            result = agent_loop.run_code_agent(**kwargs)
        else:
            events = list(agent_loop.stream_code_agent(**kwargs))
            kinds = [event["type"] for event in events]
            receipts = [event for event in events if event["type"] == "tool_call"]
            final = next(event for event in events if event["type"] == "final_response")
            done = events[-1]
            assert kinds.index("tool_started") < kinds.index("tool_call")
            assert kinds.index("final_response") < len(kinds) - 1
            assert done["type"] == "done"
            result = {**done, "response": final["text"], "tool_calls": receipts}
        assert len(model_calls) == 3
        assert len(dispatches) - before == 2
        assert all(messages[0]["role"] == "system" for messages in model_calls)
        assert all(message["role"] not in {"system", "developer"}
                   for messages in model_calls for message in messages[1:])
        return {
            "ok": result["ok"], "response": result["response"],
            "completion_status": result["completion_status"],
            "criteria_confirmed": result["criteria_confirmed"],
            "stop_reason": result["stop_reason"], "partial": result.get("partial", False),
            "tools": [(item["tool"], item["arguments"], item["ok"], item["result"])
                      for item in result["tool_calls"]],
        }

    streamed = scenario("contracts-stream", False)
    synchronous = scenario("contracts-sync", True)
    assert streamed == synchronous
    assert [item[2] for item in streamed["tools"]] == [True, False]


def test_duplicate_registration_preserves_public_run_owner_and_drain_accounting(tmp_path, monkeypatch):
    run_id = "contracts-duplicate"
    transitions = []
    original_begin = release_runtime.begin_agent_run
    original_end = release_runtime.end_agent_run

    def begin(value):
        transitions.append(("begin", value))
        original_begin(value)

    def end(value):
        transitions.append(("end", value))
        original_end(value)

    monkeypatch.setattr(release_runtime, "begin_agent_run", begin)
    monkeypatch.setattr(release_runtime, "end_agent_run", end)
    before = release_runtime.health_fields()["active_agent_runs"]
    stream = agent_loop.stream_code_agent(
        user_message="Привет", project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: response("Привет"), auto_remember=False,
    )
    try:
        assert next(stream)["type"] == "run_started"
        owner = run_control._CANCEL_REGISTRY[run_id]
        handle = run_control._cancel_handle_for(owner)
        assert release_runtime.health_fields()["active_agent_runs"] == before + 1
        with pytest.raises(RuntimeError, match="run is already active"):
            run_control._register_run(run_id)
        assert run_control._CANCEL_REGISTRY[run_id] is owner
        assert run_control._cancel_handle_for(owner) is handle
        assert not owner.is_set() and not handle.is_closed
        assert transitions == [("begin", run_id)]
    finally:
        agent_loop.request_cancel(run_id)
        stream.close()
    assert transitions == [("begin", run_id), ("end", run_id)]
    assert release_runtime.health_fields()["active_agent_runs"] == before


@pytest.mark.parametrize("transport_failure", [False, True])
def test_stop_closes_upstream_before_ack_and_attempts_every_cleanup(tmp_path, monkeypatch, transport_failure):
    run_id = "contracts-cancel"
    stages = []
    fail_close = transport_failure

    class Transport:
        def close(self):
            stages.append("transport")
            if fail_close:
                raise RuntimeError("synthetic transport close failure")

    monkeypatch.setattr(tools, "kill_run_processes", lambda value: stages.append("processes"))
    monkeypatch.setattr(tools, "cancel_run_callbacks", lambda value: stages.append("callbacks"))
    monkeypatch.setattr(_run, "stop_run_servers", lambda value: stages.append("servers") or [])
    monkeypatch.setattr(_run, "run_owned_servers", lambda value: [])
    stream = agent_loop.stream_code_agent(
        user_message="Привет", project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: response("Привет"), auto_remember=False,
    )
    try:
        assert next(stream)["type"] == "run_started"
        event = run_control._CANCEL_REGISTRY[run_id]
        run_control._cancel_handle_for(event).bind(Transport())
        if transport_failure:
            with pytest.raises(RuntimeError, match="live cleanup failed.*synthetic transport"):
                agent_loop.request_cancel(run_id)
        else:
            assert agent_loop.request_cancel(run_id)
            stages.append("ack")
        assert event.is_set()
        assert stages == ["transport", "processes", "callbacks", "servers"] + (
            [] if transport_failure else ["ack"]
        )
    finally:
        # Cleanup failure is retained for a real retry, including finally.
        fail_close = False
        agent_loop.request_cancel(run_id)
        stream.close()
    assert run_id not in run_control._CANCEL_REGISTRY


@pytest.mark.parametrize(("route_close", "retain_inner"), [
    (False, False), (True, False),
    (False, True), (True, True),
])
def test_consumer_close_releases_retained_core_provider_and_run_ownership(tmp_path, monkeypatch, route_close, retain_inner):
    run_id = "contracts-consumer-close"
    closed = threading.Event()
    worker_finished = threading.Event()
    retained = []
    original_core = agent_loop._stream_code_agent_core

    def retain_core(**kwargs):
        inner = original_core(**kwargs)
        if retain_inner:
            retained.append(inner)
        return inner

    class Transport:
        def close(self):
            closed.set()

    def provider(**kwargs):
        kwargs["options"]["_stream_cancel_handle"].bind(Transport())
        try:
            assert closed.wait(3), "consumer never closed the provider transport"
        finally:
            worker_finished.set()
        yield {"type": "message", "response": response("Поздний ответ")}

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", retain_core)
    monkeypatch.setattr(model_turn, "_LLM_HEARTBEAT_EVERY", 0.01)
    before = release_runtime.health_fields()["active_agent_runs"]
    raw = agent_loop.stream_code_agent(
        user_message="Привет", project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: pytest.fail("unexpected non-stream model call"),
        chat_stream_fn=provider, auto_remember=False,
    )
    stream = (code_agent_routes._stream_with_workflow_requests(
        raw, code_agent_run_id=run_id, permission_mode="bypass",
    ) if route_close else raw)
    try:
        for event in stream:
            if event["type"] == "heartbeat":
                break
        assert run_id in run_control._CANCEL_REGISTRY
        stream.close()
        assert closed.wait(0.5), "consumer close kept upstream alive"
        assert worker_finished.wait(0.5)
        assert run_id not in run_control._CANCEL_REGISTRY
        assert release_runtime.health_fields()["active_agent_runs"] == before
        assert RunJournal.load(run_id).state["status"] == "interrupted"
        resumed = list(agent_loop.stream_code_agent(**build_continuation_kwargs(
            run_id, chat_fn=lambda **kwargs: response("Возобновлено"),
        )))
        assert resumed[-1]["ok"] and resumed[-1]["stop_reason"] == "answer"
        assert resumed[0]["type"] == "run_resumed"
    finally:
        agent_loop.request_cancel(run_id)
        for inner in retained:
            inner.close()
        stream.close()
        closed.set()
        worker_finished.wait(1)


@pytest.mark.parametrize("route_close", [False, True])
def test_structured_delivery_close_releases_retained_public_and_core_iterators(tmp_path, monkeypatch, route_close):
    run_id = "contracts-structured-delivery-close"
    prompt = "Цель: создать private.py.\nКритерии готовности:\n- private.py существует"
    assert delivery_session._delivery_shaped(prompt, tmp_path)
    closed = threading.Event()
    worker_finished = threading.Event()
    retained_public, retained_core, dispatches = [], [], []
    original_public = delivery_session.stream_code_agent
    original_core = agent_loop._stream_code_agent_core

    def retain_public(**kwargs):
        inner = original_public(**kwargs)
        retained_public.append(inner)
        return inner

    def retain_core(**kwargs):
        inner = original_core(**kwargs)
        retained_core.append(inner)
        return inner

    class Transport:
        def close(self):
            closed.set()

    def provider(**kwargs):
        kwargs["options"]["_stream_cancel_handle"].bind(Transport())
        try:
            assert closed.wait(3), "delivery close never closed the provider transport"
            yield {"type": "message", "response": response(calls=[
                call("write_file", path="private.py", content="late dispatch\n"),
            ])}
        finally:
            worker_finished.set()

    def unexpected_dispatch(*args, **kwargs):
        dispatches.append((args, kwargs))
        pytest.fail("provider response dispatched after structured delivery close")

    monkeypatch.setattr(delivery_session, "stream_code_agent", retain_public)
    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", retain_core)
    monkeypatch.setattr(agent_loop, "_kernel_exec", unexpected_dispatch)
    monkeypatch.setattr(model_turn, "_LLM_HEARTBEAT_EVERY", 0.01)
    before = release_runtime.health_fields()["active_agent_runs"]
    raw = delivery_session.stream_delivery_session(
        user_message=prompt, project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: pytest.fail("unexpected non-stream model call"),
        chat_stream_fn=provider, base_tools=["write_file"],
        auto_remember=False, permission_mode="bypass",
    )
    stream = (code_agent_routes._stream_with_workflow_requests(
        raw, code_agent_run_id=run_id, permission_mode="bypass",
    ) if route_close else raw)
    try:
        for event in stream:
            if event["type"] == "heartbeat":
                break
        assert len(retained_public) == len(retained_core) == 1
        assert run_id in run_control._CANCEL_REGISTRY
        assert delivery_session._session_active(run_id)
        stream.close()
        assert closed.wait(0.5), "structured delivery close kept upstream alive"
        assert worker_finished.wait(0.5), "structured delivery provider kept running"
        assert run_id not in run_control._CANCEL_REGISTRY
        assert release_runtime.health_fields()["active_agent_runs"] == before
        assert not delivery_session._session_active(run_id)
        assert RunJournal.load(run_id).state["status"] == "interrupted"
        assert dispatches == [] and not (tmp_path / "private.py").exists()
    finally:
        agent_loop.request_cancel(run_id)
        for inner in retained_public:
            inner.close()
        for inner in retained_core:
            inner.close()
        stream.close()
        raw.close()
        closed.set()
        worker_finished.wait(1)


@pytest.mark.parametrize("route_close", [False, True])
def test_close_at_done_releases_retained_iterators_without_cancelling_success(tmp_path, monkeypatch, route_close):
    run_id = "contracts-close-at-done"
    retained = []
    original_core = agent_loop._stream_code_agent_core

    def retain_core(**kwargs):
        inner = original_core(**kwargs)
        retained.append(inner)
        return inner

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", retain_core)
    before = release_runtime.health_fields()["active_agent_runs"]
    raw = agent_loop.stream_code_agent(
        user_message="Привет", project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: response("Привет"), auto_remember=False,
    )
    stream = (code_agent_routes._stream_with_workflow_requests(
        raw, code_agent_run_id=run_id, permission_mode="bypass",
    ) if route_close else raw)
    try:
        for event in stream:
            if event["type"] == "done":
                assert event["ok"] and event["stop_reason"] == "answer"
                break
        assert run_id in run_control._CANCEL_REGISTRY
        owner = run_control._CANCEL_REGISTRY[run_id]
        stream.close()
        assert run_id not in run_control._CANCEL_REGISTRY
        assert not owner.is_set(), "closing successful output must not request Stop"
        assert release_runtime.health_fields()["active_agent_runs"] == before
        assert RunJournal.load(run_id).state["status"] == "completed"
    finally:
        stream.close()
        raw.close()
        for inner in retained:
            inner.close()


def test_public_close_finishes_journal_when_inner_close_raises(tmp_path, monkeypatch):
    run_id = "contracts-close-error"

    def broken_core(**kwargs):
        try:
            yield {"type": "run_started", "run_id": run_id}
        finally:
            raise RuntimeError("synthetic inner cleanup failure")

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", broken_core)
    stream = agent_loop.stream_code_agent(
        user_message="Привет", project_root=tmp_path, run_id=run_id,
        auto_remember=False,
    )
    assert next(stream)["type"] == "run_started"
    with pytest.raises(RuntimeError, match="synthetic inner cleanup failure"):
        stream.close()
    assert RunJournal.load(run_id).state["status"] == "interrupted"


def test_close_at_tool_started_prevents_dispatch(tmp_path, monkeypatch):
    run_id = "contracts-close-before-dispatch"
    monkeypatch.setattr(agent_loop, "_kernel_exec", lambda *args, **kwargs: pytest.fail("late dispatch after close"))
    before = release_runtime.health_fields()["active_agent_runs"]
    stream = agent_loop.stream_code_agent(
        user_message="Прочитай state.txt.", project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: response(calls=[call("read_file", path="state.txt")]),
        base_tools=["read_file"], auto_remember=False, permission_mode="bypass",
    )
    try:
        for event in stream:
            if event["type"] == "tool_started":
                break
        assert event["type"] == "tool_started"
    finally:
        stream.close()
    assert run_id not in run_control._CANCEL_REGISTRY
    assert release_runtime.health_fields()["active_agent_runs"] == before
    assert RunJournal.load(run_id).state["status"] == "interrupted"


def test_close_at_run_resumed_finishes_journal_before_core_creation(tmp_path, monkeypatch):
    run_id = "contracts-close-resume-boundary"
    initial = agent_loop.stream_code_agent(
        user_message="Привет", project_root=tmp_path, run_id=run_id,
        chat_fn=lambda **kwargs: response("Привет"), auto_remember=False,
    )
    assert next(initial)["type"] == "run_started"
    initial.close()
    before = release_runtime.health_fields()["active_agent_runs"]
    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", lambda **kwargs: pytest.fail("Resume core was started after close"))
    stream = agent_loop.stream_code_agent(**build_continuation_kwargs(run_id))
    assert next(stream)["type"] == "run_resumed"
    stream.close()
    assert run_id not in run_control._CANCEL_REGISTRY
    assert release_runtime.health_fields()["active_agent_runs"] == before
    assert RunJournal.load(run_id).state["status"] == "interrupted"


def test_resume_reuses_persisted_plan_without_another_planner_or_execution_call(tmp_path):
    run_id = "contracts-plan-resume"
    (tmp_path / "state.txt").write_text("checked", encoding="utf-8")
    plan = {"goal": "Проверить файл", "current_state": "Файл существует",
            "ordered_steps": ["Прочитать state.txt"],
            "acceptance_checks": ["state.txt существует"], "risks": [],
            "capability_groups": [], "current_step": 1}
    planning_calls = []
    execution_calls = []

    def chat(**kwargs):
        if not kwargs.get("tools"):
            planning_calls.append(deepcopy(kwargs["messages"]))
            assert len(planning_calls) == 1, "Resume invoked the planner again"
            return response(json.dumps(plan, ensure_ascii=False))
        execution_calls.append(deepcopy({key: kwargs[key] for key in ("messages", "tools")}))
        if len(execution_calls) == 1:
            return response(calls=[call("read_file", path="state.txt")])
        assert len(execution_calls) == 2, "unexpected additional execution call"
        assert agent_loop.request_cancel(run_id)
        return response("Поздний ответ после Stop")

    first = []
    for event in agent_loop.stream_code_agent(
        user_message="Цель: проверить файл.\nКритерии готовности:\n- state.txt существует",
        project_root=tmp_path, run_id=run_id, chat_fn=chat,
        base_tools=["read_file"], auto_remember=False,
        permission_mode="bypass", reasoning_effort="medium",
    ):
        first.append(event)
        if event["type"] == "tool_call":
            assert event["ok"]
            assert agent_loop.request_cancel(run_id)
    assert first[-1]["stop_reason"] == "cancelled"
    assert RunJournal.load(run_id).state["plan"] == plan
    restored = build_continuation_kwargs(run_id, chat_fn=chat)
    assert restored["permission_mode"] == "bypass"
    assert restored["reasoning_effort"] == "medium"
    resumed = list(agent_loop.stream_code_agent(**restored))
    assert resumed[-1]["stop_reason"] == "cancelled"
    assert not any(event["type"] == "planning_started" for event in resumed)
    assert any(event.get("applied_thinking_mode") == "plan_reused" for event in resumed)
    assert len(planning_calls) == 1 and len(execution_calls) == 2
    # The stable persona prompt survives Resume; runtime context may differ.
    assert base_system(execution_calls[0]["messages"]) == base_system(execution_calls[1]["messages"])
    assert execution_calls[0]["tools"] == execution_calls[1]["tools"]
    for invocation in execution_calls:
        assert invocation["messages"][0]["role"] == "system"
        assert all(message["role"] not in {"system", "developer"}
                   for message in invocation["messages"][1:])


def test_first_visible_mutation_receipt_has_updated_durable_observations(tmp_path):
    run_id = "contracts-receipt-crossing"
    model_calls = []

    def chat(**kwargs):
        model_calls.append(deepcopy(kwargs["messages"]))
        assert len(model_calls) == 1, "consumer close permitted another model call"
        return response(calls=[call("write_file", path="private.py", content='print("private")\n')])

    stream = agent_loop.stream_code_agent(
        user_message="Создай private.py.", project_root=tmp_path, run_id=run_id,
        chat_fn=chat, base_tools=["write_file"], auto_remember=False,
        permission_mode="bypass",
    )
    seen = []
    try:
        for event in stream:
            seen.append(event)
            if event["type"] != "tool_call":
                continue
            assert event["tool"] == "write_file" and event["ok"]
            assert event["state_changed"] and event["code_input_epoch"] == 1
            state = RunJournal.load(run_id).state
            assert state["code_input_epoch"] == state["project_epoch"] == 1
            assert state["criteria_epoch"] == -1
            assert state["criteria"] == [] and state["verifications"] == []
            assert state["task_outcome"] == event["task_outcome"]
            assert state["command_progress"] == event["command_progress"]
            assert str(tmp_path / "private.py") in state["task_outcome"]["sources"]
            break
        assert any(event["type"] == "tool_call" for event in seen)
        assert not any(event["type"] == "final_response" for event in seen)
    finally:
        agent_loop.request_cancel(run_id)
        stream.close()
    assert len(model_calls) == 1
    assert RunJournal.load(run_id).state["status"] == "interrupted"


def test_quote_correction_without_retired_web_escalation(tmp_path):
    from app.application.code_agent.answer_acceptance import AnswerAcceptance
    from app.application.code_agent.run_evidence import RunEvidence
    from app.application.code_agent.task_outcomes import TaskOutcome

    rejected = "У меня нет доступа к актуальным новостям.\n\n> one two three four five six"
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        assert len(calls) <= 3, "correction chain added another model call"
        return response(rejected if len(calls) < 2 else "> one two")

    events = list(agent_loop.stream_code_agent(
        user_message="Поясни принцип и дай цитату до 2 слов.",
        project_root=tmp_path, run_id="contracts-correction-priority",
        chat_fn=chat, base_tools=["capability_load"], auto_remember=False,
    ))
    corrections = [event for event in events if event["type"] in {
        "runtime_activation_changed", "answer_format_correction",
    }]
    assert len(calls) == 2
    assert [(event["type"], event.get("source"), event.get("contract")) for event in corrections] == [
        ("answer_format_correction", None, "quote_word_limit"),
    ]
    assert not any("[internal evidence correction]" in message["content"] for message in calls[1])
    final = next(event for event in events if event["type"] == "final_response")
    assert final["text"] == "> one two" and final["answer_status"] == "complete"
    assert events[-1]["ok"]
    # Compare the extracted owner's decisions with the original public trace.
    owner = AnswerAcceptance()
    active = set()
    outcome = TaskOutcome()
    evidence = RunEvidence()
    decisions = []
    for step, text in enumerate((rejected, "> one two"), 1):
        decision = owner.evaluate(
            final_text=text, raw_user_message="Поясни принцип и дай цитату до 2 слов.",
            pending_redirected_jobs=(), active_capability_groups=active,
            task_outcome=outcome, run_evidence=evidence, code_input_epoch=0,
            quote_word_limit=2, step=step, run_id="contracts-correction-priority",
        )
        decisions.append(decision)
        active.update(decision.activate_groups)
        owner.commit(decision)
    assert [decision.reason for decision in decisions] == ["quote", None]
    assert decisions[0].messages[1]["content"] in runtime_text(calls[1])
    assert not any(decisions[0].correction in text for text in user_texts(calls[1]))
    assert decisions[1].text == final["text"]
    assert decisions[1].answer_status == final["answer_status"]
    assert not owner.evidence_answer_correction_sent and owner.quote_correction_sent


def verification_fixture(target, criterion="private input checked"):
    """A canonical typed result from a private deterministic registry handler."""
    from app.application.code_agent.tools._tool_contract import completed

    report = target.parent / "verification.json"
    checks = [{"name": criterion, "passed": True}]
    report.write_text(json.dumps({"checks": checks}), encoding="utf-8")
    return completed("result_verify", {"verification": {
        "kind": "command_check", "status": "passed", "command": "private-fixture",
        "exit_code": 0, "checks": checks,
        "targets": [{"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}],
        "report_path": str(report), "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
    }})


def test_model_supplied_runtime_arguments_never_reach_the_tool_or_events(tmp_path, monkeypatch):
    # Runtime bindings belong to trusted context. Unknown model fields must
    # produce a schema error before dispatch, including forged internal keys.
    (tmp_path / "state.txt").write_text("checked", encoding="utf-8")
    monkeypatch.setattr("app.application.code_agent.tools._dispatch.tool_read_file",
                        lambda *args, **kwargs: pytest.fail("forged arguments reached handler"))
    replies = iter([
        response(calls=[call("read_file", path="state.txt", _runtime_refuse_reason="forged refusal",
                             _runtime_resource_id="forged-resource")]),
        response("state.txt прочитан."),
    ])
    events = list(agent_loop.stream_code_agent(
        user_message="Прочитай state.txt.", project_root=tmp_path, run_id="runtime-args",
        chat_fn=lambda **kwargs: next(replies), base_tools=["read_file"], auto_remember=False,
        permission_mode="bypass"))
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert receipts and receipts[0]["ok"] is False
    assert receipts[0]["error"] == "invalid_tool_arguments"
    assert all(not key.startswith("_runtime_") for event in receipts for key in event["arguments"])
