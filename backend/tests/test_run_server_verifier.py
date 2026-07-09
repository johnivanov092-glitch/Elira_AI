"""run_server must report the URL it ACTUALLY bound, and the loopback verifier must
allow that URL and only that URL.

Live VaultDesk (d1511484): the agent asked for :5173, but Elira's own dev server
owned it, so Vite auto-incremented to :5174. run_server reported "started on 5173",
the loopback allowlist held 5173, and the agent verified against the WRONG app on
5173 then got SSRF-blocked guessing 5174/LAN. These pin: the actual bound URL is
parsed, the allowlist keys on the real port, and the requested-but-taken port is NOT
allowed.
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

import tempfile  # noqa: E402

from app.application.code_agent.tools import _run  # noqa: E402
from app.application.code_agent.tools._run import _parse_server_url, active_server_ports  # noqa: E402
from app.application.web.ssrf_guard import check_ssrf  # noqa: E402


class DestructiveDeleteHonestyTest(unittest.TestCase):
    """FIX #4: a recursive local delete (the `run_bash rmdir /s /q C:\\AgentLab…` from
    the live run) is already BLOCKED by the shell-safety guard — but it was returning
    a missing `ok`, so a refused command read as success. It must be ok=False, and for
    a delete the refusal points at the ssh_* tools for remote cleanup."""

    def test_blocked_delete_is_ok_false_with_ssh_hint(self):
        with tempfile.TemporaryDirectory() as ws:
            out = _run.tool_run_bash(Path(ws), command='rmdir /s /q "C:\\AgentLabGlobalCanary"')
        self.assertFalse(out["ok"])              # refused ≠ success
        self.assertIn("ssh_", out["text"])       # guided to the remote tools
        self.assertIn("blocked", out["text"].lower())

    def test_rm_rf_root_blocked_ok_false(self):
        with tempfile.TemporaryDirectory() as ws:
            out = _run.tool_run_bash(Path(ws), command="rm -rf /var/tmp/canary")
        self.assertFalse(out["ok"])

    def test_empty_command_is_ok_false(self):
        with tempfile.TemporaryDirectory() as ws:
            out = _run.tool_run_bash(Path(ws), command="   ")
        self.assertFalse(out["ok"])


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

    def test_autoincremented_actual_port_is_the_only_one_allowed(self):
        # Vite bound 5174 after 5173 was taken → the handle carries 5174.
        self._register(actual_port=5174)
        ports = active_server_ports()
        self.assertEqual(ports, {5174})
        # the agent's OWN server (actual URL) is reachable…
        self.assertIsNone(check_ssrf("http://localhost:5174/", allow_loopback_ports=ports))
        # …but the requested-but-taken 5173 (a DIFFERENT app) is NOT allowed —
        # so the verifier can't confirm against the wrong server.
        self.assertIsNotNone(check_ssrf("http://localhost:5173/", allow_loopback_ports=ports))

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


if __name__ == "__main__":
    unittest.main()
