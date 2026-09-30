from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routes import release_routes
from app.application.runtime import release_status as status
from app.core import release_runtime


def identifier(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value):
        raise ValueError("Invalid release identifier")


def progress(phase="idle", release=None, **kwargs):
    return {"version": 1, "phase": phase, "release_id": release,
            "operation_id": "operation-1", "updated_at": 1790700000,
            "step": None, "error": None, **kwargs}


def approval(release):
    return {"request_id": "a" * 32, "release_id": release, "sha256": "b" * 64}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    module = SimpleNamespace(
        saved_release_action=status._release_module().saved_release_action,
        ReleaseManager=SimpleNamespace(_validate_id=identifier),
        _foundation_client_command=lambda **kwargs: None,
        read_release_progress=lambda store: progress(),
    )
    monkeypatch.setattr(status, "_release_module", lambda: module)
    monkeypatch.setenv("ELIRA_PLATFORM_ROOT", str(tmp_path))
    monkeypatch.setattr(release_runtime, "health_fields", lambda: {
        "release_id": "a", "admitted": True, "draining": False,
    })
    status._cache.clear()
    return module


def test_verified_and_pending_are_not_installed_and_current_data_is_not_written(owner, tmp_path):
    store = tmp_path / ".runtime/releases"
    store.mkdir(parents=True)
    path = store / "state.json"
    path.write_text(json.dumps({"active": "a", "pending": "b", "last_confirmation": approval("b")}), encoding="utf-8")
    owner.read_release_progress = lambda _: progress("verified", "b", step={"index": 4, "total": 4, "label": "Сборка приложения"})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = status.get_release_status(port=18581)
    assert result["phase"] == "waiting"
    assert result["active_release_id"] == "a"
    assert result["target_release_id"] == "b"
    assert result["step"]["index"] == 4
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_completed_requires_this_backend_identity_and_admission(owner, monkeypatch):
    state = {"active": "b", "pending": None}
    receipt = progress("completed", "b")
    assert status._public_view("legacy", state, receipt)["phase"] == "switching"
    monkeypatch.setattr(release_runtime, "health_fields", lambda: {
        "release_id": "b", "admitted": False, "draining": True,
    })
    assert status._public_view("legacy", state, receipt)["phase"] == "switching"
    monkeypatch.setattr(release_runtime, "health_fields", lambda: {
        "release_id": "b", "admitted": True, "draining": False,
    })
    assert status._public_view("legacy", state, receipt)["phase"] == "completed"


@pytest.mark.parametrize("phase", ["prepared", "verified", "waiting"])
def test_new_candidate_does_not_inherit_old_failure(owner, phase):
    state = {"active": "a", "error": "old failed b", "pending": "c" if phase == "waiting" else None}
    state["last_confirmation"] = approval("c")
    result = status._public_view("legacy", state, progress(phase, "c"))
    assert result["phase"] == phase
    assert result["error"] is None


def test_failed_and_interrupted_evidence_is_not_hidden_by_pending(owner):
    for phase in ("failed", "interrupted"):
        result = status._public_view("legacy", {"active": "a", "pending": "b"}, progress(phase, "b", error="Проверка остановлена"))
        assert result["phase"] == phase
        assert result["error"] == "Проверка остановлена"


@pytest.mark.parametrize("phase", ["idle", "verified", "waiting"])
@pytest.mark.parametrize("last_confirmation", [None, {}, {"release_id": "b"}, approval("c")])
def test_legacy_pending_without_matching_approval_never_claims_user_consent(owner, phase, last_confirmation):
    result = status._public_view("legacy", {"active": "a", "pending": "b", "last_confirmation": last_confirmation}, progress(phase, "b"))
    assert result["phase"] == "interrupted"
    assert "подтверждения" in result["error"]


def test_unavailable_is_not_false_idle_and_failed_read_is_cached(owner, monkeypatch):
    calls = []
    def broken(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("Broken registered Foundation")
    owner._foundation_client_command = broken
    result = status.get_release_status(port=8000)
    assert result["phase"] == "unavailable"
    assert status.get_release_status(port=8000) == result
    assert len(calls) == 1


def test_foundation_uses_protected_read_client_and_never_reads_legacy_state(owner, tmp_path, monkeypatch):
    store = tmp_path / ".runtime/releases"
    store.mkdir(parents=True)
    (store / "state.json").write_text("invalid stale legacy", encoding="utf-8")
    protected = ["protected-python", "-I", "-S", "-B", "protected-client.py", "--service", "EliraFoundation"]
    owner._foundation_client_command = lambda **kwargs: protected
    def forbidden(_):
        pytest.fail("Foundation observation read local legacy progress")
    owner.read_release_progress = forbidden
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=json.dumps({"active": "a", "progress": progress("checking", "b")}))
    monkeypatch.setattr(status.subprocess, "run", run)
    result = status.get_release_status(port=8000)
    assert result["mode"] == "foundation" and result["phase"] == "checking"
    assert calls[0][0] == protected + ["status"]
    assert calls[0][1]["timeout"] == 4


def test_old_foundation_without_progress_is_unavailable(owner, monkeypatch):
    owner._foundation_client_command = lambda **kwargs: ["protected-client"]
    monkeypatch.setattr(status.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout='{"active":"a"}'))
    assert status.get_release_status(port=8000)["phase"] == "unavailable"


def test_read_status_survives_drain_and_uses_listening_port_not_host_header(owner, monkeypatch):
    app = FastAPI()
    app.include_router(release_routes.router)
    app.add_middleware(release_runtime.ReleaseDrainMiddleware)
    monkeypatch.setattr(release_runtime, "_draining", True)
    monkeypatch.setattr(release_runtime, "_active_requests", 0)
    ports = []
    def snapshot(*, port):
        ports.append(port)
        assert release_runtime._active_requests == 0
        return {"phase": "switching"}
    monkeypatch.setattr(release_routes, "get_release_status", snapshot)
    with TestClient(app, base_url="http://testserver:18581") as client:
        reply = client.get("/api/release/status", headers={"Host": "other-installation:8000"})
        assert reply.status_code == 200 and reply.json()["phase"] == "switching"
        assert client.get("/unrelated").status_code == 503
    assert ports == [18581]
    assert release_runtime._active_requests == 0


def test_manual_rollback_availability_and_owner_selection(owner, monkeypatch):
    current = status._public_view("legacy", {"active": "a", "previous": "b"}, progress("completed", "a"))
    assert current["rollback_available"] is True and current["previous_release_id"] == "b"
    for state in ({"active": "a", "previous": "a"}, {"active": "other", "previous": "b"},
                  {"active": "a", "previous": "b", "pending": "c"},
                  {"active": "a", "previous": "b", "transition": {"phase": "switching", "to": "b"}}):
        assert status._public_view("legacy", state, progress("completed", "a"))["rollback_available"] is False
    monkeypatch.setattr(status, "get_release_status", lambda **_: current)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"pending":"b"}')
    monkeypatch.setattr(status.subprocess, "run", run)
    status.rollback_release(active_release_id="a", previous_release_id="b", port=18581)
    assert calls[0][-6:] == ["rollback", "--expected-active", "a", "--expected-previous", "b", "--confirm"]
    with pytest.raises(status.ReleaseConfirmationError) as conflict:
        status.rollback_release(active_release_id="other", previous_release_id="b", port=18581)
    assert conflict.value.status_code == 409 and len(calls) == 1


def test_saved_direction_uses_installation_order_and_hides_the_reserve(owner):
    state = {"active": "a", "previous": "z", "installed_releases": ["hidden", "a", "z"]}
    result = status._public_view("legacy", state, progress("completed", "a"))
    assert result["saved_release_action"] == "update"
    assert "hidden" not in json.dumps(result)
    assert "installed_releases" not in result
    state["installed_releases"] = ["hidden", "z", "a"]
    assert status._public_view("legacy", state, progress("completed", "a"))["saved_release_action"] == "rollback"
    state["saved_release_action"] = "update"
    assert status._public_view("foundation", state, progress("completed", "a"))["saved_release_action"] == "update"


def test_rollback_api_binds_only_asgi_port_and_validated_pair(monkeypatch):
    app = FastAPI()
    app.include_router(release_routes.router)
    calls = []
    monkeypatch.setattr(release_routes, "rollback_release", lambda **kwargs: calls.append(kwargs))
    with TestClient(app, base_url="http://testserver:18581") as client:
        payload = {"active_release_id": "a", "previous_release_id": "b"}
        assert client.post("/api/release/rollback", json={**payload, "platform": "other"}).status_code == 422
        assert client.post("/api/release/rollback", json=payload, headers={"Host": "production:8000"}).json() == {"ok": True}
    assert calls == [{"active_release_id": "a", "previous_release_id": "b", "port": 18581}]


def test_confirmation_matches_exact_proposal_and_survives_read(owner, tmp_path):
    proposal = {"request_id": "a" * 32, "release_id": "b", "sha256": "b" * 64, "requested_at": 1790700000}
    state = {"active": "a", "confirmation": proposal, "pending": None}
    evidence = progress("awaiting_confirmation", "b", operation_id=proposal["request_id"])
    result = status._public_view("legacy", state, evidence)
    assert result["phase"] == "awaiting_confirmation"
    assert result["confirmation"] == proposal
    assert state["pending"] is None
    assert status._public_view("legacy", {**state, "confirmation": None}, evidence)["phase"] == "interrupted"
    assert status._public_view("legacy", state, {**evidence, "operation_id": "c" * 32})["phase"] == "interrupted"


@pytest.mark.parametrize("foundation", [False, True])
def test_confirmation_uses_bound_owner_and_invalidates_status_cache(owner, tmp_path, monkeypatch, foundation):
    calls = []
    prefix = ["protected-python", "-I", "-S", "-B", "protected-client.py"]
    if foundation:
        owner._foundation_client_command = lambda **kwargs: prefix
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=json.dumps({"status": "completed"} if foundation else {"pending": "b"}), returncode=0)
    monkeypatch.setattr(status.subprocess, "run", run)
    status._cache[(str(tmp_path), 18581)] = (0, {})
    status.confirm_release(request_id="a" * 32, port=18581)
    command, options = calls[0]
    assert command[-1] == "120" if foundation else command[-1] == "a" * 32
    if foundation:
        assert command == prefix + ["confirm", "a" * 32, "--wait", "--timeout", "120"]
    else:
        assert "--platform" in command and str(tmp_path) in command
        assert command[command.index("--port") + 1] == "18581"
        assert Path(command[3]).name == "elira_release.py"
    assert options["timeout"] == 130 and options["cwd"] == tmp_path
    assert not status._cache


@pytest.mark.parametrize(("code", "expected"), [(1, 409), (2, 504)])
def test_confirmation_owner_failure_does_not_report_success(owner, monkeypatch, code, expected):
    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="{}", returncode=code))
    with pytest.raises(status.ReleaseConfirmationError) as caught:
        status.confirm_release(request_id="a" * 32, port=18581)
    assert caught.value.status_code == expected


def test_confirmation_timeout_is_uncertain_and_never_retried(owner, monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(status.subprocess, "run", run)
    with pytest.raises(status.ReleaseConfirmationError, match="могло быть принято") as caught:
        status.confirm_release(request_id="a" * 32, port=18581)
    assert caught.value.status_code == 504 and len(calls) == 1


def test_confirmation_route_validates_input_counts_request_and_rejects_during_drain(owner, monkeypatch):
    app = FastAPI()
    app.include_router(release_routes.router)
    app.add_middleware(release_runtime.ReleaseDrainMiddleware)
    monkeypatch.setattr(release_runtime, "_draining", False)
    monkeypatch.setattr(release_runtime, "_active_requests", 0)
    calls = []
    def confirm(**kwargs):
        calls.append(kwargs)
        assert release_runtime._active_requests == 1
    monkeypatch.setattr(release_routes, "confirm_release", confirm)
    with TestClient(app, base_url="http://testserver:18581") as client:
        for payload in ({"request_id": "../unsafe"}, {"request_id": "a" * 32, "platform": "other"}, {}):
            assert client.post("/api/release/confirm", json=payload).status_code == 422
        response = client.post("/api/release/confirm", json={"request_id": "a" * 32}, headers={"Host": "production:8000"})
        assert response.json() == {"ok": True} and response.status_code == 200
        monkeypatch.setattr(release_runtime, "_draining", True)
        assert client.post("/api/release/confirm", json={"request_id": "a" * 32}).status_code == 503
    assert calls == [{"request_id": "a" * 32, "port": 18581}]
    assert release_runtime._active_requests == 0
