from __future__ import annotations

import importlib.util
from copy import deepcopy
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "scripts" / "foundation_service.py"
SPEC = importlib.util.spec_from_file_location("foundation_service_contract_test", SOURCE)
foundation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(foundation)
CHECK_SPEC = importlib.util.spec_from_file_location("foundation_proof_contract_test", SOURCE.with_name("check_foundation.py"))
checker = importlib.util.module_from_spec(CHECK_SPEC)
CHECK_SPEC.loader.exec_module(checker)


def test_snapshot_reports_only_direction_without_hidden_inventory(tmp_path):
    service = foundation.Foundation({"store": str(tmp_path), "platform": str(tmp_path)})
    service.manager = SimpleNamespace(backend=None, ui=None,
        state=lambda: {"active": "old", "previous": "new", "installed_releases": ["hidden", "old", "new"]})
    service._update_snapshot()
    assert service.snapshot["saved_release_action"] == "update"
    assert "installed_releases" not in service.snapshot
    service.manager.state = lambda: {"active": "new", "previous": "old", "installed_releases": ["hidden", "old", "new"]}
    service._update_snapshot()
    assert service.snapshot["saved_release_action"] == "rollback"


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
    {"version": 1, "operation": "confirm", "request_id": "proof-request-0001", "confirmation_id": "a" * 32},
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
    {"version": 1, "operation": "confirm", "request_id": "proof-request-0001", "confirmation_id": "../outside"},
    {"version": 1, "operation": "confirm", "request_id": "proof-request-0001", "release_id": "proof-v1"},
])
def test_request_contract_rejects_missing_or_unbounded_arguments(payload):
    with pytest.raises(ValueError):
        foundation.validate_request(payload)


def test_confirmation_dispatches_only_the_explicit_proposal_id(config, manager_factory):
    service = foundation.Foundation(config)
    service._attach(FakeHost())
    confirmations = []

    def confirm(request_id):
        confirmations.append(request_id)
        return {"pending": "candidate", "confirmation": None}

    service.manager.confirm = confirm
    request = {"version": 1, "operation": "confirm", "request_id": "confirm-transport-001",
               "confirmation_id": "a" * 32}
    service.dispatch(request, FakeHost())
    service._operation(*service.queue.get_nowait())
    record = service.dispatch(request, FakeHost())
    assert record["status"] == "completed" and record["result"]["pending"] == "candidate"
    assert confirmations == ["a" * 32] and service.queue.empty()


def test_rejected_new_request_preserves_an_existing_approved_handoff(config, manager_factory):
    service = foundation.Foundation(config)
    service._attach(FakeHost())
    accepted = {"request_id": "a" * 32, "release_id": "approved", "sha256": "b" * 64}
    foundation._write_json(service.store / "state.json", {"pending": "approved", "last_confirmation": accepted})
    operation = foundation._begin_progress(service.store, "confirm", "approved", "waiting")

    def reject(release_id):
        raise ValueError("An explicitly confirmed installation is still pending")

    service.manager.request = reject
    request = {"version": 1, "operation": "request", "request_id": "rejected-request-001", "release_id": "next"}
    service.dispatch(request, FakeHost())
    service._operation(*service.queue.get_nowait())
    assert service.dispatch(request, FakeHost())["status"] == "failed"
    progress = service.dispatch({"version": 1, "operation": "status"}, FakeHost())["progress"]
    assert progress["operation_id"] == operation and progress["phase"] == "waiting"
    assert service.dispatch({"version": 1, "operation": "status"}, FakeHost())["last_confirmation"] == accepted


@pytest.mark.skipif(os.name != "nt", reason="Windows Foundation client")
def test_confirmed_rollback_is_exact_idempotent_and_uses_the_existing_owner(config, manager_factory):
    service = foundation.Foundation(config)
    service._attach(FakeHost())
    service.manager.state = lambda: {"active": "b", "previous": "a"}
    calls = []
    def request(release_id, **kwargs):
        calls.append((release_id, kwargs))
        return {"confirmation": {"request_id": "a" * 32}}
    service.manager.request = request
    service.manager.confirm = lambda nonce: {"pending": "a", "last_confirmation": {"request_id": nonce}}
    payload = {"version": 1, "operation": "rollback", "request_id": "rollback-transport-001",
               "expected_active": "b", "expected_previous": "a", "confirm": True}
    service.dispatch(payload, FakeHost())
    service._operation(*service.queue.get_nowait())
    receipt = service.dispatch(payload, FakeHost())
    assert receipt["status"] == "completed" and receipt["result"]["pending"] == "a"
    assert calls == [("a", {"operation": "rollback", "expected_active": "b", "expected_previous": "a"})]
    assert service.queue.empty()
    for invalid in ({**payload, "confirm": False}, {**payload, "expected_previous": "b"},
                    {key: value for key, value in payload.items() if key != "expected_active"}):
        with pytest.raises(ValueError):
            foundation.validate_request(invalid)


def test_confirmation_client_keeps_transport_nonce_separate_from_proposal(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("confirmation_client_test", SOURCE.with_name("foundation_client.py"))
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    calls = []

    def call(request, **kwargs):
        calls.append(request)
        return {"pending": "candidate"}

    monkeypatch.setattr(client, "call", call)
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "confirm", "a" * 32, "--request-id", "transport-request-0001"])
    assert client.main() == 0
    assert calls == [{"version": 1, "operation": "confirm", "request_id": "transport-request-0001",
                      "confirmation_id": "a" * 32}]


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


def test_installation_token_mode_is_explicit_and_invalid_mode_writes_nothing(config):
    service = foundation.Foundation(config)
    service._update_snapshot()
    assert service.snapshot["application_token_mode"] == "limited"
    service = foundation.Foundation({**config, "application_token_mode": "administrator"})
    service._update_snapshot()
    assert service.snapshot["application_token_mode"] == "administrator"
    invalid_store = Path(config["store"]) / "must-not-exist"
    with pytest.raises(ValueError, match="application token mode"):
        foundation.Foundation({**config, "store": str(invalid_store), "application_token_mode": "inherit"})
    assert not invalid_store.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows protected installation registry")
@pytest.mark.parametrize("mode, valid", [(None, True), ("limited", True), ("administrator", True), ("inherit", False)])
def test_client_mode_comes_only_from_protected_registration(monkeypatch, mode, valid):
    spec = importlib.util.spec_from_file_location("foundation_client_contract_test", SOURCE.with_name("foundation_client.py"))
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    monkeypatch.setattr(client, "installed", lambda service: Path("C:/Program Files") / service)
    monkeypatch.setattr(client.winreg, "OpenKey", lambda *args: nullcontext("protected-key"))

    def query(key, name):
        assert key == "protected-key" and name == "ApplicationTokenMode"
        if mode is None:
            raise FileNotFoundError
        return mode, client.winreg.REG_SZ

    monkeypatch.setattr(client.winreg, "QueryValueEx", query)
    if valid:
        assert client.installed_token_mode("EliraFoundation") == (mode or "limited")
    else:
        with pytest.raises(ValueError, match="token mode"):
            client.installed_token_mode("EliraFoundation")


@pytest.mark.skipif(os.name != "nt", reason="Windows protected installation registry")
def test_installation_paths_are_readable_without_service_calls_or_private_files(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("foundation_paths_contract_test", SOURCE.with_name("foundation_client.py"))
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    registry = {name: (str(tmp_path / name), client.winreg.REG_SZ)
                for name in ("InstallRoot", "Platform", "StateRoot", "Candidates", "Published", "Data", "Journals")}
    registry.update(Port=(18581, client.winreg.REG_DWORD), ApplicationTokenMode=("administrator", client.winreg.REG_SZ))
    monkeypatch.setattr(client.winreg, "OpenKey", lambda *args: nullcontext("protected-key"))
    monkeypatch.setattr(client.winreg, "QueryValueEx", lambda key, name: registry[name])
    monkeypatch.setattr(client, "pipe_request", lambda *args, **kwargs: pytest.fail("Discovery must not contact the service"))
    paths = client.installation_paths()
    assert paths["data"] == str(tmp_path / "Data")
    assert paths["store"] == str(tmp_path / "StateRoot")
    assert paths["port"] == 18581
    assert paths["application_token_mode"] == "administrator"
    registry["Data"] = ("relative-data", client.winreg.REG_SZ)
    with pytest.raises(ValueError, match="Data binding"):
        client.installation_paths()


def test_administrator_mode_requires_existing_elevated_interactive_identity():
    from foundation_windows import _validate_user

    limited = {"sid": "S-1-5-21-123-456-789-1001", "session_id": 2, "integrity_rid": 8192,
               "elevated": False, "elevation_type": 3, "ui_access": False,
               "administrator_enabled": False, "privileges": []}
    admin = {**limited, "elevated": True, "elevation_type": 2, "integrity_rid": 12288,
             "administrator_enabled": True, "privileges": [{"name": "SeDebugPrivilege", "attributes": 0}]}
    _validate_user(limited)
    with pytest.raises(PermissionError):
        _validate_user(admin)
    _validate_user(admin, allowed_sid=admin["sid"], token_mode="administrator")
    for invalid in (limited, {**admin, "session_id": 0}, {**admin, "sid": "S-1-5-18"},
                    {**admin, "integrity_rid": 16384}, {**admin, "ui_access": True},
                    {**admin, "sid": "S-1-5-21-999-999-999-1001"}):
        with pytest.raises(PermissionError):
            _validate_user(invalid, allowed_sid=admin["sid"], token_mode="administrator")
    with pytest.raises(ValueError, match="token mode"):
        _validate_user(admin, token_mode="auto-elevate")


@pytest.mark.skipif(os.name != "nt", reason="Windows process token contract")
def test_explicit_administrator_host_preserves_child_identity_and_log_relay(tmp_path):
    from foundation_windows import current_user_token, WindowsProcessHost

    try:
        token = current_user_token(token_mode="administrator")
    except PermissionError:
        pytest.skip("An already elevated interactive developer is required; tests do not elevate")
    # The installed host uses a base interpreter, not Windows' venv redirector
    # (whose forwarding child would intentionally fail the pipe PID binding).
    runner_python = str(Path(sys.base_prefix) / "python.exe")
    with token, WindowsProcessHost(token, runner_python=runner_python) as host, host.clone() as cloned:
        with cloned.popen([sys.executable, "-B", "-c", "pass"], cwd=tmp_path) as child:
            assert child.wait(10) == 0
            assert child.identity["elevated"] is True
            assert child.identity["administrator_enabled"] is True
            assert child.identity["session_id"] == token.info["session_id"]
        log_path = tmp_path / "administrator-relay.log"
        with log_path.open("wb") as log:
            result = cloned.run([sys.executable, "-B", "-c", "print('administrator relay complete')"],
                                cwd=tmp_path, log=log, timeout=20)
        assert result.returncode == 0
        assert "administrator relay complete" in log_path.read_text(encoding="utf-8")
        with log_path.open("ab") as log:
            with cloned.popen([sys.executable, "-B", "-c", "import time; time.sleep(600)"],
                              cwd=tmp_path, log=log) as child:
                child.terminate()
                # Killing this owned job kills its relay runner as well. It is
                # a deliberate shutdown, not a missing normal exit receipt.
                assert child.wait(10) == 1


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


def test_status_observes_atomic_progress_and_state_during_long_verification(config, manager_factory):
    service = foundation.Foundation(config)
    service._attach(FakeHost())
    entered, finish = threading.Event(), threading.Event()

    def verify(release_id):
        operation = foundation._begin_progress(service.store, "verify", release_id, "checking")
        foundation._advance_progress(service.store, operation, "checking",
                                     step={"index": 2, "total": 6, "label": "Сборка интерфейса"})
        foundation._write_json(service.store / "state.json", {
            "active": "old", "previous": "older", "pending": release_id,
            "transition": None, "error": "retained lifecycle failure",
            "confirmation": {"request_id": "c" * 32, "release_id": "candidate", "sha256": "a" * 64, "requested_at": 100}})
        entered.set()
        if not finish.wait(5):
            raise RuntimeError("fixture verification timed out")
        foundation._advance_progress(service.store, operation, "verified")
        return {"release_id": release_id, "verified": True}

    service.manager.verify = verify
    request = {"version": 1, "operation": "verify", "request_id": "progress-request-0001", "release_id": "candidate"}
    service.dispatch(request, FakeHost())
    thread = threading.Thread(target=service._operation, args=service.queue.get_nowait())
    thread.start()
    try:
        assert entered.wait(5)
        status = service.dispatch({"version": 1, "operation": "status"}, FakeHost())
        assert status["progress"]["phase"] == "checking"
        assert status["progress"]["step"]["index"] == 2
        assert status["active"] == "old" and status["pending"] == "candidate"
        assert status["confirmation"]["request_id"] == "c" * 32
        assert status["error"] == status["last_error"] == "retained lifecycle failure"
    finally:
        finish.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert service.dispatch({"version": 1, "operation": "status"}, FakeHost())["progress"]["phase"] == "verified"


def test_corrupt_progress_does_not_stop_foundation_operations_or_failure_handling(config, manager_factory, monkeypatch):
    service = foundation.Foundation(config)
    (service.store / "progress.json").write_text("{broken observer", encoding="utf-8")
    request = {"version": 1, "operation": "verify", "request_id": "corrupt-progress-0001", "release_id": "candidate"}
    service.dispatch(request, FakeHost())
    service._operation(*service.queue.get_nowait())
    assert service.dispatch(request, FakeHost())["status"] == "completed"
    assert service.dispatch({"version": 1, "operation": "status"}, FakeHost())["progress"]["phase"] == "unavailable"

    def fail_tick():
        raise RuntimeError("fixture child startup failed")

    monkeypatch.setattr(service, "_tick", fail_tick)
    service.advance_lifecycle()
    status = service.dispatch({"version": 1, "operation": "status"}, FakeHost())
    assert status["last_error"] == "fixture child startup failed"
    assert status["progress"]["phase"] == "unavailable"
    assert service.recovered is False


@pytest.mark.parametrize("operation,offer_fails,newer", [("rollback", False, True), ("rollback", True, True),
                                                       ("confirm", False, True), ("rollback", False, False)])
def test_successful_rollback_offers_preserved_version_to_older_ui_without_installing(config, manager_factory, operation, offer_fails, newer):
    service = foundation.Foundation(config)
    service._attach(FakeHost())
    service.desired, service.recovered = True, True
    manager = service.manager
    manager.backend = manager.ui = SimpleNamespace(poll=lambda: None)
    state = {"active": "saved-a", "previous": "new-b", "pending": None, "confirmation": None, "transition": None}
    state["installed_releases"] = ["saved-a", "new-b"] if newer else ["new-b", "saved-a"]
    manager.state = lambda: state
    manager.apply_pending = lambda: True
    offers = []
    def offer(release_id):
        offers.append(release_id)
        if offer_fails:
            raise ValueError("fixture unavailable seal")
        state["confirmation"] = {"release_id": release_id}
    manager.request = offer
    progress = foundation._begin_progress(service.store, operation, "saved-a", "switching")
    foundation._advance_progress(service.store, progress, "completed")
    service._tick()
    assert offers == (["new-b"] if operation == "rollback" and newer else [])
    assert state["active"] == "saved-a" and state["pending"] is None
    assert manager.backend is not None and manager.ui is not None
    assert service.desired  # A failed offer does not stop the admitted application.
