from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import code_agent_routes  # noqa: E402
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


def test_workflow_cancel_is_not_committed_when_live_cleanup_fails(
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
    assert current["status"] == "running"


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
