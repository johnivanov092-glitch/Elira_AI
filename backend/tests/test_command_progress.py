from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.command_progress import CommandProgress, command_digest


def _job(job_id: str, *, status: str = "completed", content: str = "rows=1416", **changes) -> dict:
    return {
        "action": "logs", "kind": "job", "job_id": job_id,
        "status": status, "exit_code": None if status == "running" else 0,
        "command_sha256": command_digest("python transform.py"),
        "cwd": "D:/work", "output_sha256": hashlib.sha256(content.encode()).hexdigest(),
        **changes,
    }


def test_repeated_completed_attempts_ignore_pid_wrappers_and_survive_resume() -> None:
    tracker = CommandProgress()
    first = _job("a", pid=123, text="pid=123 age=1s rows=1416")
    assert tracker.observe("run_server", {}, first, epoch=7) is None
    tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    # Polling the terminal job is still observation, including after Resume.
    assert tracker.observe("run_server", {}, first, epoch=7) is None
    hint = tracker.observe("run_server", {}, _job("b", pid=789, text="pid=789 age=2s rows=1416"), epoch=7)
    assert hint is not None and "exit=0" in hint
    assert "2 отдельных запуска" in hint
    # The same diagnostic is not injected on every subsequent tool call.
    assert tracker.observe("run_server", {}, _job("c"), epoch=7) is None
    assert tracker.observe("run_server", {}, first, epoch=7) is None


def test_live_polling_new_output_and_terminal_transition_are_not_retries() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a", status="running")) is None
    assert tracker.observe("run_server", {}, _job("a", status="running")) is None
    assert tracker.observe("run_server", {}, _job("a", status="running", content="rows=354")) is None
    assert tracker.observe("run_server", {}, _job("a", content="rows=354")) is None
    assert tracker.observe("run_server", {}, _job("a", content="rows=354")) is None
    # New actual output is information, even if the command is unchanged.
    assert tracker.observe("run_server", {}, _job("b", content="rows=355")) is None
    assert tracker.observe("run_server", {}, _job("c", content="rows=355")) is not None


def test_active_recovery_context_survives_resume_until_result_or_epoch_changes() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a"), epoch=7) is None
    hint = tracker.observe("run_server", {}, _job("b"), epoch=7)
    assert hint and tracker.context() == hint
    tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    assert tracker.context() == hint
    assert tracker.observe("run_server", {}, _job("b"), epoch=7) is None
    assert tracker.observe("run_server", {}, _job("c"), epoch=7) is None
    assert tracker.context() == hint
    assert tracker.observe("run_server", {}, _job("d", content="rows=270"), epoch=7) is None
    assert tracker.context() == ""
    assert tracker.observe("run_server", {}, _job("e", content="rows=270"), epoch=7)
    assert tracker.context()
    # Epoch synchronization on a non-command observation also retires advice.
    assert tracker.observe("write_file", {}, {}, epoch=8) is None
    assert tracker.context() == ""
    assert CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot()))).context() == ""


def test_input_code_epoch_and_working_directory_separate_attempts() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a"), epoch="input-v1") is None
    assert tracker.observe("run_server", {}, _job("b"), epoch="input-v2") is None
    assert tracker.observe("run_server", {}, _job("c", cwd="D:/other"), epoch="input-v2") is None
    # A job started before a mutation cannot prove the new input/code version.
    assert tracker.observe("run_server", {}, _job("old", status="running"), epoch="input-v2") is None
    assert tracker.observe("run_server", {}, _job("old"), epoch="input-v3") is None
    assert tracker.observe("run_server", {}, _job("new"), epoch="input-v3") is None
    assert tracker.observe("run_server", {}, _job("again"), epoch="input-v3") is not None


def test_commands_business_numbers_and_terminal_states_remain_distinct() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a", status="failed", exit_code=2)) is None
    assert tracker.observe("run_server", {}, _job("b")) is None
    assert tracker.observe("run_server", {}, _job("c", command_sha256=command_digest("python transform.py --week 2"))) is None
    assert tracker.observe("run_server", {}, _job("d", content="rows=1417")) is None
    assert command_digest("", argv=["python", "-c", "print(1)"]) != command_digest("", argv=["python", "-c", "print(2)"])


def test_incomplete_or_rejected_receipts_are_not_inferred_from_text() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a"), execution_status="blocked") is None
    incomplete = _job("a", output_sha256=None, text="False ERROR exit=0")
    assert tracker.observe("run_server", {}, incomplete) is None
    # A later poll can provide a sealed log after the writer finishes flushing.
    assert tracker.observe("run_server", {}, _job("a")) is None
    assert tracker.observe("run_server", {}, _job("b")) is not None
    restored = CommandProgress.from_snapshot({"version": 1, "results": ["invalid"], "attempts": False})
    assert restored.observe("run_server", {}, _job("a")) is None


def test_sandbox_checks_structured_outputs_without_parsing_false() -> None:
    tracker = CommandProgress()
    arguments = {"code": "print(False)"}
    output = {"ok": True, "exit_code": 0, "stdout": "False\n", "stderr": ""}
    assert tracker.observe("sandbox_run", arguments, output, cwd="D:/work") is None
    hint = tracker.observe("sandbox_run", arguments, output, cwd="D:/work")
    assert hint is not None and "exit=0" in hint


def test_unchanged_commands_require_new_diagnosis_and_bound_rechecks_across_resume() -> None:
    tracker = CommandProgress()
    args = {"command": "find evidence"}
    output = {"exit_code": 0, "stdout": "", "stderr": ""}
    for _ in range(3):
        assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
        tracker.observe("run_bash", args, output, cwd="D:/work")
    recovery = tracker.before_dispatch("run_bash", args, cwd="D:/work")
    assert recovery["status"] == "recovery_required"
    assert recovery["observed_attempts"] == 3
    assert recovery["business_outcome"] == "not_assessed"
    tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    for diagnostic in ("first observation", "external state changed"):
        tracker.observe("read_file", {"path": "input.txt"}, {"ok": True, "text": diagnostic})
        assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
        tracker.observe("run_bash", args, output, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "blocked"
    # Actual input-version change authorizes work again, including after Resume.
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work", epoch=1) is None


def test_replayed_diagnostics_do_not_unlock_commands_but_changed_results_do() -> None:
    tracker = CommandProgress()
    args = {"command": "observe service"}
    output = {"exit_code": 0, "stdout": "waiting", "stderr": ""}
    for _ in range(3):
        tracker.observe("run_bash", args, output, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "recovery_required"
    tracker.observe("read_file", {"path": "service.log"}, {"text": "waiting"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
    tracker.observe("run_bash", args, output, cwd="D:/work")
    tracker.observe("read_file", {"path": "service.log"}, {"text": "waiting"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "recovery_required"
    # Model-written strategy claims and failed diagnostic calls are not evidence.
    tracker.observe("runtime_control", {"operation": "task_decide"}, {"ok": True, "text": "new plan"})
    tracker.observe("read_file", {"path": "service.log"}, {"ok": False, "text": "missing"})
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "blocked"
    tracker.observe("read_file", {"path": "service.log"}, {"text": "ready"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
    tracker.observe("run_bash", args, {**output, "stdout": "ready"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
    assert tracker.context() == ""


def test_recovery_never_blocks_polling_an_existing_job_or_another_directory() -> None:
    tracker = CommandProgress()
    for identity in ("a", "b", "c"):
        tracker.observe("run_server", {}, _job(identity))
    args = {"action": "start", "command": "python transform.py"}
    assert tracker.before_dispatch("run_server", args, cwd="D:/work")["status"] == "recovery_required"
    for _ in range(10):
        assert tracker.before_dispatch("run_server", {"action": "logs", "pid": 123}, cwd="D:/work") is None
        tracker.observe("run_server", {}, _job("a"))
    assert tracker.before_dispatch("run_server", args, cwd="D:/other") is None


def test_kernel_failed_process_repeats_but_rejected_receipts_do_not_count(tmp_path: Path) -> None:
    from app.application.agent_kernel import executor
    from app.application.code_agent.tools import _run

    command = f'"{sys.executable}" -c "import sys; print(1416); sys.exit(2)"'
    request = executor.ToolExecutionRequest(
        run_id="command-progress-failure", agent_id="code_agent", project_scope_id="",
        tool_name="run_bash", args={"command": command}, source="code_agent",
        permission_mode="bypass",
    )
    tracker = CommandProgress()
    with mock.patch("app.application.tool_registry.runtime.get_tool", return_value=None), \
            mock.patch.object(executor, "_emit_executed"):
        first = executor.execute_tool(request, lambda _name, args: _run.tool_run_bash(tmp_path, **args))
        assert first.status == "error" and first.output["exit_code"] == 2
        for status in ("rejected", "waiting_approval", "cancelled"):
            assert tracker.observe("run_bash", request.args, first.output, execution_status=status) is None
        assert tracker.snapshot()["results"] == {}
        assert tracker.observe("run_bash", request.args, first.output, execution_status=first.status) is None

        second = executor.execute_tool(request, lambda _name, args: _run.tool_run_bash(tmp_path, **args))
        hint = tracker.observe("run_bash", request.args, second.output, execution_status=second.status)
        assert hint is not None and "exit=2" in hint and "2 отдельных запуска" in hint


def test_real_command_and_durable_background_job_produce_stable_observations(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    command = f'"{sys.executable}" -c "print(1416)"'
    direct = _run.tool_run_bash(tmp_path, command=command)
    assert direct["exit_code"] == 0
    assert direct["cwd"] == str(tmp_path.resolve())
    assert direct["command_sha256"] == command_digest(command)
    tracker = CommandProgress()
    assert tracker.observe("run_bash", {"command": command}, direct) is None
    assert tracker.observe("run_bash", {"command": command}, _run.tool_run_bash(tmp_path, command=command)) is not None

    argv = [sys.executable, "-c", "print(1416)"]
    with mock.patch.object(_background_jobs, "_state_dir", return_value=tmp_path / "jobs"):
        results = []
        for index in range(2):
            started = _run.start_background_argv_job(tmp_path, argv=argv, display_command="python probe")
            assert started["command_sha256"] == command_digest("python probe", argv=argv)
            result = started
            deadline = time.monotonic() + 10
            while result["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.05)
                result = _run.tool_run_server(tmp_path, action="logs", kind="job", pid=started["pid"])
            assert result["status"] == "completed", result
            assert result["output_sha256"] == hashlib.sha256(Path(result["log_path"]).read_bytes()).hexdigest()
            results.append(result)
        assert results[0]["job_id"] != results[1]["job_id"]
        assert results[0]["command_sha256"] == results[1]["command_sha256"]
        assert results[0]["output_sha256"] == results[1]["output_sha256"]
        assert tracker.observe("run_server", {}, results[0]) is None
        assert tracker.observe("run_server", {}, results[1]) is not None
        # Recovered handle obtains command/cwd identity from the existing journal.
        with _run._SERVERS_LOCK:
            for result in results:
                _run._LIVE_SERVERS.pop(result["pid"], None)
        _run.recover_background_jobs()
        recovered = _run.tool_run_server(tmp_path, action="logs", kind="job", pid=results[1]["pid"])
        assert recovered["command_sha256"] == results[1]["command_sha256"]
        assert recovered["cwd"] == str(tmp_path.resolve())
        with _run._SERVERS_LOCK:
            for result in results:
                _run._LIVE_SERVERS.pop(result["pid"], None)
