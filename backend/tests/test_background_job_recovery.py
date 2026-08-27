from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _python_env(data_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["ELIRA_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    return env


def _run_python(source: str, *, data_dir: Path) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=ROOT,
        env=_python_env(data_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_prelaunch_journal_is_durable_and_redacts_command(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs

    state_dir = tmp_path / "background_jobs"
    command = "curl --token super-secret https://example.invalid"
    with mock.patch.object(_background_jobs, "_state_dir", return_value=state_dir):
        prepared = _background_jobs.prepare_job(
            command,
            tmp_path,
            log_path=tmp_path / "job.log",
            run_id="redaction-test",
            started_at=time.time(),
        )
        journal = json.loads((state_dir / "jobs.json").read_text(encoding="utf-8"))
        record = journal["jobs"][prepared["job_id"]]
        spec = json.loads(Path(prepared["spec_path"]).read_text(encoding="utf-8"))

        assert record["status"] == "starting"
        assert record["command"] == "curl --token [REDACTED] https://example.invalid"
        assert "super-secret" not in (state_dir / "jobs.json").read_text(encoding="utf-8")
        assert spec["command"] == command
        _background_jobs.discard_prepared_job(prepared)


def test_corrupt_journal_is_quarantined_before_new_write(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs

    state_dir = tmp_path / "background_jobs"
    state_dir.mkdir()
    (state_dir / "jobs.json").write_text("{not-json", encoding="utf-8")
    with mock.patch.object(_background_jobs, "_state_dir", return_value=state_dir):
        prepared = _background_jobs.prepare_job(
            "echo safe",
            tmp_path,
            log_path=tmp_path / "job.log",
            run_id=None,
            started_at=time.time(),
        )
        current = json.loads((state_dir / "jobs.json").read_text(encoding="utf-8"))
        quarantined = list(state_dir.glob("jobs.corrupt-*.json"))

        assert prepared["job_id"] in current["jobs"]
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == "{not-json"
        _background_jobs.discard_prepared_job(prepared)


def test_tracked_job_api_never_exposes_raw_command_secret(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    command = (
        f'"{sys.executable}" -c "import time; time.sleep(2)" '
        "--token super-secret"
    )
    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        started = _run.tool_run_server(
            tmp_path,
            action="start",
            command=command,
            kind="job",
        )
        try:
            processes = _run.tracked_background_processes()
            current = next(item for item in processes if item["pid"] == started["pid"])
            assert "super-secret" not in str(started)
            assert "super-secret" not in str(current)
            assert "[REDACTED]" in current["command"]
        finally:
            _run.tool_run_server(
                tmp_path,
                action="stop",
                pid=int(started["pid"]),
                kind="job",
            )
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_recovery_prioritizes_live_job_when_pid_was_reused() -> None:
    from app.application.code_agent.tools import _run

    pid = 424_242
    base = {
        "pid": pid,
        "process_identity": "identity",
        "cwd": str(ROOT),
        "log_path": str(ROOT / "job.log"),
        "spec_path": str(ROOT / "job.spec.json"),
        "launch_path": str(ROOT / "job.launch.json"),
        "result_path": str(ROOT / "job.result.json"),
        "run_id": "pid-reuse",
        "kind": "job",
        "exit_code": None,
        "updated_at": time.time(),
        "finished_at": None,
        "error": None,
    }
    terminal = {
        **base,
        "job_id": "old-terminal",
        "command": "old",
        "status": "completed",
        "exit_code": 0,
        "started_at": 1.0,
        "finished_at": 2.0,
    }
    running = {
        **base,
        "job_id": "new-running",
        "command": "new",
        "status": "running",
        "started_at": 3.0,
    }
    with _run._SERVERS_LOCK:
        _run._LIVE_SERVERS.clear()
    try:
        with mock.patch.object(
            _run,
            "reconcile_job_records",
            return_value=(
                [terminal, running],
                {"running": 1, "completed": 1, "failed": 0, "cancelled": 0},
            ),
        ):
            _run.recover_background_jobs()
        with _run._SERVERS_LOCK:
            recovered = _run._LIVE_SERVERS[pid]
        assert recovered.job_id == "new-running"
        assert recovered.status == "running"
    finally:
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS.clear()


def test_job_reconnects_to_logs_and_terminal_state_after_backend_restart(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    payload = project / "job_payload.py"
    payload.write_text(
        "import time\n"
        "print('DURABLE_JOB_STARTED', flush=True)\n"
        "time.sleep(1.0)\n"
        "print('DURABLE_JOB_DONE', flush=True)\n",
        encoding="utf-8",
        newline="\n",
    )
    command = f'"{sys.executable}" "{payload}"'

    started = _run_python(
        f"""
        import json
        from pathlib import Path
        from app.application.code_agent.tools._run import tool_run_server

        result = tool_run_server(
            Path({str(project)!r}),
            action="start",
            command={command!r},
            kind="job",
        )
        print(json.dumps(result))
        """,
        data_dir=data_dir,
    )
    assert started["status"] == "running"
    assert started["job_id"]
    assert started["recovered"] is False
    pid = int(started["pid"])

    recovered = _run_python(
        f"""
        import json
        import time
        from pathlib import Path
        from app.application.code_agent.tools._run import recover_background_jobs, tool_run_server

        recovery = recover_background_jobs()
        first = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        output_deadline = time.monotonic() + 0.6
        while (
            first.get("status") == "running"
            and "DURABLE_JOB_STARTED" not in first.get("text", "")
            and time.monotonic() < output_deadline
        ):
            time.sleep(0.05)
            first = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        deadline = time.monotonic() + 8
        final = first
        while final.get("status") == "running" and time.monotonic() < deadline:
            time.sleep(0.1)
            final = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        print(json.dumps({{"recovery": recovery, "first": first, "final": final}}))
        """,
        data_dir=data_dir,
    )

    assert recovered["recovery"]["running"] == 1
    assert recovered["first"]["status"] == "running"
    assert recovered["first"]["job_id"] == started["job_id"]
    assert recovered["first"]["recovered"] is True
    assert "DURABLE_JOB_STARTED" in recovered["first"]["text"]
    assert recovered["final"]["status"] == "completed"
    assert recovered["final"]["exit_code"] == 0
    assert "DURABLE_JOB_DONE" in recovered["final"]["text"]

    terminal = _run_python(
        f"""
        import json
        from pathlib import Path
        from app.application.code_agent.tools._run import recover_background_jobs, tool_run_server

        recovery = recover_background_jobs()
        result = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        print(json.dumps({{"recovery": recovery, "result": result}}))
        """,
        data_dir=data_dir,
    )
    assert terminal["recovery"]["completed"] == 1
    assert terminal["result"]["status"] == "completed"
    assert terminal["result"]["job_id"] == started["job_id"]
    assert terminal["result"]["recovered"] is True
    assert terminal["result"]["exit_code"] == 0
    assert "DURABLE_JOB_DONE" in terminal["result"]["text"]


def test_failed_job_exit_is_recovered_explicitly(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    payload = project / "failed_job.py"
    payload.write_text(
        "import time\n"
        "print('DURABLE_JOB_FAILING', flush=True)\n"
        "time.sleep(0.4)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
        newline="\n",
    )
    command = f'"{sys.executable}" "{payload}"'
    started = _run_python(
        f"""
        import json
        from pathlib import Path
        from app.application.code_agent.tools._run import tool_run_server

        result = tool_run_server(
            Path({str(project)!r}), action="start", command={command!r}, kind="job"
        )
        print(json.dumps(result))
        """,
        data_dir=data_dir,
    )
    pid = int(started["pid"])
    time_limit = 3.0
    result = _run_python(
        f"""
        import json
        import time
        from pathlib import Path
        from app.application.code_agent.tools._run import recover_background_jobs, tool_run_server

        deadline = time.monotonic() + {time_limit}
        recovery = recover_background_jobs()
        status = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        while status.get("status") == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            status = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        print(json.dumps({{"recovery": recovery, "status": status}}))
        """,
        data_dir=data_dir,
    )

    assert result["status"]["status"] == "failed"
    assert result["status"]["exit_code"] == 7
    assert result["status"]["ok"] is False
    assert "DURABLE_JOB_FAILING" in result["status"]["text"]


def test_recovered_running_job_can_be_cancelled_and_remains_auditable(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    payload = project / "cancelled_job.py"
    payload.write_text(
        "import time\n"
        "print('DURABLE_JOB_WAITING', flush=True)\n"
        "time.sleep(10)\n",
        encoding="utf-8",
        newline="\n",
    )
    command = f'"{sys.executable}" "{payload}"'
    started = _run_python(
        f"""
        import json
        from pathlib import Path
        from app.application.code_agent.tools._run import tool_run_server

        result = tool_run_server(
            Path({str(project)!r}), action="start", command={command!r}, kind="job"
        )
        print(json.dumps(result))
        """,
        data_dir=data_dir,
    )
    pid = int(started["pid"])

    stopped = _run_python(
        f"""
        import json
        import time
        from pathlib import Path
        from app.application.code_agent.tools._run import recover_background_jobs, tool_run_server

        recovery = recover_background_jobs()
        deadline = time.monotonic() + 1
        logs = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        while "DURABLE_JOB_WAITING" not in logs.get("text", "") and time.monotonic() < deadline:
            time.sleep(0.05)
            logs = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        result = tool_run_server(Path({str(project)!r}), action="stop", pid={pid}, kind="job")
        print(json.dumps({{"recovery": recovery, "logs": logs, "result": result}}))
        """,
        data_dir=data_dir,
    )
    assert stopped["recovery"]["running"] == 1
    assert stopped["result"]["ok"] is True
    assert stopped["result"]["status"] == "cancelled"
    assert "DURABLE_JOB_WAITING" in stopped["logs"]["text"]

    terminal = _run_python(
        f"""
        import json
        from pathlib import Path
        from app.application.code_agent.tools._run import recover_background_jobs, tool_run_server

        recovery = recover_background_jobs()
        result = tool_run_server(Path({str(project)!r}), action="logs", pid={pid}, kind="job")
        print(json.dumps({{"recovery": recovery, "result": result}}))
        """,
        data_dir=data_dir,
    )
    assert terminal["recovery"]["cancelled"] == 1
    assert terminal["result"]["status"] == "cancelled"
    assert terminal["result"]["exit_code"] is None
    assert terminal["result"]["ok"] is False
    assert "DURABLE_JOB_WAITING" in terminal["result"]["text"]
