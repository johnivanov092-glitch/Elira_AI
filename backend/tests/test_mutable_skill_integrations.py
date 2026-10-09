"""Fixture acceptance of mutable integrations; no external model or SSH host."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]

def cli(tmp_path, script, action, args):
    payload = tmp_path / "arguments.json"
    payload.write_text(json.dumps(args), encoding="utf-8")
    env = {**os.environ, "ELIRA_DATA_DIR": str(tmp_path / "data"),
           "ELIRA_BACKEND_ROOT": str(ROOT / "backend"),
           "ELIRA_SKILLS_ROOT": str(ROOT / "skills"), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, str(ROOT / "skills" / script), action,
                             "--input", str(payload), "--workspace", str(tmp_path)],
                            env=env, cwd=tmp_path, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.stdout.strip(), result.stderr
    return result.returncode, json.loads(result.stdout)

def test_csv_cli_and_error_exit(tmp_path):
    (tmp_path / "values.csv").write_text("name,value\na,3\nb,4\n", encoding="utf-8")
    code, value = cli(tmp_path, "data-analysis/analyze.py", "csv", {"file_path": "values.csv"})
    assert code == 0 and value["ok"]
    result = json.loads(value["text"].split("\n", 1)[1])
    assert result["shape"] == {"rows": 2, "columns": 2}
    assert result["describe"]["value"]["mean"] == 3.5
    code, value = cli(tmp_path, "data-analysis/analyze.py", "csv", {"file_path": "missing.csv"})
    assert code != 0 and value["error"] == "file_not_found"

def test_cli_registry_and_telegram_log_need_no_network(tmp_path):
    code, value = cli(tmp_path, "linux-admin/admin.py", "hosts", {})
    assert code == 0 and value["ok"]
    code, value = cli(tmp_path, "telegram/telegram.py", "messages", {})
    assert code == 0 and value["ok"]

def test_loader_uses_installed_bytes_and_fails_on_missing_or_escape(tmp_path, monkeypatch):
    from app.core.skill_modules import load_skill_module
    monkeypatch.setenv("ELIRA_SKILLS_ROOT", str(tmp_path))
    skill = tmp_path / "fixture"
    skill.mkdir()
    (skill / "runtime.py").write_text("value = 'mutable'\n", encoding="utf-8")
    assert load_skill_module("fixture", "runtime.py").value == "mutable"
    with pytest.raises(FileNotFoundError):
        load_skill_module("fixture", "missing.py")
    with pytest.raises(ValueError):
        load_skill_module("fixture", "../escape.py")

def test_publication_rejects_qa_for_other_bytes(tmp_path, monkeypatch):
    from app.core import config
    from app.application.code_agent.tools import _resources
    monkeypatch.setattr(config, "GENERATED_DIR", tmp_path / "downloads")
    source = tmp_path / "artifact.pdf"
    source.write_bytes(b"%PDF-fixture")
    monkeypatch.setattr(_resources, "validate_document", lambda *a, **k: {
        "status": "passed", "sha256": hashlib.sha256(b"other bytes").hexdigest()})
    result = _resources.tool_resource_publish(tmp_path, project_path=source.name)
    assert not result["ok"] and result["error"] == "document_validation_unverified"
    assert result["document_qa"]["issues"][0]["code"] == "qa_identity_mismatch"
    assert not (tmp_path / "downloads").exists()

def test_kernel_separates_trusted_context_and_cleans_up(tmp_path):
    from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
    from app.application.agent_kernel.runtime_context import current_runtime_context
    observed = []
    def dispatch(name, args):
        observed.append((dict(args), dict(current_runtime_context())))
        return {"ok": True, "text": "fixture"}
    request = ToolExecutionRequest("run", "agent", "scope", "read_file",
        {"path": "x", "_runtime_resource_id": "forged"}, "test", permission_mode="bypass",
        runtime_context={"resource_id": "trusted"})
    assert execute_tool(request, dispatch).status == "ok"
    assert observed == [({"path": "x"}, {"resource_id": "trusted"})]
    assert not current_runtime_context()

def test_child_job_is_adopted_only_for_its_run_and_stops(tmp_path, monkeypatch):
    from app.application.code_agent.tools import _run, _shell
    from app.application.code_agent.skill_result import read_skill_job
    script = tmp_path / "start_fixture.py"
    script.write_text(
        "import sys, json\n"
        + "sys.path.insert(0, " + repr(str(ROOT / "skills/_shared")) + ")\n"
        + "from cli import bootstrap\nbootstrap()\n"
        + "from app.application.code_agent.tools._run import start_background_argv_job\n"
        + "from pathlib import Path\n"
        + "print(json.dumps(start_background_argv_job(Path.cwd(), argv=[sys.executable, '-c', "
          "'import time; time.sleep(60)'], display_command='fixture child')))\n",
        encoding="utf-8")
    token = _shell.set_current_run_id("skill-job-fixture")
    pid = None
    try:
        result = _run.tool_run_bash(tmp_path, command=subprocess.list2cmdline([sys.executable, str(script)]))
        payload = json.loads(result["text"].split("STDOUT:\n", 1)[1].split("\nSTDERR:", 1)[0])
        pid = payload["pid"]
        assert result["ok"] and result["kind"] == "job" and result["backgrounded"]
        assert read_skill_job(json.dumps(payload), tmp_path, "other-run") == {}
        assert read_skill_job(json.dumps(payload), tmp_path / "other", "skill-job-fixture") == {}
        stopped = _run.tool_run_server(tmp_path, action="stop", kind="job", pid=pid)
        assert stopped["ok"]
    finally:
        if pid:
            _run.tool_run_server(tmp_path, action="stop", kind="job", pid=pid)
        _shell.reset_current_run_id(token)

def test_vision_cli_uses_configured_transport(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            payload = json.dumps({"choices": [{"message": {"content": "Fixture image description"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("VISION_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    (tmp_path / "fixture.png").write_bytes(b"image fixture")
    try:
        code, value = cli(tmp_path, "vision/vision.py", "describe", {"path": "fixture.png", "prompt": "fixture"})
        assert code == 0 and value["ok"] and "Fixture image description" in value["text"]
        assert len(seen) == 1 and seen[0][0] == "/v1/chat/completions"
        assert any(item.get("text") == "fixture" for item in seen[0][1]["messages"][-1]["content"])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
