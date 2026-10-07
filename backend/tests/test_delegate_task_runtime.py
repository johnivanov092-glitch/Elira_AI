"""Canonical delegated inspection shares ownership, never permissions to write."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from app.application.code_agent import agent_loop, run_control, task_skills
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.tools import _meta, _shell
from app.application.event_bus import runtime as event_bus
from app.application.task_planner import runtime as planner_runtime, service as planner_service
from app.application.workflows import db_path as workflow_db_path, store


def response(text="", calls=()):
    return {"message": {"content": text, "tool_calls": list(calls)}}


def call(tool_name, **arguments):
    return {"function": {"name": tool_name, "arguments": arguments}}


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "journals"))
    monkeypatch.setenv("ELIRA_SKILL_ADVISOR_MODE", "off")
    monkeypatch.setattr(planner_service, "DB_PATH", tmp_path / "planner.db")
    planner_runtime.init_db(connect_func=planner_service._connect)
    monkeypatch.setattr(event_bus, "DB_PATH", tmp_path / "events.db")
    event_bus._init_db()
    previous = workflow_db_path.get_workflow_db_path()
    workflow_db_path.set_workflow_db_path(tmp_path / "workflow.db")
    store.init_db(db_path=workflow_db_path.get_workflow_db_path())
    monkeypatch.setattr(task_skills, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(task_skills, "LEGACY_DEVELOPMENT", tmp_path / "developed-skills")
    yield
    workflow_db_path.set_workflow_db_path(previous)


def start_parent(root: Path, *, run_id="parent", permission_mode="bypass") -> RunJournal:
    journal = RunJournal(run_id)
    journal.start({"user_message": "Inspect the report without changing the CSV.",
                   "project_root": str(root), "permission_mode": permission_mode,
                   "num_ctx": 16384}, {"missing": []})
    return journal


def test_planner_accepts_advertised_review_role():
    result = planner_service.start_subagent_run(parent_run_id="parent", role="review", task="Review code")
    assert result["ok"] is True and result["role"] == "review"
    assert planner_service.finish_subagent_run(subagent_run_id=result["subagent_run_id"],
        status="completed", result_text="Read-only finding")["status"] == "completed"


@pytest.mark.parametrize("interrupted", [False, True])
def test_child_release_keeps_parent_owner_and_foreign_runs_blocked(tmp_path, interrupted):
    parent = start_parent(tmp_path)
    child = RunJournal("child", parent_run_id=parent.run_id)
    owner = parent.agent_lock_path.read_bytes()
    try:
        child.start({"project_root": str(tmp_path)}, {"missing": []})
        assert parent.agent_lock_path.read_bytes() == owner
        assert parent.lock_path.exists() and child.lock_path.exists()
        duplicate = RunJournal("child", parent_run_id=parent.run_id)
        with pytest.raises(RuntimeError, match="already active"):
            duplicate.start({"project_root": str(tmp_path)}, {"missing": []})
        duplicate.release()
        child.finish(interrupted=interrupted)
        assert parent.agent_lock_path.read_bytes() == owner
        assert RunJournal.active_state(parent.run_id)["status"] == "running"
        foreign = RunJournal("foreign")
        with pytest.raises(RuntimeError, match="another write run"):
            foreign.start({"project_root": str(tmp_path)}, {"missing": []})
        foreign.release()
        assert parent.agent_lock_path.read_bytes() == owner
    finally:
        child.release()
        parent.finish()
    assert not parent.agent_lock_path.exists()


def test_parent_finishing_first_retains_global_lease_until_child_finishes(tmp_path):
    parent = start_parent(tmp_path)
    child = RunJournal("child", parent_run_id=parent.run_id)
    child.start({"project_root": str(tmp_path)}, {"missing": []})
    owner = parent.agent_lock_path.read_bytes()
    try:
        parent.finish()
        assert parent.agent_lock_path.read_bytes() == owner
        with pytest.raises(RuntimeError, match="another write run"):
            RunJournal("foreign").start({"project_root": str(tmp_path)}, {"missing": []})
        with pytest.raises(RuntimeError, match="parent run is not active"):
            RunJournal("late-child", parent_run_id=parent.run_id).start(
                {"project_root": str(tmp_path)}, {"missing": []})
    finally:
        child.finish()
        parent.release()
    assert not parent.agent_lock_path.exists()


def test_parent_association_requires_same_project_and_live_owner_pid(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    parent = start_parent(project)
    original = parent.agent_lock_path.read_bytes()
    try:
        with pytest.raises(RuntimeError, match="parent's project root"):
            RunJournal("different-project", parent_run_id=parent.run_id).start(
                {"project_root": str(tmp_path)}, {"missing": []})
        foreign_owner = {"run_id": "foreign", "pid": os.getpid(), "mode": "write"}
        parent.agent_lock_path.write_text(json.dumps(foreign_owner), encoding="utf-8", newline="\n")
        with pytest.raises(RuntimeError, match="parent run is not active"):
            RunJournal("forged-owner", parent_run_id=parent.run_id).start(
                {"project_root": str(project)}, {"missing": []})
        assert json.loads(parent.agent_lock_path.read_text(encoding="utf-8")) == foreign_owner
        parent.agent_lock_path.write_bytes(original)
    finally:
        parent.finish()


@pytest.mark.parametrize("role", ["explore", "review"])
def test_actual_parent_delegate_reaches_canonical_child_and_preserves_project(tmp_path, monkeypatch, role):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "discount.py"
    source.write_text("def discounted(total):\n    return total * 0.10\n", encoding="utf-8", newline="\n")
    before = {path.name: path.read_bytes() for path in project.iterdir()}
    finding = "discount.py:2 returns the discount amount instead of the remaining 90%."
    original_run = agent_loop.run_code_agent
    child_results = []
    child_kwargs = []

    def run_child(**kwargs):
        child_kwargs.append(kwargs)
        assert _shell.get_current_run_id() == kwargs["parent_run_id"]
        count = 0
        def chat(**model_kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return response(calls=[call("read_file", path="discount.py")])
            assert "total * 0.10" in model_kwargs["messages"][-1]["content"]
            return response(finding)
        result = original_run(**kwargs, chat_fn=chat)
        assert _shell.get_current_run_id() == kwargs["parent_run_id"]
        child_results.append(result)
        return result

    monkeypatch.setattr(agent_loop, "run_code_agent", run_child)
    parent_id = "canonical-parent-" + role
    count = 0
    def parent_chat(**kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return response(calls=[call("delegate_task", role=role,
                task="Inspect discount.py for calculation errors without edits.", run_id="forged-parent")])
        assert finding in kwargs["messages"][-1]["content"]
        owner = RunJournal.load(parent_id).agent_lock_path
        assert json.loads(owner.read_text(encoding="utf-8"))["run_id"] == parent_id
        assert RunJournal.load(parent_id).lock_path.exists()
        return response(finding)

    result = original_run(user_message="Review discount.py without edits.", project_root=project,
        run_id=parent_id, chat_fn=parent_chat, auto_remember=False, permission_mode="bypass")
    assert result["ok"] is True and finding in result["response"]
    assert child_results[0]["ok"] is True
    assert child_results[0]["tool_calls"][0]["tool"] == "read_file"
    assert child_kwargs[0]["parent_run_id"] == parent_id
    assert child_kwargs[0]["read_only"] is True and child_kwargs[0]["permission_mode"] == "bypass"
    assert "Review discount.py without edits." in child_kwargs[0]["task_instructions"]
    assert {path.name: path.read_bytes() for path in project.iterdir()} == before
    subagents = planner_service.list_subagent_runs(parent_run_id=parent_id)["items"]
    assert len(subagents) == 1 and subagents[0]["status"] == "completed"
    assert subagents[0]["depth"] == 1
    assert not RunJournal.load(parent_id).agent_lock_path.exists()


def test_read_only_child_can_read_skill_but_cannot_execute_mutations_or_activation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "fixture.txt"
    source.write_text("preserved", encoding="utf-8")
    skill = task_skills.SKILLS_ROOT / "delegate-fixture"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: delegate-fixture\ndescription: Read-only review fixture\n---\n"
        "Inspect fixture.txt. Do not execute code.\n", encoding="utf-8", newline="\n")
    parent = start_parent(project, permission_mode="ask")
    original_run = agent_loop.run_code_agent
    child_results = []
    captured = []
    unsafe = [call("write_file", path="fixture.txt", content="changed"),
              call("run_bash", command="echo changed > fixture.txt"),
              call("runtime_control", operation="result_verify", config={"command": "echo changed > fixture.txt"}),
              call("runtime_control", operation="task_decide", config={
                  "disposition": "reuse", "targets": ["review-findings.md"],
                  "delivery": {"mode": "invalid", "reason": "bad payload", "targets": []}}),
              call("runtime_control", operation="skill_publish", config={}),
              call("mcp", action="start", server_id="foreign"),
              call("capability_load", group="shell"), call("unknown__execute", command="write"),
              call("delegate_task", role="review", task="Spawn another reviewer")]
    replies = iter([response(calls=[call("read_file", path=str(skill / "SKILL.md"))]),
                    response(calls=unsafe), response(calls=[call("read_file", path="fixture.txt")]),
                    response("fixture.txt remains preserved; unsafe requested operations were refused.")])
    def run_child(**kwargs):
        captured.append(kwargs)
        def chat(**model_kwargs):
            schemas = {item["function"]["name"]: item for item in model_kwargs["tools"]}
            assert set(schemas) <= _meta.DELEGATE_READ_TOOLS
            return next(replies)
        result = original_run(**kwargs, chat_fn=chat)
        child_results.append(result)
        return result
    monkeypatch.setattr(agent_loop, "run_code_agent", run_child)
    owner = parent.agent_lock_path.read_bytes()
    try:
        result = _meta.tool_delegate_task(project, run_id=parent.run_id, role="review", task="Inspect fixture.txt",
                                          permission_mode="bypass", num_ctx=65536)
        assert result["ok"] is True
        assert source.read_text(encoding="utf-8") == "preserved"
        assert parent.agent_lock_path.read_bytes() == owner
        assert captured[0]["permission_mode"] == "ask" and captured[0]["num_ctx"] == 16384
        assert "Inspect the report without changing the CSV." in captured[0]["task_instructions"]
        assert "Return findings directly; no report files or checker scripts." in captured[0]["task_instructions"]
        receipts = child_results[0]["tool_calls"]
        assert receipts[0]["ok"] is True and receipts[0]["skill"]["name"] == "delegate-fixture"
        assert all(row["ok"] is False for row in receipts[1:1 + len(unsafe)])
        child = RunJournal.load(result["subagent_run_id"])
        assert child.state["request"]["read_only"] is True
        assert child.state["request"]["parent_run_id"] == parent.run_id
        assert child.state["active_skills"][0]["name"] == "delegate-fixture"
        assert not child.state["task_outcome"]["delivery_attempts"]  # read-only child published nothing
    finally:
        parent.finish()
    replies = iter([response(calls=[call("write_file", path="fixture.txt", content="changed on Resume")]),
                    response("Resumed read-only inspection refused the write.")])
    resumed = original_run(user_message="Continue inspection.", project_root=project,
        run_id=result["subagent_run_id"], resume=True, chat_fn=lambda **_: next(replies),
        base_tools=["write_file"], auto_remember=False, permission_mode="bypass")
    assert resumed["ok"] is True and resumed["tool_calls"][0]["ok"] is False
    assert source.read_text(encoding="utf-8") == "preserved"


def test_delegation_rejects_spoofed_executor_parent_and_nested_depth(tmp_path):
    parent = start_parent(tmp_path, run_id="bounded-parent")
    child = RunJournal("bounded-child", parent_run_id=parent.run_id)
    owner = parent.agent_lock_path.read_bytes()
    try:
        binding = _shell.set_current_run_id("foreign-executor-run")
        try:
            denied = _meta.tool_delegate_task(tmp_path, run_id=parent.run_id, task="Inspect project")
        finally:
            _shell.reset_current_run_id(binding)
        assert denied["ok"] is False and denied["error"] == "parent_run_mismatch"
        child.start({"project_root": str(tmp_path), "delegation_depth": 1}, {"missing": []})
        denied = _meta.tool_delegate_task(tmp_path, run_id=child.run_id, task="Inspect project")
        assert denied["ok"] is False and "depth limit" in denied["error"]
        assert planner_service.list_subagent_runs(parent_run_id=parent.run_id)["items"] == []
        assert planner_service.list_subagent_runs(parent_run_id=child.run_id)["items"] == []
        assert parent.agent_lock_path.read_bytes() == owner
    finally:
        child.finish()
        parent.finish()


@pytest.mark.parametrize("refuse_close", [False, True])
def test_parent_stop_cancels_live_canonical_child_and_retries_failed_transport_cleanup(tmp_path, monkeypatch, refuse_close):
    project = tmp_path / "project"
    project.mkdir()
    (project / "fixture.txt").write_text("preserved", encoding="utf-8")
    original_run = agent_loop.run_code_agent
    entered = threading.Event()
    provider_released = threading.Event()
    child_done = threading.Event()
    parent_done = threading.Event()
    results = {}
    child_ids = []
    class Transport:
        allow_close = not refuse_close
        closed = False
        def close(self):
            if not self.allow_close:
                raise RuntimeError("cleanup refused")
            self.closed = True
            provider_released.set()
    transport = Transport()
    def run_child(**kwargs):
        child_ids.append(kwargs["run_id"])
        def chat(**model_kwargs):
            handle = model_kwargs["options"]["_stream_cancel_handle"]
            handle.bind(transport)
            entered.set()
            assert provider_released.wait(5), "provider cancellation was not propagated"
            handle.release(transport)
            return response("Provider returned after cancellation.")
        try:
            result = original_run(**kwargs, chat_fn=chat)
            results["child"] = result
            return result
        finally:
            child_done.set()
    monkeypatch.setattr(agent_loop, "run_code_agent", run_child)
    parent_id = "cancel-parent-refused" if refuse_close else "cancel-parent"
    def run_parent():
        try:
            results["parent"] = original_run(user_message="Inspect fixture.txt without edits.",
                project_root=project, run_id=parent_id, auto_remember=False, permission_mode="bypass",
                chat_fn=lambda **_: response(calls=[call("delegate_task", role="review", task="Inspect fixture.txt")]))
        finally:
            parent_done.set()
    thread = threading.Thread(target=run_parent, daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        assert parent_id in run_control._CANCEL_REGISTRY and child_ids[0] in run_control._CANCEL_REGISTRY
        if refuse_close:
            with pytest.raises(RuntimeError, match="cleanup refused"):
                agent_loop.request_cancel(parent_id)
            assert child_done.wait(5) and parent_done.wait(5)
            assert parent_id in _shell._RUN_CANCEL_CALLBACKS
            assert child_ids[0] in run_control._FAILED_CANCEL_HANDLES
            transport.allow_close = True
            agent_loop.request_cancel(parent_id)
        else:
            assert agent_loop.request_cancel(parent_id) is True
        assert child_done.wait(5) and parent_done.wait(5)
        assert transport.closed is True
        assert results["parent"]["stop_reason"] == results["child"]["stop_reason"] == "cancelled"
        assert parent_id not in run_control._CANCEL_REGISTRY and child_ids[0] not in run_control._CANCEL_REGISTRY
        assert parent_id not in _shell._RUN_CANCEL_CALLBACKS
        assert child_ids[0] not in run_control._FAILED_CANCEL_HANDLES
        assert not RunJournal.load(parent_id).agent_lock_path.exists()
        assert (project / "fixture.txt").read_text(encoding="utf-8") == "preserved"
    finally:
        transport.allow_close = True
        provider_released.set()
        if not parent_done.is_set():
            agent_loop.request_cancel(parent_id)
        thread.join(5)


def test_naturally_finished_child_with_failed_close_keeps_parent_cleanup_hook(tmp_path, monkeypatch):
    parent = start_parent(tmp_path, run_id="natural-failed-close-parent")
    original_run = agent_loop.run_code_agent
    class Transport:
        allow_close = False
        closed = False
        def close(self):
            if not self.allow_close:
                raise RuntimeError("natural close refused")
            self.closed = True
    transport = Transport()
    def run_child(**kwargs):
        def chat(**model_kwargs):
            model_kwargs["options"]["_stream_cancel_handle"].bind(transport)
            return response("Inspection finished without edits.")
        return original_run(**kwargs, chat_fn=chat)
    monkeypatch.setattr(agent_loop, "run_code_agent", run_child)
    owner = parent.agent_lock_path.read_bytes()
    child_id = ""
    try:
        result = _meta.tool_delegate_task(tmp_path, run_id=parent.run_id, role="review", task="Inspect project")
        child_id = result["subagent_run_id"]
        assert result["ok"] is False and result["subagent"]["status"] == "failed"
        assert result["error"] == "child_cleanup_incomplete"
        assert result["result_text"] == "Inspection finished without edits."
        assert child_id not in run_control._CANCEL_REGISTRY
        assert run_control._has_retained_cancel_handle(child_id)
        assert parent.run_id in _shell._RUN_CANCEL_CALLBACKS
        assert parent.agent_lock_path.read_bytes() == owner
        with pytest.raises(RuntimeError, match="natural close refused"):
            agent_loop.request_cancel(parent.run_id)
        assert parent.run_id in _shell._RUN_CANCEL_CALLBACKS
        transport.allow_close = True
        agent_loop.request_cancel(parent.run_id)
        assert transport.closed
        assert not run_control._has_retained_cancel_handle(child_id)
        assert parent.run_id not in _shell._RUN_CANCEL_CALLBACKS
        assert parent.agent_lock_path.read_bytes() == owner
    finally:
        transport.allow_close = True
        if child_id and run_control._has_retained_cancel_handle(child_id):
            agent_loop.request_cancel(child_id)
        _shell.cancel_run_callbacks(parent.run_id)
        parent.finish()
