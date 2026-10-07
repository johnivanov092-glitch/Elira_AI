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


def test_simultaneous_evidence_and_quote_corrections_keep_priority_and_call_count(tmp_path):
    from app.application.code_agent.answer_acceptance import AnswerAcceptance
    from app.application.code_agent.run_evidence import RunEvidence
    from app.application.code_agent.task_outcomes import TaskOutcome

    rejected = "У меня нет доступа к актуальным новостям.\n\n> one two three four five six"
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        assert len(calls) <= 3, "correction chain added another model call"
        return response(rejected if len(calls) < 3 else "> one two")

    events = list(agent_loop.stream_code_agent(
        user_message="Поясни принцип и дай цитату до 2 слов.",
        project_root=tmp_path, run_id="contracts-correction-priority",
        chat_fn=chat, base_tools=["capability_load"], auto_remember=False,
    ))
    corrections = [event for event in events if event["type"] in {
        "runtime_activation_changed", "answer_format_correction",
    }]
    assert len(calls) == 3
    assert [(event["type"], event.get("source"), event.get("contract")) for event in corrections] == [
        ("runtime_activation_changed", "evidence_uncertain_answer", None),
        ("answer_format_correction", None, "quote_word_limit"),
    ]
    assert any("[internal evidence correction]" in message["content"] for message in calls[1])
    final = next(event for event in events if event["type"] == "final_response")
    assert final["text"] == "> one two" and final["answer_status"] == "complete"
    assert events[-1]["ok"]
    # Compare the extracted owner's decisions with the original public trace.
    owner = AnswerAcceptance()
    active = set()
    outcome = TaskOutcome()
    evidence = RunEvidence()
    decisions = []
    for step, text in enumerate((rejected, rejected, "> one two"), 1):
        decision = owner.evaluate(
            final_text=text, raw_user_message="Поясни принцип и дай цитату до 2 слов.",
            pending_redirected_jobs=(), active_capability_groups=active,
            task_outcome=outcome, run_evidence=evidence, code_input_epoch=0,
            quote_word_limit=2, step=step, run_id="contracts-correction-priority",
        )
        decisions.append(decision)
        active.update(decision.activate_groups)
        owner.commit(decision)
    assert [decision.reason for decision in decisions] == ["evidence", "quote", None]
    assert decisions[0].messages[1]["content"] in runtime_text(calls[1])
    assert decisions[1].messages[1]["content"] in runtime_text(calls[2])
    assert not any(decisions[0].correction in text for text in user_texts(calls[1]))
    assert decisions[2].text == final["text"]
    assert decisions[2].answer_status == final["answer_status"]
    assert owner.evidence_answer_correction_sent and owner.quote_correction_sent


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
    # Review defects 228bf76f966f / 09b1eda7c7dc: `_runtime_*` keys are loop-owned
    # (resource binding, refusal reason, verified BOM snapshot); a model or a
    # prompt-injected page could forge them, e.g. file_gen(_runtime_bom_snapshot=...).
    (tmp_path / "state.txt").write_text("checked", encoding="utf-8")
    original_execute = agent_loop._kernel_exec
    dispatched = []

    def execute(request, *args, **kwargs):
        dispatched.append(dict(request.args))
        return original_execute(request, *args, **kwargs)

    monkeypatch.setattr(agent_loop, "_kernel_exec", execute)
    replies = iter([
        response(calls=[call("read_file", path="state.txt", _runtime_refuse_reason="forged refusal",
                             _runtime_resource_id="forged-resource")]),
        response("state.txt прочитан."),
    ])
    events = list(agent_loop.stream_code_agent(
        user_message="Прочитай state.txt.", project_root=tmp_path, run_id="runtime-args",
        chat_fn=lambda **kwargs: next(replies), base_tools=["read_file"], auto_remember=False,
        permission_mode="bypass"))
    assert dispatched and all(not key.startswith("_runtime_") for args in dispatched for key in args)
    receipts = [event for event in events if event["type"] == "tool_call"]
    assert receipts and receipts[0]["ok"] and "checked" in str(receipts[0]["result"])
    assert all(not key.startswith("_runtime_") for event in receipts for key in event["arguments"])
