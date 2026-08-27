"""run_server reports the URL it actually bound and tracks process liveness.

Destination authorization no longer depends on a loopback allowlist; tracked
ports remain observational data used to choose the correct verification URL.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

from app.application.code_agent.tools import _background_jobs, _run  # noqa: E402
from app.application.code_agent.tools import _shell  # noqa: E402
from app.application.code_agent.tools._run import _parse_server_url, active_server_ports  # noqa: E402
from app.application.web.ssrf_guard import check_ssrf  # noqa: E402


class DestructiveActionWorkflowPermissionTest(unittest.TestCase):
    """run_bash has no command blocklist after Workflow authorization."""

    def test_destructive_commands_are_forwarded_to_the_process_runner(self):
        commands = (
            'rmdir /s /q "C:\\AgentLabGlobalCanary"',
            "rm -rf /var/tmp/canary",
        )
        for command in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as ws:
                completed = mock.MagicMock()
                completed.returncode = 0
                completed.poll.return_value = 0
                completed.stdout.read.return_value = b""
                completed.stderr.read.return_value = b""
                with mock.patch.object(
                    _run.subprocess,
                    "Popen",
                    return_value=completed,
                ) as runner:
                    out = _run.tool_run_bash(Path(ws), command=command)
                runner.assert_called_once()
                self.assertEqual(out["exit_code"], 0)
                self.assertNotIn("blocked", out["text"].lower())

    def test_empty_command_is_ok_false(self):
        with tempfile.TemporaryDirectory() as ws:
            out = _run.tool_run_bash(Path(ws), command="   ")
        self.assertFalse(out["ok"])


class StopRegistrationRaceTest(unittest.TestCase):
    def tearDown(self):
        with _shell._LIVE_SHELL_LOCK:
            _shell._LIVE_SHELL_PROCS.clear()
            _shell._RUN_CANCEL_CALLBACKS.clear()
            _shell._KILLED_RUN_IDS.clear()

    def test_process_registered_after_stop_is_killed_immediately(self):
        proc = mock.MagicMock()
        proc.poll.return_value = None
        _shell.kill_run_processes("race-run")

        with mock.patch.object(_shell, "_kill_proc_tree") as kill_tree:
            _shell._register_shell_proc("race-run", proc)

        kill_tree.assert_called_once_with(proc)
        with _shell._LIVE_SHELL_LOCK:
            self.assertNotIn("race-run", _shell._LIVE_SHELL_PROCS)

    def test_new_run_registration_can_clear_a_stale_stop_marker(self):
        proc = mock.MagicMock()
        proc.poll.return_value = None
        _shell.kill_run_processes("resumed-run")
        _shell.clear_run_stop_marker("resumed-run")

        with mock.patch.object(_shell, "_kill_proc_tree") as kill_tree:
            _shell._register_shell_proc("resumed-run", proc)

        kill_tree.assert_not_called()
        with _shell._LIVE_SHELL_LOCK:
            self.assertIn(proc, _shell._LIVE_SHELL_PROCS["resumed-run"])

    def test_registered_transport_callback_is_invoked_once_on_stop(self):
        callback = mock.Mock()
        context_token = _shell.set_current_run_id("callback-run")
        try:
            callback_token = _shell.register_run_cancel_callback(callback)
        finally:
            _shell.reset_current_run_id(context_token)

        self.assertEqual(_shell.cancel_run_callbacks("callback-run"), 1)
        callback.assert_called_once_with()
        _shell.unregister_run_cancel_callback(callback_token)

        with _shell._LIVE_SHELL_LOCK:
            self.assertNotIn("callback-run", _shell._RUN_CANCEL_CALLBACKS)

    def test_callback_registered_after_stop_is_invoked_immediately(self):
        callback = mock.Mock()
        _shell.kill_run_processes("late-callback-run")
        context_token = _shell.set_current_run_id("late-callback-run")
        try:
            callback_token = _shell.register_run_cancel_callback(callback)
        finally:
            _shell.reset_current_run_id(context_token)

        self.assertIsNone(callback_token)
        callback.assert_called_once_with()
        with _shell._LIVE_SHELL_LOCK:
            self.assertNotIn("late-callback-run", _shell._RUN_CANCEL_CALLBACKS)


class ParseServerUrlTest(unittest.TestCase):
    def test_prefers_loopback_and_last_after_autoincrement(self):
        log = (
            "Port 5173 is in use, trying another one...\n"
            "  VITE v6.4.3  ready in 320 ms\n"
            "  ➜  Local:   http://localhost:5174/\n"
            "  ➜  Network: http://192.168.88.20:5174/\n"
        )
        self.assertEqual(_parse_server_url(log), ("http://localhost:5174", 5174))

    def test_network_only_falls_back_to_lan(self):
        # No loopback line at all → return what we have (still better than guessing).
        self.assertEqual(
            _parse_server_url("Network: http://192.168.88.20:4321/"),
            ("http://192.168.88.20:4321", 4321),
        )

    def test_normalises_0_0_0_0_to_localhost(self):
        self.assertEqual(_parse_server_url("Local: http://0.0.0.0:3000/"), ("http://localhost:3000", 3000))

    def test_no_url_returns_none(self):
        self.assertIsNone(_parse_server_url("compiling...\nbuilt in 1.2s"))


class _AliveProc:
    def poll(self):
        return None  # still running


class LoopbackVerifierUsesActualPortTest(unittest.TestCase):
    def tearDown(self):
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS.clear()

    def _register(self, *, actual_port: int):
        h = _run._ServerHandle(
            pid=999001, command="npm run dev", proc=_AliveProc(),
            log_path=Path("."), port=actual_port, url=f"http://localhost:{actual_port}",
        )
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS[999001] = h

    def test_autoincremented_actual_port_is_tracked_without_blocking_others(self):
        # Vite bound 5174 after 5173 was taken → the handle carries 5174.
        self._register(actual_port=5174)
        ports = active_server_ports()
        self.assertEqual(ports, {5174})
        # The tracked URL is selected for honest verification.
        self.assertIsNone(check_ssrf("http://localhost:5174/", allow_loopback_ports=ports))
        # Destination validation is shape-only, so another local port is not blocked.
        self.assertIsNone(check_ssrf("http://localhost:5173/", allow_loopback_ports=ports))

    def test_dead_server_drops_from_allowlist(self):
        class _DeadProc:
            def poll(self):
                return 0
        h = _run._ServerHandle(pid=999002, command="x", proc=_DeadProc(),
                               log_path=Path("."), port=5174, url="http://localhost:5174")
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS[999002] = h
        self.assertEqual(active_server_ports(), set())  # reaped → nothing allowed


class RunServerHonestyTest(unittest.TestCase):
    def tearDown(self):
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS.clear()

    def test_server_verdict_helper(self):
        h = _run._ServerHandle(pid=1, command="npm run dev", proc=_AliveProc(),
                               log_path=Path("."), port=3000, url="http://localhost:3000")
        out = _run._server_verdict("running...", h, "list")
        self.assertTrue(out["ok"] and out["verifier"] and out["server_started"])
        self.assertEqual(out["actual_port"], 3000)
        self.assertIn("3000", out["evidence"])
        # no live handle → plain status, NOT a verdict
        plain = _run._server_verdict("nothing", None, "list")
        self.assertTrue(plain["ok"])
        self.assertIsNone(plain.get("verifier"))

    def test_list_of_running_server_is_a_verifier(self):
        h = _run._ServerHandle(pid=18588, command="npm run dev", proc=_AliveProc(),
                               log_path=Path("."), port=3000, url="http://localhost:3000")
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS[18588] = h
        out = _run.tool_run_server(Path("."), action="list")
        self.assertTrue(out.get("verifier"))
        self.assertEqual(out.get("actual_port"), 3000)

    def test_failed_start_is_ok_false_without_verifier(self):
        # A server that dies immediately (e.g. "Port in use") must be ok=False and NOT
        # a verifier — otherwise the loop reads a failed start as success.
        import subprocess

        class _ExitedProc:
            pid = 4242
            returncode = 1

            def poll(self):
                return 1

        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("subprocess.Popen", return_value=_ExitedProc()):
            out = _run.tool_run_server(Path(tmp), action="start", command="npm run dev", port=3000)
        self.assertFalse(out["ok"])
        self.assertIsNone(out.get("verifier"))
        self.assertIn("ERROR", out["text"])

    def test_start_requires_command_is_ok_false(self):
        out = _run.tool_run_server(Path("."), action="start", command="")
        self.assertFalse(out["ok"])

    def test_stop_of_untracked_pid_is_ok_false(self):
        out = _run.tool_run_server(Path("."), action="stop", pid=999_999_999)
        self.assertIs(out.get("ok"), False)
        self.assertIn("no tracked server", out["text"])

    def test_unknown_action_is_ok_false(self):
        out = _run.tool_run_server(Path("."), action="restart")
        self.assertIs(out.get("ok"), False)
        self.assertIn("unknown action", out["text"])


class ServerLifecycleOwnershipTest(unittest.TestCase):
    """R2 Server Lifecycle: the runtime owns what it started. Ownership is tagged from
    the executor's run_id ContextVar at start; run_owned_servers/stop_run_servers see
    ONLY that run's servers; url_is_live_server demands a live process AND a listening
    port — a stale URL never passes the gate."""

    def _sleeper_cmd(self) -> str:
        return f'"{sys.executable}" -c "import time; time.sleep(30)"'

    def test_start_tags_ownership_and_stop_kills_only_own(self):
        from app.application.code_agent.tools._shell import set_current_run_id, reset_current_run_id
        with tempfile.TemporaryDirectory() as tmp:
            token = set_current_run_id("r2-own")
            try:
                out = _run.tool_run_server(Path(tmp), action="start", command=self._sleeper_cmd())
            finally:
                reset_current_run_id(token)
            self.assertTrue(out.get("ok", True), out["text"])
            try:
                owned = _run.run_owned_servers("r2-own")
                self.assertEqual(len(owned), 1)                       # tagged to MY run
                self.assertEqual(_run.run_owned_servers("other-run"), [])   # not to another
                stopped = _run.stop_run_servers("r2-own")
                self.assertEqual(len(stopped), 1)
                self.assertEqual(stopped[0]["pid"], owned[0]["pid"])
                self.assertEqual(_run.run_owned_servers("r2-own"), [])      # gone from registry
                self.assertEqual(_run.stop_run_servers("r2-own"), [])       # idempotent
            finally:
                _run.stop_run_servers("r2-own")   # belt-and-braces cleanup

    def test_stop_action_keeps_handle_when_kill_fails(self):
        # John's P1a: a failed kill must NOT report "Stopped" and must NOT drop the
        # handle — the process would live on untracked and unstoppable.
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        handle = _run._ServerHandle(proc.pid, "sleeper", proc, Path("nolog.log"), None,
                                    run_id="p1a-stop")
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS[proc.pid] = handle
        try:
            with mock.patch.object(_run, "_kill_proc_tree", lambda p: None):  # kill "fails"
                out = _run.tool_run_server(Path("."), action="stop", pid=proc.pid)
            self.assertFalse(out.get("ok", True))
            self.assertIn("ЖИВ", out["text"])                        # honest, not "Stopped"
            with _run._SERVERS_LOCK:
                self.assertIn(proc.pid, _run._LIVE_SERVERS)          # still tracked
            out2 = _run.tool_run_server(Path("."), action="stop", pid=proc.pid)  # real kill
            self.assertIn("Stopped", out2["text"])
            with _run._SERVERS_LOCK:
                self.assertNotIn(proc.pid, _run._LIVE_SERVERS)
        finally:
            proc.kill()
            proc.wait(timeout=5)
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.pop(proc.pid, None)

    def test_stop_all_keeps_unkillable_tracked(self):
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        handle = _run._ServerHandle(proc.pid, "sleeper", proc, Path("nolog.log"), None,
                                    run_id="p1a-stopall")
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS[proc.pid] = handle
        try:
            with mock.patch.object(_run, "_kill_proc_tree", lambda p: None):
                self.assertEqual(_run.stop_all_servers(), 0)         # nothing actually died
            with _run._SERVERS_LOCK:
                self.assertIn(proc.pid, _run._LIVE_SERVERS)          # still tracked
            self.assertEqual(_run.stop_all_servers(), 1)             # real kill works
        finally:
            proc.kill()
            proc.wait(timeout=5)
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.pop(proc.pid, None)

    def test_url_is_live_server_requires_proc_and_listening_port(self):
        import socket
        import subprocess
        self.assertFalse(_run.url_is_live_server("http://localhost:59999"))  # unknown → dead
        self.assertFalse(_run.url_is_live_server("not a url"))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        handle = _run._ServerHandle(proc.pid, "sleeper", proc,
                                    Path("nolog.log"), port,
                                    url=f"http://localhost:{port}", run_id="r2-live")
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS[proc.pid] = handle
        try:
            self.assertTrue(_run.url_is_live_server(f"http://localhost:{port}"))
            sock.close()                                              # port stops listening…
            self.assertFalse(_run.url_is_live_server(f"http://localhost:{port}"))
        finally:
            try:
                sock.close()
            except OSError:
                pass
            proc.kill()
            proc.wait(timeout=5)
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.pop(proc.pid, None)
        self.assertFalse(_run.url_is_live_server(f"http://localhost:{port}"))  # dead proc → dead


class BackgroundJobLifecycleTest(unittest.TestCase):
    def tearDown(self):
        _run.stop_all_servers()
        with _run._SERVERS_LOCK:
            _run._LIVE_SERVERS.clear()

    def test_windows_job_worker_detaches_from_backend_console_and_job(self):
        if _run.os.name != "nt":
            self.skipTest("Windows process flags only")
        flags = _run._job_process_group_kwargs()["creationflags"]
        self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertTrue(flags & subprocess.DETACHED_PROCESS)
        self.assertTrue(flags & _run._WINDOWS_CREATE_BREAKAWAY_FROM_JOB)

    def test_completed_job_keeps_status_and_final_output_until_cleanup(self):
        command = (
            f'"{sys.executable}" -c "import time; '
            'print(\'JOB_STARTED\', flush=True); time.sleep(2); '
            'print(\'JOB_DONE\')"'
        )
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(
                 _background_jobs,
                 "_state_dir",
                 return_value=Path(tmp) / "background_jobs",
             ), \
             mock.patch.object(_run, "_auto_verify_gui") as verify_gui:
            started_at = time.monotonic()
            started = _run.tool_run_server(
                Path(tmp),
                action="start",
                command=command,
                kind="job",
            )
            start_elapsed = time.monotonic() - started_at
            self.assertTrue(started.get("ok"), started.get("text"))
            self.assertLess(start_elapsed, 1.0)
            pid = int(started["pid"])
            self.assertTrue(started.get("job_id"))
            self.assertIs(started.get("recovered"), False)

            live_result: dict[str, object] = {}
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                live_result = _run.tool_run_server(Path(tmp), action="logs", pid=pid)
                if "JOB_STARTED" in str(live_result.get("text", "")):
                    break
                time.sleep(0.05)
            self.assertEqual(live_result.get("status"), "running")
            self.assertEqual(live_result.get("job_id"), started.get("job_id"))
            self.assertIs(live_result.get("recovered"), False)
            self.assertIn("JOB_STARTED", str(live_result.get("text", "")))

            with _run._SERVERS_LOCK:
                handle = _run._LIVE_SERVERS[pid]
            handle.proc.wait(timeout=5)

            result = _run.tool_run_server(Path(tmp), action="logs", pid=pid)

        verify_gui.assert_not_called()
        self.assertTrue(result.get("ok"), result.get("text"))
        self.assertEqual(result.get("status"), "completed")
        self.assertEqual(result.get("job_id"), started.get("job_id"))
        self.assertIs(result.get("recovered"), False)
        self.assertEqual(result.get("exit_code"), 0)
        self.assertIn("JOB_DONE", result.get("text", ""))


if __name__ == "__main__":
    unittest.main()
