from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(tmp_path, monkeypatch):
    for name in ("ELIRA_DATA_DIR", "ELIRA_AGENT_RUNS_DIR"):
        monkeypatch.delenv(name, raising=False)
    module = load(ROOT / "backend/tests/smokes/autonomy_ui.py", "snapshot_harness")
    release = load(ROOT / "scripts/elira_release.py", "snapshot_release")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(module, "REPO", repo)
    monkeypatch.setattr(module, "release_module", lambda _repo: release)
    monkeypatch.setattr(release.ReleaseManager, "__init__",
                        lambda *args, **kwargs: pytest.fail("Snapshot constructed a production manager"))
    return module, repo


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")


def tree(root: Path) -> dict:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("foundation", [False, True])
def test_snapshot_observes_selected_layout_and_never_initializes_production(harness, tmp_path, monkeypatch, foundation):
    module, repo = harness
    platform = repo
    store = tmp_path / "protected-state" if foundation else repo / ".runtime/releases"
    published = store / ("published" if foundation else "candidates")
    active = published / "live"
    data = tmp_path / "configured-data"
    journals = tmp_path / "configured-runs"
    data.mkdir()
    journals.mkdir()
    with sqlite3.connect(data / "chats.sqlite3") as db:
        db.execute("create table chat (message text)")
        db.execute("insert into chat values ('PRIVATE SENTINEL')")
    state = {"active": "live", "previous": None, "pending": None, "transition": None}
    receipt = {"release_id": "live", "status": "verified", "root": str(active)}
    write(store / "state.json", state)
    write(active / "app.py", "print('active source')\n")
    release = module.release_module(repo)
    for directory in release._DEPENDENCY_DIRECTORIES:
        write(active / directory / "dependency", "sealed dependency\n")
    executable = "src-tauri/target/release/elira-desktop.exe"
    write(active / executable, "sealed executable\n")
    write(active / "frontend/dist/index.html", "sealed frontend\n")
    receipt.update(executable=executable,
                   sha256=object.__new__(release.ReleaseManager).fingerprint(active, executable=executable))
    write(store / "records/live.json", receipt)
    write(platform / "scripts/elira_release.py", "supervisor source\n")
    write(platform / "backend/app/core/release_runtime.py", "admission source\n")
    if foundation:
        host = tmp_path / "installation/host"
        write(host / "foundation_service.py", "protected host\n")
        write(host.parent / "python/python.exe", "protected interpreter\n")
        bindings = {"platform": str(platform), "store": str(store), "published": str(published),
                    "candidates": str(platform / ".runtime/releases/candidates"), "data": str(data),
                    "journals": str(journals), "host": str(host), "install_root": str(host.parent),
                    "python": str(host.parent / "python/python.exe"), "port": 8000,
                    "application_token_mode": "administrator"}
        write(store / "installation.json", bindings)
        monkeypatch.setattr(module, "foundation_paths", lambda: bindings)
        # A stale legacy record must never become the selected production truth.
        write(repo / ".runtime/releases/state.json", {"active": "obsolete"})
    else:
        monkeypatch.setattr(module, "foundation_paths", lambda: None)
        write(repo / "backend/.env.local", f'ELIRA_DATA_DIR="{data.as_posix()}"\nELIRA_AGENT_RUNS_DIR="{journals.as_posix()}"\n')
    before = tree(tmp_path)
    snapshot = module.production_snapshot()
    assert tree(tmp_path) == before
    assert snapshot["layout"]["kind"] == ("foundation" if foundation else "legacy")
    assert snapshot["source_root"] == str(active)
    assert snapshot["layout"]["data"] == str(data)
    assert snapshot["active_receipt"] == receipt
    assert snapshot["active_runtime_sha256"] == receipt["sha256"]
    assert "app.py" in snapshot["active_source"]
    assert "chats.sqlite3" in snapshot["databases"]
    assert "PRIVATE SENTINEL" not in json.dumps(snapshot)
    if foundation:
        assert str(host / "foundation_service.py") in snapshot["platform_core"]
    # The controller's isolated environment must not redirect production checks.
    monkeypatch.setenv("ELIRA_DATA_DIR", str(tmp_path / "trial-data"))
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "trial-runs"))
    assert module.production_snapshot()["layout"] == snapshot["layout"]
    write(active / "backend/.venv/dependency", "damaged dependency\n")
    damaged = tree(tmp_path)
    with pytest.raises(ValueError, match="runtime differs"):
        module.production_snapshot()
    assert tree(tmp_path) == damaged
    receipt.pop("sha256")
    write(store / "records/live.json", receipt)
    with pytest.raises(ValueError, match="no valid runtime seal"):
        module.production_snapshot()


@pytest.mark.parametrize("error", [ValueError("corrupt registration"), PermissionError("unreadable registration")])
def test_invalid_installed_foundation_never_falls_back_or_writes(harness, tmp_path, monkeypatch, error):
    module, repo = harness
    write(repo / ".runtime/releases/state.json", {"active": "legacy"})
    def invalid():
        raise error
    monkeypatch.setattr(module, "foundation_paths", invalid)
    before = tree(tmp_path)
    with pytest.raises(type(error), match=str(error)):
        module.production_snapshot()
    assert tree(tmp_path) == before


def test_installed_configuration_mismatch_is_not_treated_as_legacy(harness, tmp_path, monkeypatch):
    module, repo = harness
    bindings = {key: str(tmp_path / key) for key in
                ("platform", "store", "published", "candidates", "data", "journals", "host", "python")}
    bindings.update(port=8000, application_token_mode="administrator")
    write(Path(bindings["store"]) / "installation.json", {**bindings, "data": str(tmp_path / "other")})
    monkeypatch.setattr(module, "foundation_paths", lambda: bindings)
    before = tree(tmp_path)
    with pytest.raises(ValueError, match="registered data"):
        module.production_snapshot()
    assert tree(tmp_path) == before


def test_setup_clones_repo_history_then_overlays_published_source_without_git(harness, tmp_path, monkeypatch):
    module, repo = harness
    published = tmp_path / "protected-published/live"
    write(published / "app.py", "published source\n")
    write(published / "scripts/elira_release.py", "published supervisor\n")
    write(published / "backend/.venv/local-dependency", "private dependency\n")
    assert not (published / ".git").exists()
    initial = {"state": {"active": "live"}, "source_root": str(published),
               "layout": {"kind": "foundation", "platform": str(repo)}}
    monkeypatch.setattr(module, "production_snapshot", lambda: initial)
    monkeypatch.setattr(module, "budget", lambda _root: {})
    calls = []

    def command(arguments, *, cwd, **kwargs):
        calls.append(arguments)
        if "clone" in arguments:
            platform = Path(arguments[-1])
            write(platform / "history-only.py", "must be removed by exact overlay\n")
            write(platform / ".git/HEAD", "private repository history\n")

    class Manager:
        def __init__(self, platform, **kwargs):
            self.platform = platform

        def _source_files(self, source):
            return [(path.relative_to(source).as_posix(), path) for path in source.rglob("*")
                    if path.is_file() and not {".git", ".runtime", ".venv"}.intersection(path.relative_to(source).parts)]

        def path(self, release):
            return self.platform / ".runtime/releases/candidates" / release

        def prepare(self, release):
            self.path(release).mkdir(parents=True)

        def _python(self, root):
            return root / "backend/.venv/python"

    fake = SimpleNamespace(ReleaseManager=Manager, _DEPENDENCY_DIRECTORIES=("backend/.venv",),
                           _OPTIONAL_NATIVE_DIRECTORIES=())
    monkeypatch.setattr(module, "release_module", lambda _platform: fake)
    monkeypatch.setattr(module, "command", command)
    # This source-overlay unit test neither owns nor probes the live trial ports.
    monkeypatch.setattr(module, "socket", SimpleNamespace(
        socket=lambda: nullcontext(SimpleNamespace(bind=lambda _address: None))))
    # Setup's process environment is an isolated child concern, never this test runner.
    monkeypatch.setattr(module, "environment", lambda root: {})
    trial = tmp_path / "trial"
    result = module.setup(trial)
    clone = next(args for args in calls if "clone" in args)
    assert clone[-2] == str(repo)
    assert (trial / "platform/app.py").read_text() == "published source\n"
    assert not (trial / "platform/history-only.py").exists()
    assert (trial / "platform/backend/.venv/local-dependency").read_text() == "private dependency\n"
    assert (Path(result["baseline"]) / "app.py").read_text() == "published source\n"
    assert result["platform_supervisor_matches_production"] is True
