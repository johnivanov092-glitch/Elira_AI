from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("unattended_test", REPO / "scripts/run_autonomy_unattended.py")
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)


def command(args, *, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contained(path, root):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("outside owned directory")
    return path


class Inventory:
    def __init__(self, root, **kwargs):
        self.root = root

    def _source_files(self, root):
        return [(path.relative_to(root).as_posix(), path) for path in root.rglob("*")
                if path.is_file() and ".git" not in path.relative_to(root).parts]


def fixture_repo(tmp_path):
    repo = tmp_path / "source-repo"
    repo.mkdir()
    (repo / "app.py").write_text("old code\n", encoding="utf-8")
    (repo / "deleted.py").write_text("obsolete\n", encoding="utf-8")
    (repo / ".env").write_text("DUMMY_SECRET=never-copy\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    command(["git", "init", "--quiet"], cwd=repo)
    command(["git", "add", "."], cwd=repo)
    command(["git", "-c", "user.name=Acceptance Test", "-c", "user.email=test@localhost",
             "-c", "commit.gpgsign=false", "commit", "-qm", "baseline"], cwd=repo)
    return repo


def test_snapshot_uses_current_changes_without_secrets_or_index_changes(tmp_path):
    repo = fixture_repo(tmp_path)
    (repo / "app.py").write_text("current uncommitted code\n", encoding="utf-8")
    (repo / "new.py").write_text("new helper\n", encoding="utf-8")
    (repo / "deleted.py").unlink()
    (repo / ".scratch").mkdir()
    (repo / ".scratch/private.txt").write_text("private", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data/state.json").write_text("private runtime", encoding="utf-8")
    before = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=repo)
    harness = SimpleNamespace(command=command, digest=digest, contained=contained)
    module = SimpleNamespace(ReleaseManager=Inventory, _contained=contained)
    destination = tmp_path / "frozen"
    result = trial.freeze_working_source(repo, destination, harness, module)
    assert (destination / "app.py").read_text() == "current uncommitted code\n"
    assert (destination / "new.py").is_file()
    assert not (destination / "deleted.py").exists()
    assert not (destination / ".env").exists()
    assert not (destination / "data/state.json").exists()
    assert not (destination / ".scratch/private.txt").exists()
    assert result["files"]["app.py"] == digest(destination / "app.py")
    assert subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=repo) == before
    assert subprocess.check_output(["git", "remote"], cwd=destination) == b""


def test_snapshot_rejects_source_changed_during_copy(tmp_path):
    repo = fixture_repo(tmp_path)
    calls = 0
    def changing_digest(path):
        nonlocal calls
        if path == repo / "app.py":
            calls += 1
            if calls == 2:
                path.write_text("concurrent change", encoding="utf-8")
        return digest(path)
    with pytest.raises(RuntimeError, match="Source changed during snapshot"):
        trial.freeze_working_source(repo, tmp_path / "frozen",
            SimpleNamespace(command=command, digest=changing_digest, contained=contained),
            SimpleNamespace(ReleaseManager=Inventory, _contained=contained))


def test_explicit_missing_browser_does_not_silently_select_another(tmp_path):
    with pytest.raises(ValueError, match="specified browser"):
        trial.browser_path(tmp_path / "missing.exe")


@pytest.fixture
def installation(tmp_path, monkeypatch):
    harness = trial.load_harness()
    cfg = {key: str(tmp_path / key) for key in ("platform", "data", "runs", "temp")}
    cfg.update(baseline="fixture-baseline", backend_port=18581, ui_port=18582)
    harness.write_json(tmp_path / "config.json", cfg)
    monkeypatch.setenv("ELIRA_DATA_DIR", cfg["data"])
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", cfg["runs"])
    release = harness.release_module(REPO)
    manager = release.ReleaseManager(Path(cfg["platform"]), port=cfg["backend_port"])
    monkeypatch.setattr(manager, "checked", lambda release_id: {"sha256": "a" * 64})
    manager._save({"active": "fixture-active", "previous": "fixture-previous", "pending": None})
    return harness, manager


@pytest.mark.parametrize("purpose", ["baseline", "rollback", "startup-fault"])
def test_controller_can_explicitly_confirm_only_its_named_fixture(installation, tmp_path, purpose):
    harness, manager = installation
    target = "fixture-baseline" if purpose == "baseline" else "fixture-previous"
    state = harness.confirm_fixture_release(tmp_path, manager, target, purpose=purpose)
    assert state["pending"] == target and state["confirmation"] is None
    evidence = harness.read_json(tmp_path / f"fixture-{purpose}-confirmation.json")
    assert evidence["decision"] == "explicit_test_fixture"
    assert state["last_confirmation"]["request_id"] == evidence["confirmation"]["request_id"]


def test_model_proposal_stops_acceptance_without_confirming_or_overwriting_it(installation, tmp_path):
    harness, manager = installation
    state = manager.request("model-feature")
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    # A still-healthy old active release is not acceptance of the new proposal.
    with pytest.raises(trial.AwaitingUserConfirmation) as caught:
        trial.require_confirmed_release(state)
    assert caught.value.proposal == state["confirmation"]
    with pytest.raises(ValueError, match="model-generated"):
        harness.confirm_fixture_release(tmp_path, manager, "model-feature", purpose="baseline")
    with pytest.raises(ValueError, match="existing installation proposal"):
        harness.confirm_fixture_release(tmp_path, manager, "fixture-previous", purpose="rollback")
    assert manager.state()["pending"] is None
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_fixture_confirmation_rejects_another_installation(installation, tmp_path):
    harness, manager = installation
    manager.port = 8000
    with pytest.raises(ValueError, match="owned isolated"):
        harness.confirm_fixture_release(tmp_path, manager, "fixture-baseline", purpose="baseline")
    assert manager.state().get("confirmation") is None
