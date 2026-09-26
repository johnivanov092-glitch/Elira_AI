from __future__ import annotations

import importlib.util
import json
from pathlib import Path
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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
data = Path(os.environ["ELIRA_DATA_DIR"])
data.mkdir(parents=True, exist_ok=True)
rid = os.environ["ELIRA_RELEASE_ID"]
with sqlite3.connect(data / "state.db") as db:
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
        self.ui = subprocess.Popen([sys.executable, str(self.path(release_id) / executable)],
                                   cwd=self.path(release_id), env=self._environment(release_id))
        self._save_processes()


def _candidate(manager, name):
    root = manager.path(name)
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


def _db_value(data):
    with sqlite3.connect(data / "state.db") as db:
        return db.execute("SELECT value FROM state").fetchone()[0]


def test_release_real_process_lifecycle_verification_drain_and_recovery(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ELIRA_DATA_DIR", str(data))
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "journals"))
    manager = FixtureManager(tmp_path, port=release._free_port(), startup_timeout=5)
    a = _candidate(manager, "a")
    b = _candidate(manager, "b")
    try:
        manager.verify("a")
        manager.verify("b")
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

        manager._http("drain")
        manager._stop_ui()
        manager._stop_backend()
        backup = manager.store / "backups" / "interrupted"
        release._snapshot_databases(data, backup)
        state = manager.state()
        state["transition"] = {"from": "a", "to": "b", "phase": "switching", "backup": str(backup)}
        manager._save(state)
        with sqlite3.connect(data / "state.db") as db:
            db.execute("UPDATE state SET value='partial migration'")
        assert manager.recover()["active"] == "a"
        assert _db_value(data) == "a"
        assert manager.recover()["transition"] is None  # Repeated recovery is idempotent.
    finally:
        manager._stop_ui()
        if manager.backend is not None:
            manager._http("drain")
        manager._stop_backend()
