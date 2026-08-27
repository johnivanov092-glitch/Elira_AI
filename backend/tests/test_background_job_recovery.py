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


def test_background_job_accepts_argv_without_shell_reparsing(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        started = _run.start_background_argv_job(
            tmp_path,
            argv=[
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1], flush=True)",
                "SSH_ARGV_A&B|C",
            ],
            display_command="python argv probe",
        )
        try:
            assert started["ok"] is True
            pid = int(started["pid"])
            deadline = time.monotonic() + 5
            result = started
            while time.monotonic() < deadline:
                result = _run.tool_run_server(
                    tmp_path,
                    action="logs",
                    kind="job",
                    pid=pid,
                )
                if result.get("status") != "running":
                    break
                time.sleep(0.05)

            assert result["status"] == "completed"
            assert result["exit_code"] == 0
            assert "SSH_ARGV_A&B|C" in result["text"]
        finally:
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_run_server_stop_cleans_up_managed_remote_windows_process(
    tmp_path: Path,
) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        started = _run.start_background_argv_job(
            tmp_path,
            argv=[
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ],
            display_command="managed remote probe",
            runtime_metadata={
                "remote_cleanup": {
                    "kind": "ssh_windows_process_tree",
                    "host": "media-server",
                    "remote_pid": 7312,
                    "remote_process_started_ticks": 638602560000000000,
                },
            },
        )
        try:
            with mock.patch(
                "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
                return_value={
                    "ok": True,
                    "remote_pid": 7312,
                    "remote_cleanup_status": "stopped",
                    "text": "remote process tree stopped",
                },
            ) as remote_stop:
                result = _run.tool_run_server(
                    tmp_path,
                    action="stop",
                    kind="job",
                    pid=int(started["pid"]),
                )

            remote_stop.assert_called_once_with(
                host="media-server",
                remote_pid=7312,
                remote_started_ticks=638602560000000000,
            )
            assert result["ok"] is True
            assert result["status"] == "cancelled"
            assert result["remote_pid"] == 7312
            assert result["remote_cleanup_status"] == "stopped"
            assert "remote process tree stopped" in result["text"]
        finally:
            _run.stop_all_servers()
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_workflow_stop_attempts_managed_remote_windows_cleanup(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs, _run, _shell

    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        token = _shell.set_current_run_id("remote-cleanup-run")
        try:
            started = _run.start_background_argv_job(
                tmp_path,
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                display_command="workflow remote cleanup probe",
                runtime_metadata={
                    "remote_cleanup": {
                        "kind": "ssh_windows_process_tree",
                        "host": "media-server",
                        "remote_pid": 7312,
                        "remote_process_started_ticks": 638602560000000000,
                    },
                },
            )
        finally:
            _shell.reset_current_run_id(token)
        try:
            with mock.patch(
                "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
                return_value={
                    "ok": True,
                    "remote_pid": 7312,
                    "remote_cleanup_status": "stopped",
                    "text": "remote stopped",
                },
            ) as remote_stop:
                stopped = _run.stop_run_servers("remote-cleanup-run")

            remote_stop.assert_called_once_with(
                host="media-server",
                remote_pid=7312,
                remote_started_ticks=638602560000000000,
            )
            assert stopped == [{
                "pid": int(started["pid"]),
                "port": None,
                "url": None,
                "command": "workflow remote cleanup probe",
                "remote_pid": 7312,
                "remote_cleanup_status": "stopped",
            }]
            journal = json.loads(
                (tmp_path / "background_jobs" / "jobs.json").read_text(
                    encoding="utf-8"
                )
            )
            cleanup = journal["jobs"][started["job_id"]]["runtime_metadata"][
                "remote_cleanup"
            ]
            assert cleanup["remote_cleanup_status"] == "stopped"
        finally:
            _run.stop_all_servers()
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_workflow_stop_local_teardown_survives_cleanup_journal_failure(
    tmp_path: Path,
) -> None:
    from app.application.code_agent.tools import _background_jobs, _run, _shell

    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        token = _shell.set_current_run_id("cleanup-journal-failure")
        try:
            started = _run.start_background_argv_job(
                tmp_path,
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                display_command="cleanup journal failure probe",
                runtime_metadata={
                    "remote_cleanup": {
                        "kind": "ssh_windows_process_tree",
                        "host": "media-server",
                        "remote_pid": 7312,
                        "remote_process_started_ticks": 638602560000000000,
                    },
                },
            )
        finally:
            _shell.reset_current_run_id(token)
        with mock.patch(
            "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
            return_value={
                "ok": True,
                "remote_pid": 7312,
                "remote_cleanup_status": "stopped",
                "text": "remote stopped",
            },
        ), mock.patch.object(
            _run,
            "update_job_runtime_metadata",
            side_effect=OSError("journal unavailable"),
        ):
            stopped = _run.stop_run_servers("cleanup-journal-failure")

        assert stopped[0]["pid"] == int(started["pid"])
        with _run._SERVERS_LOCK:
            assert _run._LIVE_SERVERS[int(started["pid"])].proc.poll() is not None
            _run._LIVE_SERVERS.clear()


def test_background_job_captures_and_persists_remote_pid(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        started = _run.start_background_argv_job(
            tmp_path,
            argv=[
                sys.executable,
                "-c",
                (
                    "import time; "
                    "print('__ELIRA_REMOTE_PID__=7312:638602560000000000', flush=True); "
                    "time.sleep(30)"
                ),
            ],
            display_command="managed remote pid probe",
            runtime_metadata={
                "remote_cleanup": {
                    "kind": "ssh_windows_process_tree",
                    "host": "media-server",
                    "remote_pid_marker": "__ELIRA_REMOTE_PID__=",
                },
            },
        )
        try:
            assert started["ok"] is True
            assert started["remote_pid"] == 7312
            assert started["remote_cleanup_supported"] is True
            tracked = next(
                item
                for item in _run.tracked_background_processes()
                if item["pid"] == started["pid"]
            )
            assert tracked["remote_pid"] == 7312
            logs = _run.tool_run_server(
                tmp_path,
                action="logs",
                kind="job",
                pid=int(started["pid"]),
            )
            assert '"remote_pid": 7312' in logs["text"]
            assert '"remote_pid_pending": false' in logs["text"]
            assert '"remote_process_identity_captured": true' in logs["text"]
            assert '"remote_cleanup_ready": true' in logs["text"]
            assert '"action": "stop"' in logs["text"]

            journal = json.loads(
                (tmp_path / "background_jobs" / "jobs.json").read_text(
                    encoding="utf-8"
                )
            )
            record = journal["jobs"][started["job_id"]]
            assert (
                record["runtime_metadata"]["remote_cleanup"]["remote_pid"]
                == 7312
            )
            assert (
                record["runtime_metadata"]["remote_cleanup"][
                    "remote_process_started_ticks"
                ]
                == 638602560000000000
            )
        finally:
            with mock.patch(
                "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
                return_value={
                    "ok": True,
                    "remote_pid": 7312,
                    "remote_cleanup_status": "stopped",
                    "text": "remote stopped",
                },
            ):
                _run.stop_all_servers()
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_recovered_background_job_preserves_remote_cleanup_metadata(
    tmp_path: Path,
) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        started = _run.start_background_argv_job(
            tmp_path,
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            display_command="recoverable remote probe",
            runtime_metadata={
                "remote_cleanup": {
                    "kind": "ssh_windows_process_tree",
                    "host": "media-server",
                    "remote_pid": 7312,
                    "remote_process_started_ticks": 638602560000000000,
                },
            },
        )
        pid = int(started["pid"])
        with _run._SERVERS_LOCK:
            original = _run._LIVE_SERVERS.pop(pid)
        try:
            recovery = _run.recover_background_jobs()
            # Reuse the live Popen handle for deterministic in-process teardown.
            # Cross-process RecoveredJobProcess cancellation is covered below;
            # this test isolates durable remote-cleanup metadata restoration.
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS[pid].proc = original.proc
            with mock.patch(
                "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
                return_value={
                    "ok": True,
                    "remote_pid": 7312,
                    "remote_cleanup_status": "stopped",
                    "text": "remote stopped after recovery",
                },
            ) as remote_stop:
                stopped = _run.tool_run_server(
                    tmp_path,
                    action="stop",
                    kind="job",
                    pid=pid,
                )

            assert recovery["running"] == 1
            assert stopped["ok"] is True, stopped
            assert stopped["status"] == "cancelled"
            assert stopped["recovered"] is True
            assert stopped["remote_pid"] == 7312
            remote_stop.assert_called_once_with(
                host="media-server",
                remote_pid=7312,
                remote_started_ticks=638602560000000000,
            )
        finally:
            if original.proc.poll() is None:
                _run._kill_proc_tree(original.proc)
                original.proc.wait(timeout=5)
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_remote_cleanup_failure_keeps_background_job_running_and_tracked(
    tmp_path: Path,
) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        started = _run.start_background_argv_job(
            tmp_path,
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            display_command="remote cleanup retry probe",
            runtime_metadata={
                "remote_cleanup": {
                    "kind": "ssh_windows_process_tree",
                    "host": "media-server",
                    "remote_pid": 7312,
                    "remote_process_started_ticks": 638602560000000000,
                },
            },
        )
        pid = int(started["pid"])
        try:
            with mock.patch(
                "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
                return_value={
                    "ok": False,
                    "remote_pid": 7312,
                    "remote_cleanup_status": "failed",
                    "text": "remote process tree is still running",
                },
            ):
                result = _run.tool_run_server(
                    tmp_path,
                    action="stop",
                    kind="job",
                    pid=pid,
                )

            assert result["ok"] is False
            assert result["status"] == "running"
            assert result["remote_cleanup_status"] == "failed"
            with _run._SERVERS_LOCK:
                assert _run._LIVE_SERVERS[pid].proc.poll() is None
        finally:
            with mock.patch(
                "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
                return_value={
                    "ok": True,
                    "remote_pid": 7312,
                    "remote_cleanup_status": "stopped",
                    "text": "remote stopped",
                },
            ):
                _run.stop_all_servers()
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_failed_local_ssh_job_can_still_clean_verified_remote_process(
    tmp_path: Path,
) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    started_ticks = 638602560000000000
    with mock.patch.object(
        _background_jobs,
        "_state_dir",
        return_value=tmp_path / "background_jobs",
    ):
        started = _run.start_background_argv_job(
            tmp_path,
            argv=[
                sys.executable,
                "-c",
                "import time; time.sleep(0.2); raise SystemExit(7)",
            ],
            display_command="failed local ssh probe",
            runtime_metadata={
                "remote_cleanup": {
                    "kind": "ssh_windows_process_tree",
                    "host": "media-server",
                    "remote_pid": 7312,
                    "remote_process_started_ticks": started_ticks,
                },
            },
        )
        pid = int(started["pid"])
        deadline = time.monotonic() + 5
        logs = started
        while logs.get("status") == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            logs = _run.tool_run_server(
                tmp_path,
                action="logs",
                kind="job",
                pid=pid,
            )
        assert logs["status"] == "failed"

        with mock.patch(
            "app.application.tool_providers.ssh_provider.stop_remote_windows_process_tree",
            return_value={
                "ok": True,
                "remote_pid": 7312,
                "remote_cleanup_status": "stopped",
                "text": "verified remote tree stopped",
            },
        ) as remote_stop:
            stopped = _run.tool_run_server(
                tmp_path,
                action="stop",
                kind="job",
                pid=pid,
            )

        assert stopped["status"] == "failed"
        assert stopped["remote_cleanup_status"] == "stopped"
        assert "verified remote tree stopped" in stopped["text"]
        remote_stop.assert_called_once_with(
            host="media-server",
            remote_pid=7312,
            remote_started_ticks=started_ticks,
        )
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS.clear()


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
