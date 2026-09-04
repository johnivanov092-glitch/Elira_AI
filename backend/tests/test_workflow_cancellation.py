from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import code_agent_routes  # noqa: E402
from app.api.routes.workflow_routes import router as workflow_router  # noqa: E402
from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.run_journal import RunJournal  # noqa: E402
from app.application.code_agent.tools import _shell  # noqa: E402
from app.application.event_bus import runtime as event_bus  # noqa: E402
from app.application.workflows import db_path as workflow_db_path  # noqa: E402
from app.application.workflows import runtime, store  # noqa: E402


@pytest.fixture
def isolated_workflow_db(tmp_path: Path):
    original = workflow_db_path.get_workflow_db_path()
    original_event_bus_db = event_bus.DB_PATH
    selected = tmp_path / "workflow_engine.db"
    workflow_db_path.set_workflow_db_path(selected)
    event_bus.DB_PATH = tmp_path / "event_bus.db"
    store.init_db(db_path=selected)
    event_bus._init_db()
    try:
        yield selected
    finally:
        workflow_db_path.set_workflow_db_path(original)
        event_bus.DB_PATH = original_event_bus_db


def test_cancelled_background_tool_run_cannot_complete(
    isolated_workflow_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    worker_errors: list[BaseException] = []

    def blocking_tool(*args, **kwargs):
        started.set()
        if not release.wait(timeout=2.0):
            raise AssertionError("test did not release the blocking tool")
        return {"ok": True, "text": "late success"}

    monkeypatch.setattr(
        "app.application.tool_registry.service.run_tool",
        blocking_tool,
    )
    store.create_workflow_template(
        db_path=isolated_workflow_db,
        template={
            "id": "test.cancel-in-flight",
            "name": "Cancel in flight",
            "graph": {
                "entry_step": "command",
                "steps": [
                    {
                        "id": "command",
                        "type": "tool",
                        "tool_name": "search_memory",
                        "next": None,
                    }
                ],
            },
        },
    )

    created = store.create_workflow_run_record(
        db_path=isolated_workflow_db,
        workflow_id="test.cancel-in-flight",
        context={"project_root": str(tmp_path)},
        permission_mode="bypass",
    )
    run_id = str(created["run_id"])

    def execute_run() -> None:
        try:
            runtime.execute_workflow_run(
                run_id=run_id,
                db_path=isolated_workflow_db,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            worker_errors.append(exc)

    worker = threading.Thread(target=execute_run)
    worker.start()
    assert started.wait(timeout=1.0)

    cancelled = runtime.cancel_workflow_run(run_id, db_path=isolated_workflow_db)
    assert cancelled["status"] == "cancelled"
    release.set()
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert not worker_errors

    current = store.get_workflow_run(db_path=isolated_workflow_db, run_id=run_id)
    assert current is not None
    assert current["status"] == "cancelled"
    completed_events, _ = event_bus.list_events(
        event_type="workflow.run.completed",
        limit=100,
        offset=0,
    )
    assert not any(
        event.get("payload", {}).get("run_id") == run_id
        for event in completed_events
    )


def test_workflow_cancel_is_committed_but_error_surfaces_when_live_cleanup_fails(
    isolated_workflow_db: Path,
) -> None:
    store.create_workflow_template(
        db_path=isolated_workflow_db,
        template={
            "id": "test.cancel-cleanup-failure",
            "name": "Cancel cleanup failure",
            "graph": {
                "entry_step": "command",
                "steps": [
                    {
                        "id": "command",
                        "type": "tool",
                        "tool_name": "search_memory",
                        "next": None,
                    }
                ],
            },
        },
    )
    created = store.create_workflow_run_record(
        db_path=isolated_workflow_db,
        workflow_id="test.cancel-cleanup-failure",
    )
    run_id = str(created["run_id"])

    with mock.patch(
        "app.application.code_agent.agent_loop.request_cancel",
        side_effect=RuntimeError("transport close failed"),
    ):
        with pytest.raises(RuntimeError, match="transport close failed"):
            runtime.cancel_workflow_run(run_id, db_path=isolated_workflow_db)

    current = store.get_workflow_run(db_path=isolated_workflow_db, run_id=run_id)
    assert current is not None
    assert current["status"] == "cancelled"


def test_request_cancel_surfaces_provider_callback_failure() -> None:
    run_id = "provider-cleanup-failure"
    callback = mock.Mock(side_effect=RuntimeError("provider close failed"))
    context_token = _shell.set_current_run_id(run_id)
    try:
        callback_token = _shell.register_run_cancel_callback(callback)
    finally:
        _shell.reset_current_run_id(context_token)

    try:
        with pytest.raises(RuntimeError, match="provider close failed"):
            agent_loop.request_cancel(run_id)
        callback.assert_called_once_with()
    finally:
        _shell.unregister_run_cancel_callback(callback_token)
        _shell.clear_run_stop_marker(run_id)


def test_kill_run_processes_surfaces_process_that_remains_alive() -> None:
    run_id = "process-cleanup-failure"
    process = mock.Mock(pid=7312)
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("tool", 5)
    with _shell._LIVE_SHELL_LOCK:
        _shell._LIVE_SHELL_PROCS[run_id] = {process}

    try:
        with mock.patch.object(_shell, "_kill_proc_tree"):
            with pytest.raises(RuntimeError, match="still alive"):
                _shell.kill_run_processes(run_id)
    finally:
        with _shell._LIVE_SHELL_LOCK:
            _shell._LIVE_SHELL_PROCS.pop(run_id, None)
        _shell.clear_run_stop_marker(run_id)


def test_closing_unfinished_stream_requests_runtime_cancellation(
    isolated_workflow_db: Path,
) -> None:
    run_id = "disconnect-run"
    events = iter([
        {"type": "run_started", "run_id": run_id},
        {"type": "heartbeat", "run_id": run_id},
    ])

    with (
        mock.patch.object(code_agent_routes, "request_session_cancel") as cancel_session,
        mock.patch.object(code_agent_routes, "request_cancel") as cancel_runtime,
    ):
        stream = code_agent_routes._stream_with_workflow_requests(
            events,
            code_agent_run_id=run_id,
            permission_mode="ask",
        )
        assert next(stream)["type"] == "run_started"
        stream.close()

    cancel_session.assert_called_once_with(run_id)
    cancel_runtime.assert_called_once_with(run_id)
    projected = store.get_workflow_run(
        db_path=isolated_workflow_db,
        run_id=code_agent_routes._composer_workflow_run_id(run_id),
    )
    assert projected is not None
    assert projected["status"] == "cancelled"


def test_completed_stream_does_not_request_runtime_cancellation(
    isolated_workflow_db: Path,
) -> None:
    run_id = "completed-run"
    events = iter([
        {"type": "run_started", "run_id": run_id},
        {
            "type": "done",
            "run_id": run_id,
            "ok": True,
            "stop_reason": "completed",
            "completion_status": "confirmed",
        },
    ])

    with (
        mock.patch.object(code_agent_routes, "request_session_cancel") as cancel_session,
        mock.patch.object(code_agent_routes, "request_cancel") as cancel_runtime,
    ):
        streamed = list(code_agent_routes._stream_with_workflow_requests(
            events,
            code_agent_run_id=run_id,
            permission_mode="ask",
        ))

    assert [event["type"] for event in streamed] == ["run_started", "done"]
    cancel_session.assert_not_called()
    cancel_runtime.assert_not_called()
    projected = store.get_workflow_run(
        db_path=isolated_workflow_db,
        run_id=code_agent_routes._composer_workflow_run_id(run_id),
    )
    assert projected is not None
    assert projected["status"] == "completed"


def test_stream_cleanup_failure_still_commits_cancelled_projection(
    isolated_workflow_db: Path,
) -> None:
    run_id = "disconnect-cleanup-failure"
    events = iter([
        {"type": "run_started", "run_id": run_id},
        {"type": "heartbeat", "run_id": run_id},
    ])

    with (
        mock.patch.object(code_agent_routes, "request_session_cancel"),
        mock.patch.object(
            code_agent_routes,
            "request_cancel",
            side_effect=RuntimeError("provider close failed"),
        ),
    ):
        stream = code_agent_routes._stream_with_workflow_requests(
            events,
            code_agent_run_id=run_id,
            permission_mode="ask",
        )
        assert next(stream)["type"] == "run_started"
        with pytest.raises(RuntimeError, match="provider close failed"):
            stream.close()

    projected = store.get_workflow_run(
        db_path=isolated_workflow_db,
        run_id=code_agent_routes._composer_workflow_run_id(run_id),
    )
    assert projected is not None
    assert projected["status"] == "cancelled"


def test_resume_creates_a_new_workflow_attempt_and_keeps_cancelled_attempt_terminal(
    isolated_workflow_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "resume-attempt-run"
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    app = FastAPI()
    app.include_router(code_agent_routes.router)
    app.include_router(workflow_router)

    initial_events = iter([
        {"type": "run_started", "run_id": run_id},
        {
            "type": "done",
            "run_id": run_id,
            "ok": False,
            "stop_reason": "cancelled",
            "completion_status": "none",
            "resumable": True,
        },
    ])
    with (
        TestClient(app) as client,
        mock.patch.object(
            code_agent_routes,
            "stream_delivery_session",
            return_value=initial_events,
        ),
    ):
        initial = client.post(
            "/api/code-agent/stream",
            json={
                "run_id": run_id,
                "message": "start",
                "project_root": str(tmp_path),
                "permission_mode": "bypass",
            },
        )
        assert initial.status_code == 200

        journal = RunJournal(run_id)
        journal.start(
            {
                "user_message": "start",
                "project_root": str(tmp_path),
                "permission_mode": "bypass",
            },
            {},
        )
        journal.finish(interrupted=True)

        resumed_events = iter([
            {"type": "run_resumed", "run_id": run_id, "from_step": 0},
            {
                "type": "done",
                "run_id": run_id,
                "ok": True,
                "stop_reason": "answer",
                "completion_status": "confirmed",
            },
        ])
        with mock.patch.object(
            code_agent_routes,
            "stream_resume_session",
            return_value=resumed_events,
        ):
            resumed = client.post(f"/api/code-agent/runs/{run_id}/resume")
        assert resumed.status_code == 200

        response = client.get(
            "/api/agent-os/workflow-runs",
            params={"workflow_id": "builtin-composer-runtime"},
        )

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert len(runs) == 2
    cancelled = next(run for run in runs if run["status"] == "cancelled")
    completed = next(run for run in runs if run["status"] == "completed")
    assert completed["run_id"] != cancelled["run_id"]
    assert completed["context"]["code_agent_run_id"] == run_id
    assert completed["context"]["workflow_root_run_id"] == cancelled["run_id"]
    assert completed["context"]["attempt_number"] == 2


def test_duplicate_stream_refusal_does_not_fail_the_active_workflow_attempt(
    isolated_workflow_db: Path,
    tmp_path: Path,
) -> None:
    run_id = "duplicate-stream-run"
    owner_started = threading.Event()
    release_owner = threading.Event()
    request_lock = threading.Lock()
    request_count = 0
    owner_responses: list[int] = []

    def owned_events():
        yield {"type": "run_started", "run_id": run_id}
        owner_started.set()
        if not release_owner.wait(timeout=2.0):
            raise AssertionError("test did not release the owning stream")
        yield {
            "type": "done",
            "run_id": run_id,
            "ok": True,
            "stop_reason": "answer",
            "completion_status": "confirmed",
        }

    def stream_events(**kwargs):
        nonlocal request_count
        with request_lock:
            request_count += 1
            current_request = request_count
        if current_request == 1:
            return owned_events()
        return iter([
            {
                "type": "done",
                "run_id": run_id,
                "ok": False,
                "stop_reason": "error",
                "error": "delivery_session_already_active",
                "resumable": False,
            },
        ])

    app = FastAPI()
    app.include_router(code_agent_routes.router)
    app.include_router(workflow_router)
    request = {
        "run_id": run_id,
        "message": "duplicate",
        "project_root": str(tmp_path),
        "permission_mode": "bypass",
    }

    with mock.patch.object(
        code_agent_routes,
        "stream_delivery_session",
        side_effect=stream_events,
    ), TestClient(app) as owner_client, TestClient(app) as duplicate_client:
        owner = threading.Thread(
            target=lambda: owner_responses.append(
                owner_client.post("/api/code-agent/stream", json=request).status_code
            )
        )
        owner.start()
        assert owner_started.wait(timeout=1.0)
        refused = duplicate_client.post("/api/code-agent/stream", json=request)
        running = duplicate_client.get(
            "/api/agent-os/workflow-runs",
            params={"workflow_id": "builtin-composer-runtime"},
        ).json()["runs"]
        release_owner.set()
        owner.join(timeout=2.0)
        assert not owner.is_alive()
        finished = duplicate_client.get(
            "/api/agent-os/workflow-runs",
            params={"workflow_id": "builtin-composer-runtime"},
        ).json()["runs"]

    assert refused.status_code == 200
    assert "delivery_session_already_active" in refused.text
    assert len(running) == 1
    assert running[0]["status"] == "running"
    assert owner_responses == [200]
    assert len(finished) == 1
    assert finished[0]["status"] == "completed"
