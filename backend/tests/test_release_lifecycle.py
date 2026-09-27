from __future__ import annotations

import importlib.util
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "scripts" / "elira_release.py"
SPEC = importlib.util.spec_from_file_location("elira_release_test", SOURCE)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

BACKEND = '''
import json, os, sqlite3, sys, threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
data = Path(os.environ["ELIRA_DATA_DIR"])
data.mkdir(parents=True, exist_ok=True)
rid = os.environ["ELIRA_RELEASE_ID"]
with closing(sqlite3.connect(data / "state.db")) as db, db:
    db.execute("CREATE TABLE IF NOT EXISTS state(value TEXT)")
    db.execute("DELETE FROM state")
    db.execute("INSERT INTO state VALUES(?)", (rid,))
if (data / "fail-release").exists() and (data / "fail-release").read_text() == rid:
    sys.exit(3)
class Handler(BaseHTTPRequestHandler):
    admitted = False
    def log_message(self, *args): pass
    def do_GET(self):
        self.respond({"service":"elira-ai-api", "release_id":rid,
            "instance_id":os.environ["ELIRA_RELEASE_INSTANCE"],
            "draining":not self.admitted})
    def respond(self, value):
        payload = json.dumps(value).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(payload)
    def do_POST(self):
        assert self.headers["x-elira-release-token"] == os.environ["ELIRA_RELEASE_TOKEN"]
        action = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["action"]
        if action in {"activate", "resume"}: Handler.admitted = True
        if action == "drain": Handler.admitted = False
        self.respond({"ok":True,"idle":not (data / "busy").exists()})
        if action == "shutdown": threading.Thread(target=server.shutdown).start()
server = HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
server.serve_forever()
'''


class FixtureManager(release.ReleaseManager):
    """Real child processes; substitute tiny applications for a Tauri/Rust build."""

    def _verification_commands(self, root):
        return [[sys.executable, "verify.py"]]

    def _find_executable(self, root):
        return "desktop.py"

    def _backend_command(self, release_id):
        return [sys.executable, str(self.path(release_id) / "backend" / "stub.py"), str(self.port)]

    def _start_ui(self, release_id, executable):
        env = self._environment(release_id)
        env.pop("ELIRA_RELEASE_TOKEN", None)
        self.ui = self.host.popen([sys.executable, str(self.path(release_id) / executable)],
                                 cwd=self.path(release_id), env=env, desktop=True)
        self._save_processes()


def _candidate(manager, name):
    root = manager.candidate_path(name)
    root.mkdir(parents=True)
    for directory in ("backend/.venv", "node_modules", "frontend/node_modules", "frontend/dist"):
        (root / directory).mkdir(parents=True)
        (root / directory / "dependency.txt").write_text("v1", encoding="utf-8")
    (root / ".gitignore").write_text(".venv/\nnode_modules/\nfrontend/dist/\nvalidation.marker\n", encoding="utf-8")
    (root / "backend/stub.py").write_text(BACKEND, encoding="utf-8")
    (root / "desktop.py").write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    (root / "verify.py").write_text(
        "print('checks actually ran')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root


class RecordingHost(release.LocalProcessHost):
    """Real local children, recording routing; not a Windows identity/ACL proof."""

    def __init__(self):
        self.calls = []
        self.processes = []

    def user_environment(self):
        return {**super().user_environment(), "RELEASE_TEST_USER_ENV": "present"}

    def run(self, arguments, **kwargs):
        self.calls.append(("run", list(arguments), kwargs.copy()))
        return super().run(arguments, **kwargs)

    def popen(self, arguments, **kwargs):
        self.calls.append(("popen", list(arguments), kwargs.copy()))
        # The fake desktop is a console Python program. Keep its window hidden.
        process = ClosableProcess(super().popen(arguments, **{**kwargs, "desktop": False}))
        self.processes.append(process)
        return process


class ClosableProcess:
    """Exercise explicit Windows-style handle release using real local children."""

    def __init__(self, process):
        self.process = process
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.process, name)

    def close(self):
        assert not self.closed
        assert self.process.poll() is not None
        self.closed = True


class FixtureStorage:
    """Storage routing fixture only; secure Windows copying is tested separately."""

    def __init__(self):
        self.copies = []

    @contextmanager
    def user_access(self):
        yield

    def publish(self, source, destination, *, files=None):
        self.copies.append(("publish", source, destination))
        destination.mkdir(parents=True, exist_ok=False)
        names = files if files is not None else [p.relative_to(source).as_posix() for p in release._tree_files(source)]
        for name in names:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)

    def export(self, source, destination):
        self.copies.append(("export", source, destination))
        shutil.copytree(source, destination)


def _relocatable_venv(root):
    import pip._vendor.distlib

    environment = root / "backend/.venv"
    release.LocalProcessHost().run([sys.executable, "-m", "venv", "--copies", "--without-pip", str(environment)], cwd=root)
    packages = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    vendor = packages / "pip/_vendor"
    vendor.mkdir(parents=True)
    (packages / "pip/__init__.py").write_text("", encoding="utf-8")
    (vendor / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(Path(pip._vendor.distlib.__file__).parent, vendor / "distlib",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    metadata = packages / "release_fixture-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: release_fixture\nVersion: 1.0\n", encoding="utf-8")
    (metadata / "entry_points.txt").write_text("[console_scripts]\nrelease-fixture = release_fixture:main\n", encoding="utf-8")
    (packages / "release_fixture.py").write_text("import sys\ndef main():\n    print(sys.executable)\n", encoding="utf-8")


def _protected_manager(tmp_path):
    layout = release.ReleaseLayout(
        store=tmp_path / "private/state", candidates=tmp_path / "user/candidates",
        published=tmp_path / "private/published", data=tmp_path / "user/data",
        journals=tmp_path / "user/journals", config_root=tmp_path / "user/config",
        worker_root=tmp_path / "user/work", worker_python=Path(sys.executable), worker_script=SOURCE,
        service_name="EliraFoundationProof")
    host, storage = RecordingHost(), FixtureStorage()
    manager = FixtureManager(tmp_path, layout=layout, host=host, storage=storage,
                             port=release._free_port(), startup_timeout=10)
    return manager, host, storage


def _db_value(data):
    with closing(sqlite3.connect(data / "state.db")) as db:
        return db.execute("SELECT value FROM state").fetchone()[0]


def test_release_real_process_lifecycle_verification_drain_and_recovery(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ELIRA_DATA_DIR", str(data))
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "journals"))
    host = RecordingHost()
    manager = FixtureManager(tmp_path, port=release._free_port(), startup_timeout=5, host=host)
    a = _candidate(manager, "a")
    b = _candidate(manager, "b")
    native_dependency = b / ".runtime/poppler/bin/pdftoppm.exe"
    native_dependency.parent.mkdir(parents=True)
    native_dependency.write_bytes(b"native dependency v1")
    try:
        manager.verify("a")
        manager.verify("b")
        assert len([item for item in host.calls if item[0] == "popen"]) == 2
        assert all(item[2]["env"]["RELEASE_TEST_USER_ENV"] == "present" for item in host.calls)
        assert "checks actually ran" in (manager.store / "logs/a-verify.log").read_text(encoding="utf-8")
        assert not (data / "state.db").exists()  # Verification used isolated data.
        manager.request("a")
        assert manager.apply_pending()
        assert manager._http()["release_id"] == "a"
        assert _db_value(data) == "a"

        (data / "busy").touch()
        manager.request("b")
        assert not manager.apply_pending()
        assert manager.state()["active"] == "a"
        (data / "busy").unlink()
        (data / "fail-release").write_text("b", encoding="utf-8")
        assert not manager.apply_pending()  # B mutated the DB, then failed startup.
        assert manager.state()["active"] == "a"
        assert manager.state()["transition"] is None
        assert _db_value(data) == "a"
        assert manager._http()["release_id"] == "a"

        (data / "fail-release").unlink()
        manager.request("b")
        assert manager.apply_pending()
        assert manager.state()["active"] == "b"
        assert manager.state()["previous"] == "a"

        manager.request("a")
        assert manager.apply_pending()  # Code rollback goes through the same startup protocol.
        manager.request("b")
        backend_pid = manager.backend.pid
        (b / "backend/.venv/dependency.txt").write_text("changed after verification", encoding="utf-8")
        assert not manager.apply_pending()
        assert manager.backend.pid == backend_pid
        assert manager.state()["active"] == "a"
        with pytest.raises(ValueError, match="changed after verification"):
            manager.request("b")
        (b / "backend/.venv/dependency.txt").write_text("v1", encoding="utf-8")
        manager.checked("b")
        native_dependency.write_bytes(b"native dependency changed after verification")
        with pytest.raises(ValueError, match="changed after verification"):
            manager.request("b")
        assert manager.backend.pid == backend_pid

        manager._http("drain")
        manager._stop_ui()
        manager._stop_backend()
        backup = manager.store / "backups" / "interrupted"
        release._snapshot_databases(data, backup)
        state = manager.state()
        state["transition"] = {"from": "a", "to": "b", "phase": "switching", "backup": str(backup)}
        manager._save(state)
        with closing(sqlite3.connect(data / "state.db")) as db, db:
            db.execute("UPDATE state SET value='partial migration'")
        assert manager.recover()["active"] == "a"
        assert _db_value(data) == "a"
        assert manager.recover()["transition"] is None  # Repeated recovery is idempotent.
    finally:
        manager._stop_ui()
        if manager.backend is not None:
            manager._http("drain")
        manager._stop_backend()
    assert host.processes and all(process.closed for process in host.processes)


def test_protected_layout_publishes_final_venv_and_routes_database_recovery_through_user_worker(tmp_path, monkeypatch):
    manager, host, storage = _protected_manager(tmp_path)
    a, b = _candidate(manager, "a"), _candidate(manager, "b")
    _relocatable_venv(a)
    _relocatable_venv(b)
    original = manager.fingerprint(a)
    try:
        first = manager.verify("a")
        manager.verify("b")
        assert manager.fingerprint(a) == original
        assert first["root"] == str(manager.path("a")) != str(a)
        assert not (manager.path("a") / ".git").exists()
        assert not (manager.data / "state.db").exists()
        launcher = manager._python(manager.path("a")).parent / ("release-fixture.exe" if os.name == "nt" else "release-fixture")
        output = subprocess.check_output([str(launcher)], cwd=tmp_path, text=True,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
        assert Path(output) == manager._python(manager.path("a"))
        (a / "backend/stub.py").write_text("edited mutable candidate", encoding="utf-8")
        assert manager.checked("a")["sha256"] == first["sha256"]
        with pytest.raises(ValueError, match="immutable"):
            manager.verify("a")

        def forbidden_service_database_write(*args, **kwargs):
            raise AssertionError("The supervisor must not mutate user databases")

        monkeypatch.setattr(release, "_snapshot_databases", forbidden_service_database_write)
        monkeypatch.setattr(release, "_restore_databases", forbidden_service_database_write)
        manager.request("a")
        assert manager.apply_pending()
        for name in ("facts.sqlite", "jobs.sqlite3"):
            with closing(sqlite3.connect(manager.data / name)) as db, db:
                db.execute("CREATE TABLE preserved(value TEXT)")
                db.execute("INSERT INTO preserved VALUES('safe')")
        (manager.data / "busy").touch()
        manager.request("b")
        assert not manager.apply_pending()
        assert manager.state()["pending"] == "b"
        (manager.data / "busy").unlink()
        (manager.data / "fail-release").write_text("b", encoding="utf-8")
        assert not manager.apply_pending()
        assert manager.state()["active"] == "a"
        assert _db_value(manager.data) == "a"
        for name in ("facts.sqlite", "jobs.sqlite3"):
            with closing(sqlite3.connect(manager.data / name)) as db:
                assert db.execute("SELECT value FROM preserved").fetchone()[0] == "safe"
        assert any(kind == "export" for kind, _, _ in storage.copies)
        operations = [args[args.index("worker") + 1] for kind, args, _ in host.calls if "worker" in args]
        assert {"snapshot", "restore", "relocate", "restore-environment", "cleanup"} <= set(operations)
        for kind, args, options in host.calls:
            if kind == "popen":
                env = options["env"]
                assert env["ELIRA_FOUNDATION_SERVICE"] == "EliraFoundationProof"
                assert env["ELIRA_CONFIG_ROOT"] == str(manager.layout.config_root)
                assert any(str(arg).startswith(str(manager.path(env["ELIRA_RELEASE_ID"]))) for arg in args)
                if options.get("desktop"):
                    assert "ELIRA_RELEASE_TOKEN" not in env
        assert not list(manager.layout.worker_root.iterdir())
    finally:
        manager._stop_ui()
        if manager.backend is not None:
            manager._http("drain")
        manager._stop_backend()
    assert host.processes and all(process.closed for process in host.processes)


def test_protected_publish_mismatch_cannot_write_verified_receipt_and_restores_candidate(tmp_path):
    manager, host, storage = _protected_manager(tmp_path)
    root = _candidate(manager, "a")
    _relocatable_venv(root)
    before = manager.fingerprint(root)
    publish = storage.publish

    def corrupt_copy(source, destination, **kwargs):
        publish(source, destination, **kwargs)
        (destination / "backend/stub.py").write_text("changed while publishing", encoding="utf-8")

    storage.publish = corrupt_copy
    with pytest.raises(ValueError, match="Published bytes"):
        manager.verify("a")
    assert manager.fingerprint(root) == before
    assert release._read_json(manager.record("a"))["status"] == "failed"
    with pytest.raises(ValueError, match="not passed"):
        manager.request("a")
    assert manager.state()["pending"] is None
    assert not any(kind == "popen" for kind, _, _ in host.calls)
    with pytest.raises(ValueError, match="explicit user process host"):
        release.ReleaseManager(tmp_path, layout=manager.layout, storage=storage)


def test_managed_cli_delegates_without_constructing_local_manager_and_preserves_failure(monkeypatch):
    calls = []
    client = ["protected-python", "-I", "-S", "-B", "protected-client", "--service", "EliraFoundationProof"]
    monkeypatch.setattr(release, "_foundation_client_command", lambda: client)

    def forbidden_manager(*args, **kwargs):
        raise AssertionError("Managed CLI must not construct a local supervisor")

    def observed_run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 7)

    monkeypatch.setattr(release, "ReleaseManager", forbidden_manager)
    monkeypatch.setattr(release.subprocess, "run", observed_run)
    for operation in ("prepare", "verify", "request", "status", "rollback", "run"):
        identifier = ["v2"] if operation in {"prepare", "verify", "request"} else []
        monkeypatch.setattr(sys, "argv", [str(SOURCE), operation, *identifier])
        assert release.main() == 7
        assert calls[-1] == client + ["open" if operation == "run" else operation, *identifier, "--wait"]


def test_managed_user_child_merges_local_configuration_without_overwriting_bound_runtime(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ELIRA_FS_UNRESTRICTED=1\nRELEASE_TEST_SETTING=base\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text(
        "ELIRA_FS_UNRESTRICTED=0\nRELEASE_TEST_SETTING=local\nELIRA_DATA_DIR=wrong\n"
        "ELIRA_RELEASE_STAGING=0\nLOCAL_EMBED_ENABLED=true\n", encoding="utf-8")
    for key in ("ELIRA_FS_UNRESTRICTED", "RELEASE_TEST_SETTING"):
        monkeypatch.setenv(key, "temporary")
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ELIRA_DATA_DIR", "bound-check-data")
    monkeypatch.setenv("ELIRA_RELEASE_STAGING", "1")
    monkeypatch.setenv("LOCAL_EMBED_ENABLED", "false")
    release._load_application_environment(tmp_path)
    assert os.environ["ELIRA_FS_UNRESTRICTED"] == "0"
    assert os.environ["RELEASE_TEST_SETTING"] == "local"
    assert os.environ["ELIRA_DATA_DIR"] == "bound-check-data"
    assert os.environ["ELIRA_RELEASE_STAGING"] == "1"
    assert os.environ["LOCAL_EMBED_ENABLED"] == "false"


def test_process_identity_uses_held_handle_then_user_host_during_recovery(tmp_path, monkeypatch):
    class Process:
        pid = 43210
        creation_identity = "win:fixture-creation"

        def poll(self):
            return None

    class IdentityHost(release.LocalProcessHost):
        def __init__(self):
            self.identities = {Process.pid: Process.creation_identity}
            self.observed = []
            self.stopped = []

        def process_identity(self, pid):
            self.observed.append(pid)
            return self.identities.get(pid)

        def terminate_owned(self, pid, identity):
            assert self.identities[pid] == identity
            self.stopped.append(pid)
            self.identities.pop(pid)

    def forbidden_service_query(pid):
        raise AssertionError("The supervisor must not reopen a user process under its service identity")

    host = IdentityHost()
    manager = release.ReleaseManager(tmp_path, host=host)
    monkeypatch.setattr(release, "_process_identity", forbidden_service_query)
    manager.ui = Process()
    manager._save_processes()
    assert host.observed == []  # Creation identity came from the held process handle.
    assert release._read_json(manager.owned("live.json"))["ui"]["identity"] == Process.creation_identity
    manager.ui = None  # A fresh supervisor only has the private persisted identity.
    manager._reap_owned_processes()
    assert host.stopped == [Process.pid]
    assert host.observed and all(pid == Process.pid for pid in host.observed)
