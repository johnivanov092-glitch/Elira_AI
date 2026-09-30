from __future__ import annotations

import importlib.util
from contextlib import closing, contextmanager, nullcontext
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
            "draining":not self.admitted, "admitted":self.admitted})
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
        "from pathlib import Path\n"
        "Path('frontend/dist').mkdir(parents=True, exist_ok=True)\n"
        "Path('frontend/dist/fixture.js').write_text('verified frontend output')\n"
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


def _request_and_confirm(manager, release_id):
    proposal = manager.request(release_id)["confirmation"]
    assert release.read_release_progress(manager.store)["phase"] == "awaiting_confirmation"
    assert manager.state().get("pending") is None
    return manager.confirm(proposal["request_id"])


def _db_value(data):
    with closing(sqlite3.connect(data / "state.db")) as db:
        return db.execute("SELECT value FROM state").fetchone()[0]


@pytest.mark.parametrize("legacy_profile_inventory", [False, True])
def test_snapshot_and_restore_never_open_or_remove_native_browser_databases(tmp_path, monkeypatch, legacy_profile_inventory):
    data, snapshot = tmp_path / "data", tmp_path / "snapshot"
    profile = data / "webview2/EBWebView/Default"
    profile.mkdir(parents=True)
    application = data / "chats.db"
    browser = profile / "locked.db"
    with closing(sqlite3.connect(application)) as db, db:
        db.execute("CREATE TABLE messages(text TEXT)")
        db.execute("INSERT INTO messages VALUES('original')")
    with closing(sqlite3.connect(browser)) as db, db:
        db.execute("CREATE TABLE browser_only(value TEXT)")
    browser_bytes = browser.read_bytes()
    connect = sqlite3.connect
    def app_only(path, *args, **kwargs):
        assert "webview2" not in str(path).casefold(), "Browser locks must not enter the application backup."
        return connect(path, *args, **kwargs)
    monkeypatch.setattr(release.sqlite3, "connect", app_only)
    release._snapshot_databases(data, snapshot)
    assert release._read_json(snapshot / "snapshot.json")["databases"] == ["chats.db"]
    assert not (snapshot / "webview2").exists()
    if legacy_profile_inventory:
        release._write_json(snapshot / "snapshot.json", {"databases": ["chats.db", "webview2/EBWebView/Default/locked.db"]})
    with closing(connect(application)) as db, db:
        db.execute("UPDATE messages SET text='failed migration'")
    fresh_browser = profile / "created.db"
    fresh_browser.write_bytes(b"fresh browser cache")
    release._restore_databases(data, snapshot)
    assert browser.read_bytes() == browser_bytes
    assert fresh_browser.read_bytes() == b"fresh browser cache"
    with closing(connect(application)) as db:
        assert db.execute("SELECT text FROM messages").fetchone()[0] == "original"


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
        assert release.read_release_progress(manager.store)["phase"] == "verified"
        manager.verify("b")
        assert len([item for item in host.calls if item[0] == "popen"]) == 2
        assert all(item[2]["env"]["RELEASE_TEST_USER_ENV"] == "present" for item in host.calls)
        assert "checks actually ran" in (manager.store / "logs/a-verify.log").read_text(encoding="utf-8")
        assert not (data / "state.db").exists()  # Verification used isolated data.
        _request_and_confirm(manager, "a")
        assert release.read_release_progress(manager.store)["phase"] == "waiting"
        assert manager.apply_pending()
        assert release.read_release_progress(manager.store)["phase"] == "completed"
        assert manager._http()["release_id"] == "a"
        assert _db_value(data) == "a"

        (data / "busy").touch()
        _request_and_confirm(manager, "b")
        assert not manager.apply_pending()
        assert release.read_release_progress(manager.store)["phase"] == "waiting"
        assert manager.state()["active"] == "a"
        (data / "busy").unlink()
        (data / "fail-release").write_text("b", encoding="utf-8")
        assert not manager.apply_pending()  # B mutated the DB, then failed startup.
        assert release.read_release_progress(manager.store)["phase"] == "failed"
        assert manager.state()["active"] == "a"
        assert manager.state()["transition"] is None
        assert _db_value(data) == "a"
        assert manager._http()["release_id"] == "a"

        (data / "fail-release").unlink()
        _request_and_confirm(manager, "b")
        assert manager.apply_pending()
        assert manager.state()["active"] == "b"
        assert manager.state()["previous"] == "a"
        with closing(sqlite3.connect(data / "chats.db")) as db, db:
            db.execute("CREATE TABLE messages(text TEXT)")
            db.execute("INSERT INTO messages VALUES ('created after successful update')")
        before = manager.state()
        with pytest.raises(ValueError, match="selection changed"):
            manager.request("a", operation="rollback", expected_active="obsolete", expected_previous="a")
        assert manager.state() == before
        proposal = manager.request("a", operation="rollback", expected_active="b", expected_previous="a")
        manager.confirm(proposal["confirmation"]["request_id"])
        assert manager.apply_pending()  # Code rollback goes through the same startup protocol.
        assert manager.state()["previous"] == "b" and manager.checked("b")
        assert b.exists() and a.exists()
        with closing(sqlite3.connect(data / "chats.db")) as db:
            assert db.execute("SELECT text FROM messages").fetchone()[0] == "created after successful update"
        _request_and_confirm(manager, "b")
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
        assert release.read_release_progress(manager.store)["phase"] == "interrupted"
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
        _request_and_confirm(manager, "a")
        assert manager.apply_pending()
        for name in ("facts.sqlite", "jobs.sqlite3"):
            with closing(sqlite3.connect(manager.data / name)) as db, db:
                db.execute("CREATE TABLE preserved(value TEXT)")
                db.execute("INSERT INTO preserved VALUES('safe')")
        (manager.data / "busy").touch()
        _request_and_confirm(manager, "b")
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
        _request_and_confirm(manager, "a")
    assert manager.state()["pending"] is None
    assert not any(kind == "popen" for kind, _, _ in host.calls)


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_three_installed_releases_preserve_forward_return_candidates_and_data(tmp_path, monkeypatch, cleanup_failure):
    manager, _, storage = _protected_manager(tmp_path)
    # Tiny sealed applications exercise real child startup/admission; the
    # separate publication tests cover the full venv/build verification path.
    for name in "abcd":
        source = _candidate(manager, name)
        storage.publish(source, manager.path(name))
        release._write_json(manager.record(name), {
            "release_id": name, "status": "verified", "root": str(manager.path(name)),
            "executable": "desktop.py", "sha256": manager.fingerprint(manager.path(name), executable="desktop.py"),
        })
    try:
        for name in "abc":
            _request_and_confirm(manager, name)
            assert manager.apply_pending()
        assert manager.state()["installed_releases"] == list("abc")
        with closing(sqlite3.connect(manager.data / "chats.db")) as db, db:
            db.execute("CREATE TABLE messages(text TEXT)")
            db.execute("INSERT INTO messages VALUES('preserved through return')")
        (manager.data / "fail-release").write_text("d", encoding="utf-8")
        _request_and_confirm(manager, "d")
        assert not manager.apply_pending()
        assert manager.path("a").is_dir()  # Failed startup never retires a reserve.
        (manager.data / "fail-release").unlink()

        remove = release.shutil.rmtree
        def guarded_remove(path, *args, **kwargs):
            if cleanup_failure and path == manager.path("a"):
                raise PermissionError("fixture sharing violation")
            return remove(path, *args, **kwargs)

        monkeypatch.setattr(release.shutil, "rmtree", guarded_remove)
        _request_and_confirm(manager, "d")
        assert manager.apply_pending()
        assert manager.state()["active"] == "d"
        assert release.read_release_progress(manager.store)["phase"] == "completed"
        if cleanup_failure:
            assert manager.path("a").is_dir()
            monkeypatch.setattr(release.shutil, "rmtree", remove)
            manager._stop_ui()
            manager._stop_backend()
            manager._launch_active("d")  # Cleanup retries after successful admission.
        assert manager.state()["installed_releases"] == list("bcd")
        assert not manager.path("a").exists()
        assert release._read_json(manager.record("a"))["status"] == "retired"
        assert all(manager.candidate_path(name).is_dir() for name in "abcd")

        for active, target in (("d", "c"), ("c", "d")):
            proposal = manager.request(target, operation="rollback", expected_active=active,
                                       expected_previous=target)["confirmation"]
            manager.confirm(proposal["request_id"])
            assert manager.apply_pending()
            assert manager.state()["active"] == target
            assert manager.state()["previous"] == active
            assert manager.state()["installed_releases"] == list("bcd")
            assert manager.checked(active) and manager.checked(target)
        with closing(sqlite3.connect(manager.data / "chats.db")) as db:
            assert db.execute("SELECT text FROM messages").fetchone()[0] == "preserved through return"
    finally:
        manager._stop_ui()
        manager._stop_backend()
    with pytest.raises(ValueError, match="explicit user process host"):
        release.ReleaseManager(tmp_path, layout=manager.layout, storage=storage)


def _legacy_bootstrap_source(tmp_path):
    legacy = FixtureManager(tmp_path)
    source = _candidate(legacy, "legacy-active")
    _relocatable_venv(source)
    receipt = {"release_id": "legacy-active", "status": "verified", "root": str(source),
               "executable": "desktop.py", "sha256": legacy.fingerprint(source, executable="desktop.py")}
    release._write_json(legacy.record("legacy-active"), receipt)
    legacy._save({"active": "legacy-active", "previous": None, "pending": None, "transition": None})
    return legacy, source, receipt


def test_foundation_bootstrap_copies_exact_legacy_source_with_independent_environment(tmp_path):
    legacy, source, receipt = _legacy_bootstrap_source(tmp_path)
    manager, host, storage = _protected_manager(tmp_path)
    prepared = manager.prepare("foundation-baseline")
    candidate = manager.candidate_path("foundation-baseline")
    assert prepared["base_release_id"] == "legacy-active"
    assert prepared["base_sha256"] == receipt["sha256"]
    assert prepared["source_root"] == str(source)
    assert prepared["base_runtime"] == "legacy"
    assert prepared["python"] == str(manager._python(candidate))
    assert (candidate / "backend/stub.py").read_bytes() == (source / "backend/stub.py").read_bytes()
    (candidate / "backend/.venv/dependency.txt").write_text("candidate-only dependency", encoding="utf-8")
    assert legacy.fingerprint(source, executable="desktop.py") == receipt["sha256"]
    assert legacy.state()["active"] == "legacy-active"
    assert not list(manager.layout.worker_root.iterdir())
    assert any("worker" in args and "prepare" in args for _, args, _ in host.calls)
    assert not manager.path("foundation-baseline").exists()  # Publication still requires verify.
    verified = manager.verify("foundation-baseline")
    for field in ("base_release_id", "base_sha256", "base_runtime", "source_root"):
        assert verified[field] == prepared[field]


@pytest.mark.parametrize("damage", ["changed", "wrong_root", "wrong_id", "traversal", "executable", "transition"])
def test_foundation_bootstrap_refuses_changed_or_misdirected_legacy_without_writes(tmp_path, damage):
    legacy, source, receipt = _legacy_bootstrap_source(tmp_path)
    if damage == "changed":
        (source / "backend/stub.py").write_text("changed", encoding="utf-8")
    elif damage == "wrong_root":
        receipt["root"] = str(tmp_path / "outside")
    elif damage == "wrong_id":
        receipt["release_id"] = "another"
    elif damage == "executable":
        receipt["executable"] = "../outside.exe"
    else:
        state = legacy.state()
        state["active" if damage == "traversal" else "transition"] = "../outside" if damage == "traversal" else {"phase": "switching"}
        legacy._save(state)
    release._write_json(legacy.record("legacy-active"), receipt)
    manager, host, storage = _protected_manager(tmp_path)
    with pytest.raises(ValueError):
        manager.prepare("foundation-baseline")
    assert not manager.candidate_path("foundation-baseline").exists()
    assert not manager.record("foundation-baseline").exists()
    assert host.calls == []


@pytest.mark.parametrize("selected", ["active", "previous", "pending"])
def test_selected_legacy_release_cannot_be_reverified_or_lose_its_receipt(tmp_path, selected):
    manager = FixtureManager(tmp_path)
    _candidate(manager, "selected")
    receipt = {"release_id": "selected", "status": "verified", "sha256": "preserved"}
    release._write_json(manager.record("selected"), receipt)
    state = manager.state()
    state[selected] = "selected"
    manager._save(state)
    with pytest.raises(ValueError, match="immutable"):
        manager.verify("selected")
    assert release._read_json(manager.record("selected")) == receipt
    assert not (manager.store / "logs").exists()


def test_startup_that_mutates_candidate_cannot_receive_verified_receipt(tmp_path, monkeypatch):
    manager = FixtureManager(tmp_path, port=release._free_port(), startup_timeout=5)
    root = _candidate(manager, "mutating")
    backend = root / "backend/stub.py"
    backend.write_text(BACKEND.replace('data = Path(os.environ["ELIRA_DATA_DIR"])',
        'Path(__file__).with_name("changed.py").write_text("changed during startup")\n'
        'data = Path(os.environ["ELIRA_DATA_DIR"])'), encoding="utf-8")
    with pytest.raises(ValueError, match="runtime changed during staged startup"):
        manager.verify("mutating")
    assert release._read_json(manager.record("mutating"))["status"] == "failed"


@pytest.mark.parametrize("failure", ["refused", "wrong_release", "wrong_instance", "not_admitted", "draining"])
def test_activation_requires_acknowledgement_and_matching_admitted_health(tmp_path, monkeypatch, failure):
    manager = release.ReleaseManager(tmp_path)
    health = {"service": "elira-ai-api", "release_id": "candidate", "instance_id": manager.instance,
              "admitted": True, "draining": False}
    if failure == "wrong_release":
        health["release_id"] = "another"
    elif failure == "wrong_instance":
        health["instance_id"] = "another"
    elif failure == "not_admitted":
        health["admitted"] = False
    elif failure == "draining":
        health["draining"] = True
    monkeypatch.setattr(manager, "_http", lambda action=None: {"ok": failure != "refused"} if action else health)
    with pytest.raises(RuntimeError, match="activation|health"):
        manager._activate("candidate")


@pytest.mark.parametrize("identity_matches", [True, False])
def test_activation_retries_transient_ack_only_for_same_admitted_instance(tmp_path, monkeypatch, identity_matches):
    manager = release.ReleaseManager(tmp_path)
    calls = []
    failure = release.HTTPError("http://local/control", 500, "fixture scheduler race", {}, None)

    def http(action=None):
        calls.append(action)
        if action:
            if calls.count("activate") == 1:
                raise failure
            return {"ok": True}
        return {"service": "elira-ai-api", "release_id": "candidate", "admitted": True, "draining": False,
                "instance_id": manager.instance if identity_matches else "unowned"}

    monkeypatch.setattr(manager, "_http", http)
    if identity_matches:
        manager._activate("candidate")
        assert calls == ["activate", None, "activate", None]
    else:
        with pytest.raises(release.HTTPError) as caught:
            manager._activate("candidate")
        assert caught.value is failure
        assert calls == ["activate", None]


def test_activation_does_not_accept_health_when_all_acknowledgements_fail(tmp_path, monkeypatch):
    manager = release.ReleaseManager(tmp_path)
    calls = []
    def http(action=None):
        calls.append(action)
        if action:
            raise release.HTTPError("http://local/control", 500, "persistent failure", {}, None)
        return {"service": "elira-ai-api", "release_id": "candidate", "instance_id": manager.instance,
                "admitted": True, "draining": False}
    monkeypatch.setattr(manager, "_http", http)
    with pytest.raises(release.HTTPError):
        manager._activate("candidate")
    assert calls.count("activate") == 3


def test_managed_cli_delegates_without_constructing_local_manager_and_preserves_failure(monkeypatch):
    calls = []
    client = ["protected-python", "-I", "-S", "-B", "protected-client", "--service", "EliraFoundationProof"]
    monkeypatch.setattr(release, "_foundation_client_command", lambda **kwargs: client)

    def forbidden_manager(*args, **kwargs):
        raise AssertionError("Managed CLI must not construct a local supervisor")

    def observed_run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 7)

    monkeypatch.setattr(release, "ReleaseManager", forbidden_manager)
    monkeypatch.setattr(release.subprocess, "run", observed_run)
    for operation in ("prepare", "verify", "request", "confirm", "status", "rollback", "run"):
        identifier = ["v2"] if operation in {"prepare", "verify", "request"} else []
        if operation == "confirm":
            identifier = ["a" * 32]
        monkeypatch.setattr(sys, "argv", [str(SOURCE), operation, *identifier])
        assert release.main() == 7
        assert calls[-1] == client + ["open" if operation == "run" else operation, *identifier, "--wait"]


@pytest.mark.skipif(os.name != "nt", reason="Windows registered Foundation binding")
def test_installed_foundation_never_captures_commands_for_an_isolated_platform(tmp_path, monkeypatch):
    import winreg

    installed, production = tmp_path / "installed", tmp_path / "production"
    for relative in ("python/python.exe", "host/foundation_client.py"):
        target = installed / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture")
    bindings = {"InstallRoot": (str(installed), winreg.REG_SZ),
                "Platform": (str(production), winreg.REG_SZ), "Port": (8000, winreg.REG_DWORD)}
    monkeypatch.setattr(winreg, "OpenKey", lambda *args: nullcontext("protected-key"))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: bindings[name])
    monkeypatch.setenv("ELIRA_FOUNDATION_MANAGED", "1")
    monkeypatch.setenv("ELIRA_FOUNDATION_SERVICE", "EliraFoundation")
    assert release._foundation_client_command(platform=production, port=8000)[0] == str(installed / "python/python.exe")
    assert release._foundation_client_command(platform=tmp_path / "isolated", port=18581) is None
    with pytest.raises(ValueError, match="registered port"):
        release._foundation_client_command(platform=production, port=18581)
    with pytest.raises(ValueError, match="different"):
        release._foundation_client_command(platform=tmp_path / "isolated", port=8000)
    (installed / "python/python.exe").unlink()
    with pytest.raises(RuntimeError, match="incomplete"):
        release._foundation_client_command(platform=production, port=8000)
    assert release._foundation_client_command(platform=tmp_path / "isolated", port=18581) is None


def test_isolated_legacy_environment_does_not_inherit_foundation_ownership(tmp_path, monkeypatch):
    for name in ("ELIRA_FOUNDATION_MANAGED", "ELIRA_FOUNDATION_SERVICE", "ELIRA_FOUNDATION_PYTHON", "ELIRA_FOUNDATION_CLIENT"):
        monkeypatch.setenv(name, "production-owned")
    manager = release.ReleaseManager(tmp_path)
    env = manager._environment("isolated")
    assert not any(name.startswith("ELIRA_FOUNDATION_") for name in env)
    assert env["ELIRA_PLATFORM_ROOT"] == str(tmp_path)


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


def test_progress_reader_is_read_only_and_rejects_incomplete_success(tmp_path):
    store = tmp_path / "not-created"
    assert release.read_release_progress(store)["phase"] == "idle"
    assert not store.exists()
    release._write_json(store / "progress.json", {"version": 1, "phase": "completed"})
    before = (store / "progress.json").read_bytes()
    assert release.read_release_progress(store)["phase"] == "unavailable"
    assert (store / "progress.json").read_bytes() == before


def test_invalid_candidate_id_retains_safe_failed_progress(tmp_path):
    manager = release.ReleaseManager(tmp_path)
    with pytest.raises(ValueError, match="Release id"):
        manager.prepare("недопустимый кандидат")
    progress = release.read_release_progress(manager.store)
    assert progress["phase"] == "failed"
    assert progress["release_id"] is None and "Release id" in progress["error"]


def test_progress_fences_old_completion_and_detects_reused_owner_without_writes(tmp_path, monkeypatch):
    old = release._begin_progress(tmp_path, "prepare", "a", "preparing")
    current = release._begin_progress(tmp_path, "verify", "b", "checking")
    assert not release._advance_progress(tmp_path, old, "prepared")
    assert release.read_release_progress(tmp_path)["operation_id"] == current
    assert release.read_release_progress(tmp_path)["phase"] == "checking"
    before = (tmp_path / "progress.json").read_bytes()
    monkeypatch.setattr(release, "_process_identity", lambda pid: "another-process-incarnation")
    assert release.read_release_progress(tmp_path)["phase"] == "interrupted"
    assert (tmp_path / "progress.json").read_bytes() == before


def test_progress_waiting_is_pending_handoff_not_request_process_liveness(tmp_path, monkeypatch):
    operation = release._begin_progress(tmp_path, "request", "b", "checking")
    release._write_json(tmp_path / "state.json", {"active": "a", "pending": "b", "last_confirmation": {
        "request_id": "a" * 32, "release_id": "b", "sha256": "b" * 64}})
    release._advance_progress(tmp_path, operation, "waiting")
    monkeypatch.setattr(release, "_process_identity", lambda pid: None)
    waiting = release.read_release_progress(tmp_path)
    assert waiting["phase"] == "waiting" and waiting["owner_pid"] is None
    release._write_json(tmp_path / "state.json", {"active": "a", "pending": None})
    assert release.read_release_progress(tmp_path)["phase"] == "interrupted"


@pytest.mark.parametrize("accepted", [None, {}, {"request_id": "bad", "release_id": "b", "sha256": "b" * 64},
    {"request_id": "a" * 32, "release_id": "other", "sha256": "b" * 64},
    {"request_id": "a" * 32, "release_id": "b", "sha256": "bad"}])
def test_progress_waiting_requires_a_matching_durable_confirmation(tmp_path, accepted):
    release._begin_progress(tmp_path, "confirm", "b", "waiting")
    release._write_json(tmp_path / "state.json", {"pending": "b", "last_confirmation": accepted})
    assert release.read_release_progress(tmp_path)["phase"] == "interrupted"


def test_request_of_already_selected_release_preserves_progress_and_has_no_proposal(tmp_path, monkeypatch):
    manager = release.ReleaseManager(tmp_path)
    manager._save({"active": "a", "previous": None, "pending": None, "transition": None})
    monkeypatch.setattr(manager, "checked", lambda release_id: {"status": "verified"})
    manager.request("a")
    assert release.read_release_progress(manager.store)["phase"] == "idle"
    assert manager.state().get("confirmation") is None


def test_confirmation_survives_restart_without_approving_or_starting_work(tmp_path, monkeypatch):
    manager = release.ReleaseManager(tmp_path)
    monkeypatch.setattr(manager, "checked", lambda release_id: {"sha256": "a" * 64})
    state = manager.request("candidate")
    proposal = state["confirmation"]
    assert proposal["release_id"] == "candidate" and proposal["sha256"] == "a" * 64
    progress = release.read_release_progress(manager.store)
    assert progress["phase"] == "awaiting_confirmation"
    assert progress["operation_id"] == proposal["request_id"] and progress["owner_pid"] is None
    restarted = release.ReleaseManager(tmp_path)
    assert restarted.recover()["confirmation"] == proposal
    assert not restarted.apply_pending()
    assert restarted.backend is None and restarted.ui is None
    assert not manager.owned("enabled").exists()
    monkeypatch.setattr(release, "_process_identity", lambda pid: None)
    assert release.read_release_progress(manager.store)["phase"] == "awaiting_confirmation"
    state["confirmation"]["request_id"] = "b" * 32
    manager._save(state)
    assert release.read_release_progress(manager.store)["phase"] == "interrupted"


def test_confirmation_rejects_replaced_proposal_and_changed_fingerprint(tmp_path, monkeypatch):
    manager = release.ReleaseManager(tmp_path)
    seal = {"sha256": "a" * 64}
    monkeypatch.setattr(manager, "checked", lambda release_id: dict(seal))
    old = manager.request("candidate")["confirmation"]
    new = manager.request("candidate")["confirmation"]
    assert old["request_id"] != new["request_id"]
    before = (manager.store / "progress.json").read_bytes()
    with pytest.raises(ValueError, match="stale"):
        manager.confirm(old["request_id"])
    assert (manager.store / "progress.json").read_bytes() == before
    assert manager.state()["confirmation"] == new and manager.state()["pending"] is None
    seal["sha256"] = "b" * 64  # Even a new valid verification receipt needs a new proposal.
    with pytest.raises(ValueError, match="changed since"):
        manager.confirm(new["request_id"])
    assert manager.state()["pending"] is None
    assert release.read_release_progress(manager.store)["phase"] == "failed"


def test_confirmation_replay_is_exact_and_new_proposals_cannot_replace_pending(tmp_path, monkeypatch):
    manager = release.ReleaseManager(tmp_path)
    monkeypatch.setattr(manager, "checked", lambda release_id: {"sha256": "a" * 64})
    proposal = manager.request("candidate")["confirmation"]
    approved = manager.confirm(proposal["request_id"])
    assert approved["confirmation"] is None and approved["pending"] == "candidate"
    assert approved["last_confirmation"] == {key: proposal[key] for key in ("request_id", "release_id", "sha256")}
    journal = (manager.store / "progress.json").read_bytes()
    assert manager.confirm(proposal["request_id"]) == approved
    assert (manager.store / "progress.json").read_bytes() == journal
    with pytest.raises(ValueError, match="still pending"):
        manager.request("next")
    assert manager.state() == approved
    approved.update(active="candidate", pending=None)
    manager._save(approved)
    assert manager.confirm(proposal["request_id"]) == approved
    newer = manager.request("next")["confirmation"]
    with pytest.raises(ValueError, match="stale"):
        manager.confirm(proposal["request_id"])
    assert manager.state()["confirmation"] == newer and manager.state()["pending"] is None


@pytest.mark.parametrize("accepted", [None, {"request_id": "a" * 32, "release_id": "candidate", "sha256": "old"}])
def test_unapproved_or_changed_pending_never_reaches_drain(tmp_path, monkeypatch, accepted):
    manager = release.ReleaseManager(tmp_path)
    monkeypatch.setattr(manager, "checked", lambda release_id: {"sha256": "a" * 64})
    manager._save({"active": "old", "pending": "candidate", "last_confirmation": accepted})
    monkeypatch.setattr(manager, "_http", lambda *args: pytest.fail("Unapproved release reached drain"))
    assert not manager.apply_pending()
    assert manager.state()["active"] == "old" and manager.state()["pending"] is None
    assert release.read_release_progress(manager.store)["phase"] == "failed"


@pytest.mark.parametrize("request_id", ["../outside", "b" * 31, "B" * 32, None, "новая версия"])
def test_confirmation_nonce_rejects_unbounded_or_invalid_identifiers(tmp_path, request_id):
    with pytest.raises(ValueError, match="Invalid confirmation"):
        release.ReleaseManager(tmp_path).confirm(request_id)


def test_verify_publishes_real_steps_and_completion_waits_for_admission(tmp_path, monkeypatch):
    manager = FixtureManager(tmp_path, port=release._free_port(), startup_timeout=5)
    _candidate(manager, "a")
    events = []
    command = manager._command

    def observed_command(*args, **kwargs):
        assert kwargs.get("timeout") is None  # Build/check duration has no hour limit.
        events.append(release.read_release_progress(manager.store))
        return command(*args, **kwargs)

    monkeypatch.setattr(manager, "_command", observed_command)
    manager.verify("a")
    assert events[0]["phase"] == "checking"
    assert events[0]["step"] == {"index": 1, "total": 3, "label": "Проверка кандидата"}
    assert release.read_release_progress(manager.store)["phase"] == "verified"
    original_activate = manager._activate

    def observed_activation(release_id):
        assert release.read_release_progress(manager.store)["phase"] == "switching"
        assert release.read_release_progress(manager.store)["step"]["index"] == 4
        original_activate(release_id)
        assert release.read_release_progress(manager.store)["phase"] == "switching"

    monkeypatch.setattr(manager, "_activate", observed_activation)
    try:
        _request_and_confirm(manager, "a")
        assert manager.apply_pending()
        assert release.read_release_progress(manager.store)["phase"] == "completed"
        manager._stop_ui()
        manager._stop_backend()
        monkeypatch.setattr(manager, "_activate", original_activate)
        (manager.store / "progress.json").write_text("{broken journal", encoding="utf-8")
        manager._launch_active("a")
        assert manager._http()["admitted"] is True
        assert release.read_release_progress(manager.store)["phase"] == "unavailable"
    finally:
        manager._stop_ui()
        manager._stop_backend()


def test_activation_failure_cannot_publish_completed_progress(tmp_path, monkeypatch):
    manager = FixtureManager(tmp_path, port=release._free_port(), startup_timeout=5)
    _candidate(manager, "a")
    manager.verify("a")

    def refuse_activation(release_id):
        raise RuntimeError("fixture admission acknowledgement failed")

    monkeypatch.setattr(manager, "_activate", refuse_activation)
    _request_and_confirm(manager, "a")
    with pytest.raises(RuntimeError, match="admission"):
        manager.apply_pending()
    assert manager.state()["transition"]["phase"] == "admitting"
    progress = release.read_release_progress(manager.store)
    assert progress["phase"] == "failed" and "admission" in progress["error"]
    assert manager.backend is None and manager.ui is None

    monkeypatch.setattr(manager, "_activate", release.ReleaseManager._activate.__get__(manager))
    manager.recover()
    try:
        manager._launch_active("a")
        assert manager.state()["transition"] is None
        assert "error" not in manager.state()
        assert release.read_release_progress(manager.store)["phase"] == "completed"
        assert manager._http()["admitted"] is True
    finally:
        manager._stop_ui()
        manager._stop_backend()


def test_cargo_hard_link_is_detached_before_sealing_without_changing_bytes(tmp_path):
    output = tmp_path / "src-tauri/target/release/elira-desktop.exe"
    output.parent.mkdir(parents=True)
    alias = output.parent / "cargo-output.exe"
    alias.write_bytes(b"compiled desktop bytes")
    os.link(alias, output)
    label = output.relative_to(tmp_path).as_posix()
    before = release._digest([(label, output)])

    release._detach_build_artifact(tmp_path, label)

    assert output.stat().st_nlink == 1
    assert release._digest([(label, output)]) == before
    alias.write_bytes(b"later compiler output")
    assert output.read_bytes() == b"compiled desktop bytes"
    assert not list(output.parent.glob(".elira-desktop-*.tmp"))


def test_detach_build_artifact_preserves_unlinked_output_and_rejects_escape(tmp_path):
    output = tmp_path / "desktop.exe"
    output.write_bytes(b"desktop")
    modified = output.stat().st_mtime_ns
    release._detach_build_artifact(tmp_path, "desktop.exe")
    assert output.stat().st_mtime_ns == modified
    with pytest.raises(ValueError, match="owned directory"):
        release._detach_build_artifact(tmp_path, "../desktop.exe")
