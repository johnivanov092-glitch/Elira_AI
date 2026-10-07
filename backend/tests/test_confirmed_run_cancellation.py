from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import mock

import psutil
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import code_agent_routes
from app.application.code_agent import run_control
from app.application.code_agent.tools import _shell
from app.infrastructure.llm.openai_compatible import LLMStreamCancelHandle


def test_cancel_real_process_failure_then_retry() -> None:
    run_id = f"confirmed-stop-{uuid.uuid4().hex}"
    owner = run_control._register_run(run_id)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    token = _shell.set_current_run_id(run_id)
    try:
        _shell.register_run_process(process)
    finally:
        _shell.reset_current_run_id(token)
    try:
        assert psutil.pid_exists(process.pid)
        with mock.patch.object(_shell, "_kill_proc_tree"):
            failed = code_agent_routes.cancel(code_agent_routes.CodeAgentCancelRequest(run_id=run_id))
        assert failed["state"] == "cancel_failed" and not failed["ok"]
        assert "still alive" in failed["error"]
        assert owner.is_set() and process.poll() is None
        assert run_control._CANCEL_REGISTRY[run_id] is owner
        stopped = code_agent_routes.cancel(code_agent_routes.CodeAgentCancelRequest(run_id=run_id))
        assert stopped["state"] == "stopped" and stopped["ok"]
        assert stopped["run_id"] == run_id
        process.wait(timeout=5)
        assert not psutil.pid_exists(process.pid)
        assert code_agent_routes.cancel(code_agent_routes.CodeAgentCancelRequest(run_id=run_id))["state"] == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        _shell.unregister_run_process(run_id, process)
        run_control._unregister_run(run_id)
        _shell.clear_run_stop_marker(run_id)


def test_cancel_before_registration_and_after_natural_completion() -> None:
    run_id = f"early-stop-{uuid.uuid4().hex}"
    assert not run_control.request_cancel(run_id)
    owner = run_control._register_run(run_id)
    assert owner.is_set()
    assert run_control._cancel_handle_for(owner).is_closed
    run_control._unregister_run(run_id)
    # A repeated Stop racing natural completion acknowledges already-clean
    # ownership and cannot poison the next explicit execution generation.
    assert not run_control.request_cancel(run_id)
    next_owner = run_control._register_run(run_id, resume=True)
    try:
        assert not next_owner.is_set()
    finally:
        run_control._unregister_run(run_id)
        _shell.clear_run_stop_marker(run_id)


def test_failed_transport_close_is_retained_after_unregister_for_retry() -> None:
    run_id = f"transport-stop-{uuid.uuid4().hex}"
    owner = run_control._register_run(run_id)
    transport = mock.Mock()
    transport.close.side_effect = RuntimeError("transport close refused")
    handle = run_control._cancel_handle_for(owner)
    handle.bind(transport)
    try:
        with pytest.raises(RuntimeError, match="transport close refused"):
            run_control.request_cancel(run_id)
        with pytest.raises(RuntimeError, match="transport close refused"):
            run_control._unregister_run(run_id)
        assert run_control._FAILED_CANCEL_HANDLES[run_id] is handle
        with pytest.raises(RuntimeError, match="cleanup is incomplete"):
            run_control._register_run(run_id, resume=True)
        transport.close.side_effect = None
        assert not run_control.request_cancel(run_id)
        assert run_id not in run_control._FAILED_CANCEL_HANDLES
        assert transport.close.call_count == 3
    finally:
        transport.close.side_effect = None
        run_control.request_cancel(run_id)
        _shell.clear_run_stop_marker(run_id)


def test_cancel_handle_retains_a_failed_late_response() -> None:
    handle = LLMStreamCancelHandle()
    handle.close()
    response = mock.Mock()
    response.close.side_effect = RuntimeError("late close refused")
    with pytest.raises(RuntimeError, match="late close refused"):
        handle.bind(response)
    with pytest.raises(RuntimeError, match="late close refused"):
        handle.close()
    response.close.side_effect = None
    handle.close()
    assert response.close.call_count == 3


def test_resume_stop_after_headers_before_lazy_registration() -> None:
    run_id = f"resumed-stop-{uuid.uuid4().hex}"
    run_control._register_run(run_id)
    run_control._unregister_run(run_id)
    run_control.prepare_run(run_id, resume=True)
    assert not run_control.request_cancel(run_id)
    owner = run_control._register_run(run_id, resume=True)
    try:
        assert owner.is_set()
    finally:
        run_control._unregister_run(run_id)
        _shell.clear_run_stop_marker(run_id)
