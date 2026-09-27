from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "scripts" / "foundation_service.py"
SPEC = importlib.util.spec_from_file_location("foundation_service_contract_test", SOURCE)
foundation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(foundation)
CHECK_SPEC = importlib.util.spec_from_file_location("foundation_proof_contract_test", SOURCE.with_name("check_foundation.py"))
checker = importlib.util.module_from_spec(CHECK_SPEC)
CHECK_SPEC.loader.exec_module(checker)


class FakeHost:
    def __init__(self, session_id=1, logon_sid="S-1-5-5-100-101"):
        self.token = SimpleNamespace(info={"sid": "S-1-5-21-123-456-789-1001",
                                          "session_id": session_id,
                                          "groups": [{"sid": logon_sid, "attributes": 0xC0000007}]})
        self.closed = False
        self.clones = []

    def clone(self):
        result = FakeHost(self.token.info["session_id"])
        result.token.info = deepcopy(self.token.info)
        self.clones.append(result)
        return result

    def close(self):
        self.closed = True


class FakeManager:
    def __init__(self):
        self.backend = None
        self.ui = None
        self.verifications = []
        self.stop_calls = []
        self.fail_stop = set()

    def verify(self, release_id):
        self.verifications.append(release_id)
        return {"release_id": release_id, "verified": True}

    def state(self):
        return {}

    def _stop(self, resource):
        self.stop_calls.append(resource)
        if resource in self.fail_stop:
            raise OSError(f"fixture {resource} stop failure")
        child = getattr(self, resource)
        if child is not None:
            child.close()
        setattr(self, resource, None)

    def _stop_ui(self):
        self._stop("ui")

    def _stop_backend(self):
        self._stop("backend")


@pytest.fixture
def config(tmp_path):
    store = tmp_path / "protected-state"
    store.mkdir()
    return {"store": str(store), "platform": str(tmp_path / "platform"),
            "candidates": str(tmp_path / "candidates"), "published": str(store / "published"),
            "data": str(tmp_path / "data"), "journals": str(tmp_path / "journals"),
            "python": str(tmp_path / "protected-python.exe"), "port": 18591,
            "service_name": "EliraFoundationProof"}


@pytest.fixture
def manager_factory(monkeypatch):
    created = []

    def factory(*args, **kwargs):
        manager = FakeManager()
        created.append(manager)
        return manager

    monkeypatch.setattr(foundation, "ReleaseManager", factory)
    return created


@pytest.mark.parametrize("payload", [
    {"version": 1, "operation": "status"},
    {"version": 1, "operation": "operation_status", "operation_id": "proof-request-0001"},
    {"version": 1, "operation": "verify", "request_id": "proof-request-0001", "release_id": "proof-v1"},
])
def test_request_contract_accepts_only_declared_fields(payload):
    assert foundation.validate_request(payload) == payload
    with pytest.raises(ValueError):
        foundation.validate_request({**payload, "command": "untrusted command"})


@pytest.mark.parametrize("payload", [
    {"version": 2, "operation": "status"},
    {"version": 1, "operation": "execute"},
    {"version": 1, "operation": "open"},
    {"version": 1, "operation": "verify", "request_id": "proof-request-0001", "release_id": "../outside"},
    {"version": 1, "operation": "operation_status", "operation_id": "../outside"},
])
def test_request_contract_rejects_missing_or_unbounded_arguments(payload):
    with pytest.raises(ValueError):
        foundation.validate_request(payload)


def test_request_replay_queues_and_verifies_once(config, manager_factory):
    service = foundation.Foundation(config)
    host = FakeHost()
    request = {"version": 1, "operation": "verify", "request_id": "proof-request-0001",
               "release_id": "proof-v1"}
    first = service.dispatch(request, host)
    assert first["status"] == "queued"
    assert service.dispatch(dict(request), host) == first
    assert service.queue.qsize() == 1
    assert len(host.clones) == 1
    with pytest.raises(ValueError, match="different operation"):
        service.dispatch({**request, "release_id": "proof-v2"}, host)

    queued_request, queued_host = service.queue.get_nowait()
    host.close()  # IPC scope ends; the operation owns a distinct live clone.
    service._operation(queued_request, queued_host)
    replay = service.dispatch(request, FakeHost())
    assert replay["status"] == "completed"
    assert replay["result"]["release_id"] == "proof-v1"
    assert service.queue.empty()
    assert manager_factory[0].verifications == ["proof-v1"]
    assert service.host is queued_host and not queued_host.closed


def test_restart_marks_queued_request_interrupted_without_rerunning(config):
    service = foundation.Foundation(config)
    request = {"version": 1, "operation": "open", "request_id": "proof-request-0002"}
    host = FakeHost()
    service.dispatch(request, host)
    restarted = foundation.Foundation(config)
    retry_host = FakeHost()
    replay = restarted.dispatch(request, retry_host)
    assert replay["status"] == "interrupted"
    assert restarted.queue.empty()
    assert retry_host.clones == []
    service.queue.get_nowait()[1].close()


def test_attach_failure_releases_host_and_next_request_can_retry(config, monkeypatch):
    service = foundation.Foundation(config)
    calls = []

    def factory(*args, **kwargs):
        calls.append(kwargs["host"])
        if len(calls) == 1:
            raise OSError("fixture manager construction failure")
        return FakeManager()

    monkeypatch.setattr(foundation, "ReleaseManager", factory)
    failed = FakeHost()
    with pytest.raises(OSError, match="fixture manager"):
        service._attach(failed)
    assert failed.closed
    assert service.host is None and service.manager is None
    succeeding = FakeHost()
    service._attach(succeeding)
    assert service.host is succeeding and not succeeding.closed
    assert service.manager is not None
    assert calls == [failed, succeeding]


def test_new_session_cannot_replace_live_children_but_can_attach_after_close(config, manager_factory):
    service = foundation.Foundation(config)
    original = FakeHost(1)
    service._attach(original)
    service.manager.backend = SimpleNamespace(poll=lambda: None)
    refused = FakeHost(2)
    with pytest.raises(RuntimeError, match="Another interactive session"):
        service._attach(refused)
    assert refused.closed and not original.closed
    assert service.host is original and len(manager_factory) == 1

    service.manager.backend = None
    service.recovered = True
    new_session = FakeHost(2)
    service._attach(new_session)
    assert original.closed and not new_session.closed
    assert service.host is new_session
    assert service.manager is manager_factory[1]
    assert not service.recovered


def test_reused_session_number_with_new_logon_closes_old_jobs_before_replacement(config, manager_factory):
    service = foundation.Foundation(config)
    original = FakeHost(2, "S-1-5-5-100-101")
    service._attach(original)
    old_manager = service.manager
    old_manager.backend = SimpleNamespace(poll=lambda: None)
    refused = FakeHost(2, "S-1-5-5-200-201")
    with pytest.raises(RuntimeError, match="Another interactive session"):
        service._attach(refused)
    assert refused.closed and not original.closed
    assert service.host is original and old_manager.stop_calls == []

    jobs_closed = []

    def close_job(name):
        assert not original.closed  # Retain the old token until owned jobs are closed.
        jobs_closed.append(name)

    old_manager.ui = SimpleNamespace(poll=lambda: 0, close=lambda: close_job("ui"))
    old_manager.backend = SimpleNamespace(poll=lambda: 1, close=lambda: close_job("backend"))
    service.recovered = True
    replacement = FakeHost(2, "S-1-5-5-200-201")
    service._attach(replacement)
    assert jobs_closed == ["ui", "backend"]
    assert old_manager.stop_calls == ["ui", "backend"]
    assert original.closed and not replacement.closed
    assert service.host is replacement and service.manager is manager_factory[1]
    assert not service.recovered


@pytest.mark.parametrize("failed_resource", ["ui", "backend"])
def test_stop_failure_retains_failed_handle_and_attempts_other_resource(config, manager_factory, failed_resource):
    service = foundation.Foundation(config)
    host = FakeHost()
    service._attach(host)
    manager = service.manager
    closed = []
    ui = SimpleNamespace(pid=100, poll=lambda: None, close=lambda: closed.append("ui"))
    backend = SimpleNamespace(pid=101, poll=lambda: None, close=lambda: closed.append("backend"))
    manager.ui, manager.backend = ui, backend
    manager.fail_stop.add(failed_resource)

    assert service._stop_application() is False
    assert manager.stop_calls == ["ui", "backend"]
    assert closed == ["backend" if failed_resource == "ui" else "ui"]
    assert getattr(manager, failed_resource) is (ui if failed_resource == "ui" else backend)
    assert not host.closed
    service._update_snapshot()  # Cleanup failure did not prevent the service status path.
    assert service.snapshot["foundation"] == "running"

    manager.fail_stop.clear()
    assert service._stop_application() is True
    assert manager.ui is None and manager.backend is None
    assert sorted(closed) == ["backend", "ui"]


def test_identity_proof_rejects_successful_exit_with_wrong_real_child_token():
    client = {"sid": "S-1-5-21-123-456-789-1001", "session_id": 2, "integrity_rid": 8192,
              "elevated": False, "elevation_type": 3, "ui_access": False,
              "groups": [{"sid": "S-1-5-32-544", "attributes": 16}],
              "privileges": [{"name": "SeChangeNotifyPrivilege", "attributes": 3}],
              "administrator_enabled": False}
    service_sid = "S-1-5-80-123-456-789-123-456"
    response = {"ok": True, "result": {
        "service": {"sid": "S-1-5-19", "session_id": 0,
                    "groups": [{"sid": service_sid, "attributes": 4}]},
        "service_pid": 1234, "client": deepcopy(client), "child": deepcopy(client),
        "child_pid": 4321, "exit_code": 0,
        "child_creation_identity": "win:13440223334567890", "child_image": "C:/fixture/python.exe"}}
    arguments = {"client": client, "service_sid": service_sid, "service_pid": 1234}
    assert checker.validate_identity(response, **arguments) == response["result"]
    for invalid in (
        {"sid": "S-1-5-19"}, {"session_id": 0}, {"integrity_rid": 12288},
        {"elevated": True}, {"groups": [{"sid": "S-1-5-32-544", "attributes": 4}]},
        {"privileges": [{"name": "SeDebugPrivilege", "attributes": 0}]},
    ):
        forged = deepcopy(response)
        forged["result"]["child"].update(invalid)
        with pytest.raises(ValueError):
            checker.validate_identity(forged, **arguments)
    mismatched = deepcopy(response)
    mismatched["result"]["service_pid"] = 5678
    with pytest.raises(ValueError, match="another service PID"):
        checker.validate_identity(mismatched, **arguments)
