"""Installed diagnostic fixture for EliraFoundationProof, never production.

The release manager, secure publisher and Foundation recovery loop are real.
Only application source/build commands are small offline fixtures. The public
operation has no command/path arguments. All user files and SQLite operations
are executed by the authenticated WindowsProcessHost.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parent))

from elira_release import ReleaseLayout, ReleaseManager, _lock, _read_json, _write_json
from foundation_service import Foundation
from foundation_storage import FoundationStorage
from foundation_windows import current_user_token, service_pid


SERVICE = "EliraFoundationProof"
PORT = 18585
DATABASES = ("state.db", "facts.sqlite", "jobs.sqlite3")
BACKEND = r'''
import json, os, sqlite3, subprocess, sys, threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import release_fixture
data = Path(os.environ['ELIRA_DATA_DIR'])
data.mkdir(parents=True, exist_ok=True)
rid = os.environ['ELIRA_RELEASE_ID']
with closing(sqlite3.connect(data / 'state.db')) as db, db:
    db.execute('CREATE TABLE IF NOT EXISTS runtime(value TEXT)')
    db.execute('DELETE FROM runtime')
    db.execute('INSERT INTO runtime VALUES(?)', (rid,))
if (data / 'fail-release').exists() and (data / 'fail-release').read_text() == rid:
    for name in ('state.db', 'facts.sqlite', 'jobs.sqlite3'):
        with closing(sqlite3.connect(data / name)) as db, db:
            db.execute("UPDATE preserved SET value='partial migration'")
    sys.exit(3)
identity = release_fixture.identity()
class Handler(BaseHTTPRequestHandler):
    admitted = False
    def log_message(self, *args): pass
    def respond(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        self.respond({'service':'elira-ai-api','release_id':rid,
            'instance_id':os.environ['ELIRA_RELEASE_INSTANCE'], 'draining':not self.admitted,
            'admitted':self.admitted, 'fixture_identity':identity,
            'active_agent_runs':int((data/'busy').exists())})
    def do_POST(self):
        if self.headers.get('x-elira-release-token') != os.environ['ELIRA_RELEASE_TOKEN']:
            self.respond({'ok':False},403); return
        action = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['action']
        if action == 'fixture_start_job':
            with (data/'durable-job.log').open('xb') as log:
                job = subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('job.py'))],
                    stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True,
                    creationflags=0x01000000|0x00000008|0x00000200|0x08000000)
            self.respond({'ok':True,'pid':job.pid}); return
        if action in {'activate','resume'}: Handler.admitted=True
        if action == 'drain': Handler.admitted=False
        self.respond({'ok':True,'idle':not(data/'busy').exists()})
        if action == 'shutdown': threading.Thread(target=server.shutdown).start()
server = HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)
server.serve_forever()
'''
PACKAGE = r'''
import json, os, sys
from pathlib import Path
def identity():
    import ctypes
    from ctypes import wintypes as w
    host = Path(os.environ['ELIRA_FOUNDATION_CLIENT']).parent
    sys.path.insert(0,str(host))
    import foundation_windows as f
    handle=w.HANDLE()
    f._check(f._a.OpenProcessToken(f._k.GetCurrentProcess(),8,ctypes.byref(handle)))
    try: token=f.token_info(handle.value)
    finally: f._k.CloseHandle(handle)
    return {'pid':os.getpid(),'executable':sys.executable,'prefix':sys.prefix,
            'base_prefix':sys.base_prefix,'module':__file__,'token':token}
def main():
    print(json.dumps(identity(),ensure_ascii=True))
'''
DESKTOP = "import release_fixture,time\nprint(release_fixture.identity(),flush=True)\ntime.sleep(600)\n"
JOB = "import json,release_fixture,time\nprint(json.dumps(release_fixture.identity()),flush=True)\nfor i in range(120):\n    print('DURABLE:'+str(i),flush=True)\n    time.sleep(0.5)\n"


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, timeout=90,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _typed(value):
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, float):
        return ["real", value.hex()]
    return ["integer" if isinstance(value, int) else "text", value]


def _rows(data):
    result = {}
    for name in DATABASES:
        path = data / name
        if not path.exists():
            result[name] = None
            continue
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            tables = {}
            for _, table, _, _ in schema:
                if not any(row[0] == "table" and row[1] == table for row in schema):
                    continue
                quoted = '"' + table.replace('"', '""') + '"'
                values = [[_typed(v) for v in row] for row in db.execute("SELECT * FROM " + quoted)]
                tables[table] = sorted(values, key=lambda row: json.dumps(row, sort_keys=True))
            result[name] = {"schema": schema, "tables": tables}
    return result


def _worker(action, root, data, base_python):
    root, data = root.resolve(), data.resolve()
    if action == "seed":
        _require(not root.exists(), "Fixture source already exists")
        root.mkdir(parents=True)
        for directory in ("backend", "frontend/dist", "frontend/node_modules", "node_modules"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        for directory in ("frontend/dist", "frontend/node_modules", "node_modules"):
            _write(root / directory / "fixture.txt", "offline fixture\n")
        _write(root / "backend/stub.py", BACKEND)
        _write(root / "backend/job.py", JOB)
        _write(root / "desktop.py", DESKTOP)
        _write(root / "frontend/index.html", "<!doctype html>\n<html lang=\"en\"><meta charset=\"utf-8\"><title>Foundation fixture</title><body>Offline release fixture</body></html>\n")
        _write(root / ".gitignore", "backend/.venv/\nnode_modules/\nfrontend/node_modules/\nfrontend/dist/\n")
        _run([str(base_python), "-m", "venv", "--copies", str(root / "backend/.venv")], root)
        packages = root / "backend/.venv/Lib/site-packages"
        _write(packages / "release_fixture.py", PACKAGE)
        metadata = packages / "release_fixture-1.0.dist-info"
        _write(metadata / "METADATA", "Name: release_fixture\nVersion: 1.0\n")
        _write(metadata / "entry_points.txt", "[console_scripts]\nrelease-fixture = release_fixture:main\n")
        python = root / "backend/.venv/Scripts/python.exe"
        code = "from pip._vendor.distlib.scripts import ScriptMaker; import sys; m=ScriptMaker(None,sys.argv[1]); m.executable=sys.executable; m.variants={''}; m.make('release-fixture=release_fixture:main')"
        _run([str(python), "-I", "-c", code, str(python.parent)], root)
        _run(["git", "init", "--quiet", str(root)], root)
        _run(["git", "add", "."], root)
        _run(["git", "-c", "user.name=Foundation Fixture", "-c", "user.email=fixture.invalid@example.invalid",
              "commit", "--quiet", "-m", "Fixed offline Foundation fixture"], root)
        return {"source": str(root), "base_python": str(base_python), "base_sha256": _hash(base_python)}
    if action == "check":
        import ast
        ast.parse((root / "backend/stub.py").read_text(encoding="utf-8"))
        ast.parse((root / "desktop.py").read_text(encoding="utf-8"))
        _run([str(root / "backend/.venv/Scripts/python.exe"), "-I", "-c",
              "import release_fixture,sqlite3; assert callable(release_fixture.identity)"], root)
        # The tracked static UI is the fixture's offline build input. Git clone
        # intentionally excludes dist, so verification must actually build it.
        html = (root / "frontend/index.html").read_text(encoding="utf-8")
        _require(html.startswith("<!doctype html>\n") and "Offline release fixture" in html,
                 "Invalid fixed frontend source")
        output = root / "frontend/dist/index.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(html)
        return {"source_syntax_and_real_imports": True,
                "offline_frontend_build": str(output), "frontend_sha256": _hash(output)}
    data.mkdir(parents=True, exist_ok=True)
    if action == "seed-db":
        for name in DATABASES:
            with closing(sqlite3.connect(data / name)) as db, db:
                db.execute("CREATE TABLE preserved(key TEXT PRIMARY KEY,value TEXT,raw BLOB,ratio REAL)")
                db.execute("INSERT INTO preserved VALUES('original','safe',?,1.25)", (b"\x00\xff",))
        with closing(sqlite3.connect(data / "state.db")) as db, db:
            db.execute("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT)")
            db.execute("INSERT INTO chats VALUES('before','Original fixture chat')")
    elif action == "post-chat":
        with closing(sqlite3.connect(data / "state.db")) as db, db:
            db.execute("INSERT INTO chats VALUES('after','Created after admission')")
    elif action in {"busy", "fail-on"}:
        _write(data / ("busy" if action == "busy" else "fail-release"), "1" if action == "busy" else "b")
    elif action in {"idle", "fail-off"}:
        (data / ("busy" if action == "idle" else "fail-release")).unlink()
    elif action == "job-state":
        payload = (data / "durable-job.log").read_bytes()
        lines = payload.decode("utf-8").splitlines()
        return {"identity": json.loads(lines[0]), "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(), "last_line": lines[-1]}
    elif action != "snapshot":
        raise ValueError("Unknown fixed fixture action")
    return {"databases": _rows(data)}


class RecordingHost:
    """Observe the real Windows host; no process identity is simulated."""
    def __init__(self, host, observations=None, deadline=None):
        self.host = host
        self.observations = observations if observations is not None else []
        self.deadline = deadline

    def __getattr__(self, key):
        return getattr(self.host, key)

    def clone(self):
        return RecordingHost(self.host.clone(), self.observations, self.deadline)

    def popen(self, args, **kwargs):
        process = self.host.popen(args, **kwargs)
        self.observations.append({"argv": args, "pid": process.pid, "token": process.identity,
                                  "creation_identity": process.creation_identity,
                                  "image": process.image_path,
                                  "command_pid": getattr(process, "command_pid", process.pid)})
        return process

    def run(self, args, **kwargs):
        timeout = kwargs.pop("timeout", None) or 90
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("Fixture execution budget exceeded before command launch")
        with self.popen(args, **kwargs) as process:
            try:
                result = subprocess.CompletedProcess(args, process.wait(timeout))
            except BaseException:
                process.kill(); process.wait(10)
                raise
            result.check_returncode()
            return result


class FixtureManager(ReleaseManager):
    def _verification_commands(self, root):
        return [[str(self.layout.worker_python), "-I", "-S", "-B", str(Path(__file__)),
                 "--worker", "check", "--root", str(root), "--data", str(self.data)]]

    def _find_executable(self, root):
        return "desktop.py"

    def _backend_command(self, release_id):
        return [str(self._python(self.path(release_id))), "-B", str(self.path(release_id) / "backend/stub.py"), str(self.port)]

    def _start_ui(self, release_id, executable):
        env = self._environment(release_id)
        env.pop("ELIRA_RELEASE_TOKEN", None)
        self.ui = self.host.popen([str(self._python(self.path(release_id))), "-B",
                                  str(self.path(release_id) / executable)],
                                 cwd=self.path(release_id), env=env, desktop=False)
        self._save_processes()
        time.sleep(0.2)
        _require(self.ui.poll() is None, "Fixture UI exited during startup")


def lifecycle_proof(client_host):
    """Fixed, bounded test in the installed TEST service; no request parameters."""
    config_path = Path(os.environ["ProgramData"]) / SERVICE / "installation.json"
    original = _read_json(config_path)
    _require(original.get("service_name") == SERVICE and original.get("diagnostic") is True
             and original.get("port") == PORT, "Only installed test service on port 18585 is permitted")
    _require(service_pid(SERVICE) == os.getpid(), "Fixture must run inside the actual TEST service")
    with socket.socket() as probe:
        _require(probe.connect_ex(("127.0.0.1", PORT)) != 0, "Test port already owned; nothing was stopped")
    attempt = "lifecycle-" + uuid.uuid4().hex
    platform = Path(original["platform"]).resolve()
    _require(platform.name == "platform" and platform.parent.name == "foundation-proof",
             "Unexpected installed test platform")
    store = Path(original["store"]) / "lifecycle-proofs" / attempt
    store.mkdir(parents=True, exist_ok=False)
    config = {**original, "store": str(store), "platform": str(platform / attempt),
              "candidates": str(Path(original["candidates"]) / attempt),
              "published": str(Path(original["published"]) / attempt),
              "data": str(Path(original["data"]) / attempt),
              "journals": str(Path(original["journals"]) / attempt)}
    deadline = time.monotonic() + 270
    retained = RecordingHost(client_host.clone(), deadline=deadline)
    foundation = Foundation(config)
    layout = ReleaseLayout(store=store, candidates=Path(config["candidates"]), published=Path(config["published"]),
        data=Path(config["data"]), journals=Path(config["journals"]), config_root=Path(config["platform"]) / "backend",
        worker_root=Path(config["platform"]) / ".runtime/foundation-work", worker_python=Path(config["python"]),
        worker_script=Path(__file__).with_name("elira_release.py"), service_name=SERVICE)
    storage = FoundationStorage(retained, protected_root=store.parent.parent, worker_root=layout.worker_root)
    manager = FixtureManager(Path(config["platform"]), layout=layout, host=retained, storage=storage,
                             port=PORT, startup_timeout=12)
    foundation.host, foundation.manager = retained, manager
    report = {"status": "INCOMPLETE", "attempt": attempt, "service_pid": os.getpid(),
              "started_at": time.time(), "scope": "real protected release mechanism with stub application",
              "full_elira": "NOT_RUN", "native_tauri": "NOT_RUN", "events": [],
              "processes": retained.observations, "roots": config}
    durable_job = None

    def observe(name, value):
        _require(time.monotonic() < deadline, "Fixture 270-second execution budget exceeded")
        report["events"].append({"name": name, "time": time.time(), "value": value})
        _write_json(store / "proof.json", report)
        return value

    def user(action):
        log_path = store / ("user-" + str(len(report["events"])) + "-" + action + ".log")
        base_python = Path(os.environ["ProgramFiles"]) / "Python310/python.exe"
        with log_path.open("xb") as log:
            retained.run([config["python"], "-I", "-S", "-B", str(Path(__file__)), "--worker", action,
                          "--root", config["platform"], "--data", config["data"], "--base-python", str(base_python)],
                         cwd=Path(__file__).parent, env=retained.user_environment(), log=log, timeout=100)
        lines = log_path.read_text(encoding="utf-8").splitlines()
        result = json.loads(lines[-1])
        _require(result.get("ok") is True, "Fixture worker did not provide a successful result")
        report.setdefault("workers", []).append({"action": action, "identity": result["worker_identity"]})
        return observe(action, result["result"])

    def operation(name, release_id=None):
        request = {"version": 1, "operation": name, "request_id": uuid.uuid4().hex}
        if release_id is not None:
            request["confirmation_id" if name == "confirm" else "release_id"] = release_id
        with client_host.clone() as connection_host:
            queued = foundation.dispatch(request, connection_host)
        _require(queued["status"] == "queued", "Operation did not enter real Foundation queue")
        queued_request, owned_host = foundation.queue.get_nowait()
        foundation._operation(queued_request, owned_host)
        result = _read_json(foundation.operations / (request["request_id"] + ".json"))
        observe("operation-" + name, result)
        _require(result.get("status") == "completed", "Foundation operation failed: " + str(result.get("error")))
        return result["result"]

    def tick_until(predicate, seconds=20):
        limit = min(deadline, time.monotonic() + seconds)
        while time.monotonic() < limit:
            foundation.advance_lifecycle()
            if predicate():
                return
            time.sleep(0.2)
        raise TimeoutError("Foundation did not reach the observed lifecycle condition")

    try:
        with _lock(store / "supervisor.lock"):
            user("seed")
            for release_id in ("a", "b"):
                operation("prepare", release_id)
                operation("verify", release_id)
                receipt = manager.checked(release_id)
                with storage.user_access():
                    candidate_fingerprint = manager.fingerprint(manager.candidate_path(release_id))
                observe("published-" + release_id, {"receipt": receipt,
                    "candidate_fingerprint": candidate_fingerprint,
                    "published_fingerprint": manager.fingerprint(manager.path(release_id), executable="desktop.py")})
            launcher = manager._python(manager.path("a")).parent / "release-fixture.exe"
            launcher_log = store / "published-launcher.log"
            with launcher_log.open("xb") as log:
                retained.run([str(launcher)], cwd=manager.path("a"), env=manager._environment("a"), log=log, timeout=20)
            identity = json.loads(launcher_log.read_text(encoding="utf-8").splitlines()[-1])
            _require(Path(identity["executable"]).resolve() == manager._python(manager.path("a")).resolve(),
                     "Console launcher used another Python")
            _require(Path(identity["prefix"]).resolve() == (manager.path("a") / "backend/.venv").resolve()
                     and Path(identity["module"]).resolve().is_relative_to(manager.path("a")),
                     "Published import escaped the sealed environment")
            observe("published-launcher", identity)
            operation("request", "a")
            operation("confirm", manager.state()["confirmation"]["request_id"])
            operation("open")
            tick_until(lambda: manager.state().get("active") == "a" and manager._http().get("admitted") is True)
            observe("active-a", manager._http())
            user("seed-db")
            user("busy")
            pid_before = manager.backend.pid
            operation("request", "b")
            operation("confirm", manager.state()["confirmation"]["request_id"])
            for _ in range(3):
                foundation.advance_lifecycle()
                _require(manager.state().get("active") == "a" and manager.state().get("pending") == "b"
                         and manager.backend.pid == pid_before, "Busy release was forcibly replaced")
                time.sleep(0.2)
            observe("pending-while-busy", {"state": manager.state(), "health": manager._http(), "backend_pid": pid_before})
            user("idle")
            user("fail-on")
            before_failure = user("snapshot")["databases"]
            foundation.advance_lifecycle()
            _require(manager.state().get("active") == "a" and manager.state().get("pending") is None
                     and manager.state().get("error"), "Failed startup did not roll back")
            after_failure = user("snapshot")["databases"]
            _require(before_failure == after_failure, "Failed startup changed fixture SQLite rows/schema")
            observe("failed-startup-rollback", {"state": manager.state(), "all_database_rows_equal": True})
            user("fail-off")
            operation("request", "b")
            operation("confirm", manager.state()["confirmation"]["request_id"])
            tick_until(lambda: manager.state().get("active") == "b" and manager._http().get("admitted") is True)
            observe("active-b", manager._http())
            post_admission = user("post-chat")["databases"]
            job_response = manager._http("fixture_start_job")
            job_identity = retained.process_identity(job_response["pid"])
            _require(job_identity is not None, "Detached durable worker did not start")
            durable_job = {"pid": job_response["pid"], "identity": job_identity}
            time.sleep(1)
            before_job = user("job-state")
            actual_backend_pid = manager._http()["fixture_identity"]["pid"]
            killed = {"pid": actual_backend_pid, "identity": retained.process_identity(actual_backend_pid),
                      "owned_runner_pid": manager.backend.pid}
            _require(killed["identity"] is not None, "Actual backend identity is unavailable")
            retained.terminate_owned(killed["pid"], killed["identity"])
            tick_until(lambda: manager.backend is not None and manager.backend.pid != killed["owned_runner_pid"]
                       and manager.backend.poll() is None and manager._http().get("admitted") is True, seconds=25)
            _require(service_pid(SERVICE) == os.getpid(), "Foundation service changed during child recovery")
            _require(user("snapshot")["databases"] == post_admission, "Child recovery lost fixture data")
            after_job = user("job-state")
            _require(retained.process_identity(durable_job["pid"]) == durable_job["identity"]
                     and before_job["identity"] == after_job["identity"]
                     and after_job["bytes"] > before_job["bytes"],
                     "Durable breakaway worker did not survive backend recovery with progressing output")
            observe("durable-worker-survived", {"process": durable_job, "before": before_job, "after": after_job})
            observe("automatic-child-recovery", {"killed": killed, "replacement_pid": manager.backend.pid,
                                                  "service_pid": os.getpid(), "health": manager._http()})
            operation("rollback")
            operation("confirm", manager.state()["confirmation"]["request_id"])
            tick_until(lambda: manager.state().get("active") == "a" and manager._http().get("admitted") is True)
            final_rows = user("snapshot")["databases"]
            expected = json.loads(json.dumps(post_admission))
            expected["state.db"]["tables"]["runtime"] = [[["text", "a"]]]
            _require(final_rows == expected, "Post-admission rollback lost new chats or changed unrelated data")
            observe("post-admission-code-rollback", {"state": manager.state(), "rows": final_rows})
            for release_id in ("a", "b"):
                manager.checked(release_id)
            operation("close")
            _require(manager.backend is None and manager.ui is None, "Fixture children did not close")
            report["status"] = "PASS_STUB_RELEASE_LIFECYCLE"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    finally:
        if durable_job is not None:
            try:
                retained.terminate_owned(durable_job["pid"], durable_job["identity"])
                report["durable_job_cleanup"] = retained.process_identity(durable_job["pid"]) != durable_job["identity"]
                if not report["durable_job_cleanup"]:
                    report["status"] = "FAIL"
            except Exception as exc:
                report["durable_job_cleanup_error"] = str(exc)
                report["status"] = "FAIL"
        report["cleanup_ok"] = foundation._stop_application()
        report["final_state"] = manager.state()
        report["remaining_backend_pid"] = manager.backend.pid if manager.backend is not None else None
        report["remaining_ui_pid"] = manager.ui.pid if manager.ui is not None else None
        if not report["cleanup_ok"]:
            report["status"] = "FAIL"
        try:
            report["remaining_owned_processes"] = [process for process in retained.observations
                if retained.process_identity(process["pid"]) == process["creation_identity"]]
            if report["remaining_owned_processes"]:
                report["status"] = "FAIL"
        except Exception as exc:
            report["process_cleanup_observation_error"] = str(exc)
            report["status"] = "FAIL"
        retained.close()
        report["finished_at"] = time.time()
        _write_json(store / "proof.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, choices=("seed", "check", "seed-db", "post-chat", "busy", "idle", "fail-on", "fail-off", "snapshot", "job-state"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base-python", type=Path)
    args = parser.parse_args()
    # An accidental direct service invocation must fail before opening user DBs.
    with current_user_token(limited=False) as actual_worker:
        worker_identity = actual_worker.info
    result = _worker(args.worker, args.root, args.data, args.base_python)
    print(json.dumps({"ok": True, "result": result, "worker_identity": worker_identity}, ensure_ascii=True))


if __name__ == "__main__":
    main()
