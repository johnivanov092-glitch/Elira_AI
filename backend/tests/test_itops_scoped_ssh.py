"""Scoped Read-Only SSH — behavior tests (Step 2 + Step 3 Linux inventory adapter).

The executor capability-allowlist gate binds each scope to EXACTLY ONE adapter tool
(scope.allowed_tool): a scoped run may run ONLY tool_search + that one tool. Every
other tool — including OTHER itops adapters, run_bash, and a future read-only itops
tool — is blocked. This preserves the Step 2 boundary as adapters are added.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.agent_kernel import operation_scope as opscope  # noqa: E402
from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool  # noqa: E402
from app.application.tool_registry.runtime import register_tool, seed_builtin_tools  # noqa: E402
from app.infrastructure.it_ops import store as itstore  # noqa: E402

HEALTH = "itops_ssh_healthcheck"
INV = "itops_linux_inventory"
WIN = "itops_windows_inventory"
NET = "itops_network_inventory"


def _fake_proc(out=b"", err=b"", code=0):
    m = unittest.mock.Mock()
    m.stdout, m.stderr, m.returncode = out, err, code
    return m


class ScopeStoreTest(unittest.TestCase):
    def tearDown(self):
        for r in ("r-a", "r-b", "r-ttl", "r-old", "r-new"):
            opscope.clear_scope(r)

    def test_bind_records_profile_and_allowed_tool(self):
        view = opscope.bind_scope("r-a", "prof-1", allowed_tool=INV)
        self.assertIsNotNone(view)
        self.assertEqual(view.profile_id, "prof-1")
        self.assertEqual(view.mode, "read_only")
        self.assertEqual(view.allowed_tool, INV)
        self.assertEqual(opscope.locked_tool("r-a"), INV)
        self.assertTrue(opscope.is_scoped("r-a"))
        opscope.clear_scope("r-a")
        self.assertIsNone(opscope.get_active_scope("r-a"))
        self.assertIsNone(opscope.locked_tool("r-a"))

    def test_empty_args_never_bind(self):
        self.assertIsNone(opscope.bind_scope("", "prof-1", allowed_tool=INV))
        self.assertIsNone(opscope.bind_scope("r-a", "", allowed_tool=INV))
        self.assertIsNone(opscope.bind_scope("r-a", "prof-1", allowed_tool=""))
        self.assertFalse(opscope.is_scoped("r-a"))

    def test_ttl_expiry_reads_as_absent(self):
        opscope.bind_scope("r-ttl", "prof-1", allowed_tool=HEALTH, ttl_seconds=-1)
        self.assertIsNone(opscope.get_active_scope("r-ttl"))
        self.assertFalse(opscope.reserve_operation("r-ttl"))

    def test_reserve_operation_is_once(self):
        opscope.bind_scope("r-b", "prof-1", allowed_tool=HEALTH)
        self.assertTrue(opscope.reserve_operation("r-b"))    # first wins
        self.assertFalse(opscope.reserve_operation("r-b"))   # second refused
        self.assertFalse(opscope.reserve_operation("r-none"))  # no scope

    def test_ttl_expiry_is_sticky_lockdown(self):
        # TTL expiry blocks the operation (no LIVE scope) but must NOT re-open the
        # lockdown — the run stays locked to its allowed_tool until clear_scope.
        opscope.bind_scope("r-a", "prof-1", allowed_tool=INV, ttl_seconds=-1)
        self.assertIsNone(opscope.get_active_scope("r-a"))     # no live scope
        self.assertEqual(opscope.locked_tool("r-a"), INV)       # still locked (sticky)
        self.assertFalse(opscope.reserve_operation("r-a"))
        opscope.clear_scope("r-a")
        self.assertIsNone(opscope.locked_tool("r-a"))           # cleared only by clear_scope

    def test_abandoned_entries_are_swept(self):
        opscope.bind_scope("r-old", "prof-1", allowed_tool=HEALTH, ttl_seconds=-2000)
        self.assertTrue(opscope.is_locked_down("r-old"))
        opscope.bind_scope("r-new", "prof-2", allowed_tool=HEALTH)  # binding sweeps the dead one
        self.assertFalse(opscope.is_locked_down("r-old"))
        self.assertTrue(opscope.is_locked_down("r-new"))

    def test_claim_stream_is_once(self):
        self.assertTrue(opscope.claim_stream("itops-diag-c1"))
        self.assertFalse(opscope.claim_stream("itops-diag-c1"))
        self.assertFalse(opscope.claim_stream(""))
        self.assertTrue(opscope.claim_stream("itops-diag-c2"))


class ScopedGateTest(unittest.TestCase):
    def setUp(self):
        seed_builtin_tools()
        # A future read-only itops tool — must NOT be runnable from a scope bound to a
        # different adapter (source==itops is not allowlist membership).
        register_tool("itops_dummy_ro", lambda a: {"ok": True, "text": "dummy"},
                      source="itops", permission="auto", side_effect=False, scopes=["net.outbound"])
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        itstore.init_db()
        itstore.upsert_asset(asset_id="ssh-lab", label="Lab", kind="linux", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-ok", asset_id="ssh-lab",
                                       transport="ssh", ssh_alias="lab")
        itstore.upsert_asset(asset_id="ssh-draft", label="D", kind="linux", lifecycle_state="draft")
        itstore.put_connection_profile(profile_id="prof-draft", asset_id="ssh-draft",
                                       transport="ssh", ssh_alias="draftlab")
        self.rid = "run-scoped-1"
        opscope.clear_scope(self.rid)

    def tearDown(self):
        opscope.clear_scope(self.rid)
        itstore._DB_PATH_OVERRIDE = None

    def _bind(self, allowed_tool, profile_id="prof-ok", **kw):
        opscope.bind_scope(self.rid, profile_id, allowed_tool=allowed_tool, **kw)

    def _exec(self, tool, args, run_id=None):
        calls = []

        def dispatch(name, a):
            calls.append((name, dict(a)))
            return {"ok": True, "text": "dispatched"}
        req = ToolExecutionRequest(run_id=run_id or self.rid, agent_id="code-agent",
                                   project_scope_id="", tool_name=tool, args=args, source="code_agent")
        return execute_tool(req, dispatch), calls

    # ── the Step 2 boundary preserved: a scope binds ONE adapter ────────────────
    def test_inventory_scope_allows_only_inventory(self):
        self._bind(INV)
        ok, calls = self._exec(INV, {"profile_id": "prof-ok"})   # the bound adapter runs
        self.assertEqual(ok.status, "ok", ok.output)
        self.assertEqual(len(calls), 1)
        for other in (HEALTH, "run_bash", "itops_dummy_ro"):     # everything else blocked
            res, c = self._exec(other, {"profile_id": "prof-ok", "command": "id"})
            self.assertEqual(res.status, "blocked", other)
            self.assertEqual(res.error, "scope_restricted", other)
            self.assertEqual(c, [])

    def test_windows_inventory_scope_allows_only_windows_inventory(self):
        self._bind(WIN)                                          # gate is kind-agnostic
        ok, calls = self._exec(WIN, {"profile_id": "prof-ok"})
        self.assertEqual(ok.status, "ok", ok.output)
        self.assertEqual(len(calls), 1)
        for other in (HEALTH, INV, "run_bash", "itops_dummy_ro"):
            res, c = self._exec(other, {"profile_id": "prof-ok", "command": "id"})
            self.assertEqual(res.error, "scope_restricted", other)
            self.assertEqual(c, [])

    def test_network_scope_allows_only_network_inventory(self):
        opscope.bind_scope_network(self.rid, cidr="192.168.88.0/24",
                                   port_profile="common-v1", allowed_tool=NET)
        ok, calls = self._exec(NET, {})                      # no args → allowed
        self.assertEqual(ok.status, "ok", ok.output)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], {})                    # gate injected empty authoritative args
        for other in (HEALTH, INV, WIN, "run_bash", "ssh_run", "itops_dummy_ro"):
            res, c = self._exec(other, {"profile_id": "prof-ok", "command": "id", "host": "x"})
            self.assertEqual(res.error, "scope_restricted", other)
            self.assertEqual(c, [])

    def test_network_tool_rejects_any_model_arg(self):
        opscope.bind_scope_network(self.rid, cidr="192.168.88.0/24",
                                   port_profile="common-v1", allowed_tool=NET)
        res, c = self._exec(NET, {"cidr": "10.0.0.0/8"})     # model tries to supply a target
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.error, "scope_args_forbidden")
        self.assertEqual(c, [])

    def test_network_tool_without_scope_blocked(self):
        res, c = self._exec(NET, {}, run_id="unscoped-net")
        self.assertEqual(res.error, "no_operation_scope")
        self.assertEqual(c, [])

    def test_ssh_and_network_scopes_are_isolated(self):
        # an SSH scope never opens the network tool, and a network scope never opens SSH.
        self._bind(HEALTH)
        res, _ = self._exec(NET, {})
        self.assertEqual(res.error, "scope_restricted")
        opscope.clear_scope(self.rid)
        opscope.bind_scope_network(self.rid, cidr="192.168.88.0/24",
                                   port_profile="common-v1", allowed_tool=NET)
        res2, _ = self._exec(HEALTH, {"profile_id": "prof-ok"})
        self.assertEqual(res2.error, "scope_restricted")

    def test_healthcheck_scope_allows_only_healthcheck(self):
        self._bind(HEALTH)
        res, c = self._exec(INV, {"profile_id": "prof-ok"})      # inventory blocked in a health scope
        self.assertEqual(res.error, "scope_restricted")
        self.assertEqual(c, [])
        ok, calls = self._exec(HEALTH, {"profile_id": "prof-ok"})
        self.assertEqual(ok.status, "ok")
        self.assertEqual(len(calls), 1)

    def test_adapter_without_scope_is_blocked(self):
        for tool in (HEALTH, INV, "itops_dummy_ro"):
            res, calls = self._exec(tool, {"profile_id": "prof-ok"}, run_id=f"unscoped-{tool}")
            self.assertEqual(res.status, "blocked", tool)
            self.assertEqual(res.error, "no_operation_scope", tool)
            self.assertEqual(calls, [])

    def test_wrong_profile_is_blocked(self):
        self._bind(INV)
        res, calls = self._exec(INV, {"profile_id": "prof-other"})
        self.assertEqual(res.error, "scope_mismatch")
        self.assertEqual(calls, [])

    def test_draft_profile_is_blocked(self):
        self._bind(HEALTH, profile_id="prof-draft")
        res, calls = self._exec(HEALTH, {"profile_id": "prof-draft"})
        self.assertEqual(res.error, "profile_not_enabled")
        self.assertEqual(calls, [])

    def test_one_operation_per_run(self):
        self._bind(HEALTH)
        ok, _ = self._exec(HEALTH, {"profile_id": "prof-ok"})
        self.assertEqual(ok.status, "ok")
        res2, c2 = self._exec(HEALTH, {"profile_id": "prof-ok"})   # second → refused
        self.assertEqual(res2.error, "operation_already_used")
        self.assertEqual(c2, [])

    def test_scope_layer_error_fails_closed_for_diag_run(self):
        import app.application.agent_kernel.operation_scope as opmod
        with unittest.mock.patch.object(opmod, "locked_tool", side_effect=RuntimeError("boom")):
            res, calls = self._exec("run_bash", {"command": "whoami"}, run_id="itops-diag-err")
            self.assertEqual(res.error, "scope_unavailable")
            self.assertEqual(calls, [])
            res2, _ = self._exec("run_bash", {"command": "whoami"}, run_id="plain-run")
            self.assertNotEqual(res2.error, "scope_unavailable")   # normal run untouched
            resh, ch = self._exec(HEALTH, {"profile_id": "prof-ok"}, run_id="plain-run-2")
            self.assertEqual(resh.error, "scope_unavailable")      # itops tool blocked on error

    def test_expired_scope_still_blocks_other_tools(self):
        self._bind(INV, ttl_seconds=-1)
        res, calls = self._exec("run_bash", {"command": "whoami"})
        self.assertEqual(res.error, "scope_restricted")           # sticky lockdown
        self.assertEqual(calls, [])
        res2, c2 = self._exec(INV, {"profile_id": "prof-ok"})
        self.assertEqual(res2.error, "no_operation_scope")        # adapter needs live scope
        self.assertEqual(c2, [])

    def test_unscoped_run_leaves_other_tools_untouched(self):
        res, _ = self._exec("run_bash", {"command": "whoami"}, run_id="run-plain")
        self.assertNotEqual(res.error, "scope_restricted")

    def test_tool_search_hides_both_itops_tools_when_flag_off(self):
        from app.application.agent_kernel.deferred_tools import clear_run, enable_deferred_tools
        from app.application.code_agent.tools import tool_search
        from app.application import feature_flags as ff
        rid = "search-run-1"
        enable_deferred_tools(rid, ())
        q = "itops ssh linux inventory health check diagnostic hostname uname"
        try:
            with unittest.mock.patch.object(ff, "flag_enabled", side_effect=lambda n: n != "itops"):
                off = [m.get("name") for m in tool_search(run_id=rid, query=q).get("matches", [])]
            self.assertNotIn(HEALTH, off)
            self.assertNotIn(INV, off)
            with unittest.mock.patch.object(ff, "flag_enabled", side_effect=lambda n: True):
                on = [m.get("name") for m in tool_search(run_id=rid, query=q).get("matches", [])]
            self.assertIn(HEALTH, on)
            self.assertIn(INV, on)
        finally:
            clear_run(rid)


class InventoryHandlerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        itstore.init_db()
        itstore.upsert_asset(asset_id="ssh-lab", label="Lab", kind="linux", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-linux", asset_id="ssh-lab",
                                       transport="ssh", ssh_alias="lab")
        itstore.upsert_asset(asset_id="win-1", label="Win", kind="windows", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-win", asset_id="win-1",
                                       transport="ssh", ssh_alias="winbox")

    def tearDown(self):
        itstore._DB_PATH_OVERRIDE = None

    def _run(self, run_id, fakes):
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.tool_providers import itops_provider
        tok = set_current_run_id(run_id)
        try:
            with unittest.mock.patch("subprocess.run", side_effect=fakes):
                return itops_provider.tool_itops_linux_inventory(profile_id="prof-linux")
        finally:
            reset_current_run_id(tok)

    def _n_cmds(self):
        from app.application.tool_providers.itops_provider import _INVENTORY_COMMANDS
        return len(_INVENTORY_COMMANDS)

    def test_all_ok_writes_one_evidence_per_command(self):
        n = self._n_cmds()
        out = self._run("ctx-inv-ok", [_fake_proc(b"data\n", b"", 0) for _ in range(n)])
        self.assertTrue(out["ok"], out)
        ev = itstore.list_evidence("ctx-inv-ok")
        self.assertEqual(len(ev), n)
        self.assertTrue(all(e["operation"].startswith("linux_inventory:") for e in ev))

    def test_command_failure_makes_inventory_unsuccessful(self):
        n = self._n_cmds()
        fakes = [_fake_proc(b"ok\n", b"", 0) for _ in range(n)]
        fakes[3] = _fake_proc(b"", b"some real error", 1)   # a required command fails
        out = self._run("ctx-inv-fail", fakes)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "inventory_command_failed")

    def test_systemd_absent_is_unsupported_not_failure(self):
        n = self._n_cmds()
        fakes = [_fake_proc(b"ok\n", b"", 0) for _ in range(n)]
        # the LAST command is the optional `systemctl --failed`; systemd-absent signal
        fakes[-1] = _fake_proc(b"", b"System has not been booted with systemd as init system (PID 1).", 1)
        out = self._run("ctx-inv-nosysd", fakes)
        self.assertTrue(out["ok"], out)                       # unsupported must not fail it
        last = out["results"][-1]
        self.assertEqual(last["status"], "unsupported")

    def test_other_nonzero_on_optional_is_a_failure(self):
        n = self._n_cmds()
        fakes = [_fake_proc(b"ok\n", b"", 0) for _ in range(n)]
        fakes[-1] = _fake_proc(b"", b"permission denied", 1)   # NOT the systemd-absent signal
        out = self._run("ctx-inv-optfail", fakes)
        self.assertFalse(out["ok"])
        self.assertEqual(out["results"][-1]["status"], "failed")

    def test_total_12k_budget_bounds_evidence(self):
        n = self._n_cmds()
        out = self._run("ctx-inv-budget", [_fake_proc(b"A" * 5000, b"", 0) for _ in range(n)])
        self.assertTrue(out["ok"])
        ev = itstore.list_evidence("ctx-inv-budget")
        total = sum(len(e["result"].get("stdout", "")) + len(e["result"].get("stderr", "")) for e in ev)
        self.assertLessEqual(total, 12000)                    # shared budget bounds evidence too

    def test_reply_text_within_12k(self):
        # the assembled reply text (marker counted) never exceeds the 12K cap.
        n = self._n_cmds()
        out = self._run("ctx-inv-text", [_fake_proc(b"B" * 5000, b"", 0) for _ in range(n)])
        self.assertLessEqual(len(out["text"]), 12000)

    def test_requires_linux_asset(self):
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.tool_providers import itops_provider
        tok = set_current_run_id("ctx-inv-win")
        try:
            out = itops_provider.tool_itops_linux_inventory(profile_id="prof-win")
        finally:
            reset_current_run_id(tok)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "not_linux_asset")
        self.assertEqual(itstore.list_evidence("ctx-inv-win"), [])   # nothing run

    # ── Windows inventory adapter (#2) ──────────────────────────────────────────
    def _run_win(self, run_id, pid, fakes):
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.tool_providers import itops_provider
        tok = set_current_run_id(run_id)
        try:
            with unittest.mock.patch("subprocess.run", side_effect=fakes):
                return itops_provider.tool_itops_windows_inventory(profile_id=pid)
        finally:
            reset_current_run_id(tok)

    def _n_win(self):
        from app.application.tool_providers.itops_provider import _WINDOWS_COMMANDS
        return len(_WINDOWS_COMMANDS)

    def test_windows_encoded_command_wraps_and_not_raw(self):
        import base64 as _b64
        from app.application.tool_providers.itops_provider import (
            _ps_argv, _ps_encode, _ps_wrap, _WINDOWS_COMMANDS)
        script = _WINDOWS_COMMANDS[0][1]
        wrapped = _ps_wrap(script)
        self.assertIn("$ErrorActionPreference='Stop'", wrapped)   # errors fail the command
        self.assertIn("catch", wrapped)
        self.assertIn("exit 1", wrapped)
        enc = _ps_encode(wrapped)
        self.assertEqual(_b64.b64decode(enc).decode("utf-16-le"), wrapped)   # UTF-16LE round-trip
        argv = _ps_argv("lab", script)
        self.assertIn("powershell.exe", argv)
        self.assertIn("-NoProfile", argv)
        self.assertIn("-NonInteractive", argv)
        self.assertIn("-EncodedCommand", argv)
        self.assertIn(enc, argv)                           # the WRAPPED, encoded payload
        self.assertNotIn("-Command", argv)                 # not -Command
        self.assertNotIn("Bypass", " ".join(argv))         # no ExecutionPolicy Bypass
        self.assertNotIn(script, argv)                     # the raw script is never on the argv

    def test_windows_wrap_makes_nonterminating_errors_nonzero(self):
        # Regression on a REAL local powershell.exe: a non-terminating error (Write-Error,
        # a failing CIM query) — which by default can exit 0 depending on the host — MUST
        # become a non-zero exit under our Stop+try/catch wrap, else a WMI/CIM failure
        # would be recorded as a successful inventory. Skip off-Windows. (Whether the
        # UNWRAPPED command exits 0 or 1 is version/host-specific and not asserted; the
        # invariant we guarantee is: with the wrap, an error fails the command.)
        import platform
        import shutil
        import subprocess as sp
        if platform.system() != "Windows" or not shutil.which("powershell.exe"):
            self.skipTest("requires a local powershell.exe (Windows)")
        from app.application.tool_providers.itops_provider import _ps_encode, _ps_wrap
        for bad in ("Write-Error 'regression-check'", "Get-CimInstance Win32_NonExistentClass_zzz"):
            proc = sp.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                           "-EncodedCommand", _ps_encode(_ps_wrap(bad))], capture_output=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0, f"wrap must fail the command on: {bad}")

    def test_windows_all_ok_writes_readable_evidence(self):
        n = self._n_win()
        out = self._run_win("ctx-win-ok", "prof-win", [_fake_proc(b"data\r\n", b"", 0) for _ in range(n)])
        self.assertTrue(out["ok"], out)
        ev = itstore.list_evidence("ctx-win-ok")
        self.assertEqual(len(ev), n)
        self.assertTrue(all(e["operation"].startswith("windows_inventory:") for e in ev))
        for e in ev:                                        # evidence stores the readable script
            self.assertNotIn("EncodedCommand", e["result"].get("command", ""))

    def test_windows_requires_windows_asset(self):
        out = self._run_win("ctx-win-linux", "prof-linux", [_fake_proc(b"", b"", 0)])
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "not_windows_asset")
        self.assertEqual(itstore.list_evidence("ctx-win-linux"), [])

    def test_windows_command_failure_makes_ok_false(self):
        n = self._n_win()
        fakes = [_fake_proc(b"ok\r\n", b"", 0) for _ in range(n)]
        fakes[2] = _fake_proc(b"", b"boom", 1)
        out = self._run_win("ctx-win-fail", "prof-win", fakes)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "inventory_command_failed")


class DiagnosticsRouteTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.itops_routes import router
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        from app.application import feature_flags as ff
        self._flag = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag.start()
        itstore.init_db()
        itstore.upsert_asset(asset_id="ssh-lab", label="Lab", kind="linux", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-ok", asset_id="ssh-lab",
                                       transport="ssh", ssh_alias="lab")
        itstore.upsert_asset(asset_id="win-1", label="Win", kind="windows", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-win", asset_id="win-1",
                                       transport="ssh", ssh_alias="winbox")
        itstore.upsert_asset(asset_id="ssh-draft", label="D", kind="linux", lifecycle_state="draft")
        itstore.put_connection_profile(profile_id="prof-draft", asset_id="ssh-draft",
                                       transport="ssh", ssh_alias="draftlab")
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._flag.stop()
        itstore._DB_PATH_OVERRIDE = None

    def _start(self, **body):
        return self.client.post("/api/itops/diagnostics/start", json=body)

    def test_default_adapter_binds_healthcheck(self):
        r = self._start(profile_id="prof-ok")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["tool"], HEALTH)
        self.assertIn(HEALTH, body["message"])
        scope = opscope.get_active_scope(body["run_id"])
        self.assertEqual(scope.allowed_tool, HEALTH)
        opscope.clear_scope(body["run_id"])

    def test_linux_inventory_adapter_binds_inventory_tool(self):
        r = self._start(profile_id="prof-ok", adapter="linux_inventory")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["tool"], INV)
        self.assertIn(INV, body["message"])
        scope = opscope.get_active_scope(body["run_id"])
        self.assertEqual(scope.allowed_tool, INV)              # scope bound to exactly this tool
        opscope.clear_scope(body["run_id"])

    def test_linux_inventory_refused_on_non_linux_asset(self):
        r = self._start(profile_id="prof-win", adapter="linux_inventory")
        self.assertEqual(r.status_code, 409, r.text)

    def test_windows_inventory_adapter_binds_windows_tool(self):
        r = self._start(profile_id="prof-win", adapter="windows_inventory")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["tool"], WIN)
        self.assertEqual(opscope.get_active_scope(body["run_id"]).allowed_tool, WIN)
        opscope.clear_scope(body["run_id"])

    def test_windows_inventory_refused_on_non_windows_asset(self):
        r = self._start(profile_id="prof-ok", adapter="windows_inventory")   # prof-ok is linux
        self.assertEqual(r.status_code, 409, r.text)

    def test_client_cannot_pass_a_tool_name(self):
        # adapter is a Literal enum; an arbitrary value (or a tool name) is a 422.
        self.assertEqual(self._start(profile_id="prof-ok", adapter="itops_ssh_healthcheck").status_code, 422)
        self.assertEqual(self._start(profile_id="prof-ok", allowed_tool="x").status_code, 422)

    def test_start_refuses_draft_and_unknown(self):
        self.assertEqual(self._start(profile_id="prof-draft").status_code, 409)
        self.assertEqual(self._start(profile_id="prof-nope").status_code, 404)

    def test_flag_off_disables_route(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            self.assertEqual(self._start(profile_id="prof-ok").status_code, 404)


class DiagStreamGuardTest(unittest.TestCase):
    """A diagnostic run_id may be streamed exactly once and never resumed."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.code_agent_routes import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_stream_refuses_diag_run_without_live_scope(self):
        r = self.client.post("/api/code-agent/stream",
                             json={"message": "x", "project_root": ".", "run_id": "itops-diag-nobind"})
        self.assertEqual(r.status_code, 409, r.text)

    def test_stream_refuses_already_claimed_diag_run(self):
        opscope.bind_scope("itops-diag-claimed", "prof-x", allowed_tool=HEALTH)
        opscope.claim_stream("itops-diag-claimed")
        try:
            r = self.client.post("/api/code-agent/stream",
                                 json={"message": "x", "project_root": ".", "run_id": "itops-diag-claimed"})
            self.assertEqual(r.status_code, 409, r.text)
        finally:
            opscope.clear_scope("itops-diag-claimed")

    def test_resume_refuses_diag_run(self):
        r = self.client.post("/api/code-agent/runs/itops-diag-x/resume")
        self.assertEqual(r.status_code, 409, r.text)


class EvidenceHistoryRouteTest(unittest.TestCase):
    """Read-only diagnostics journal: /evidence/runs summary + /evidence?run_id detail."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.itops_routes import router
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        from app.application import feature_flags as ff
        self._flag = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag.start()
        itstore.init_db()
        ti = "ssh-lab/prof-ok"
        # inventory run: ok, ok, unsupported, failed
        for op, status, code in [("linux_inventory:hostname", "ok", "0"),
                                 ("linux_inventory:cpu", "ok", "0"),
                                 ("linux_inventory:failed_units", "unsupported", "1"),
                                 ("linux_inventory:disk", "failed", "1")]:
            itstore.record_evidence(run_id="run-inv", target_identity=ti, scanner_vantage="v",
                                    operation=op, exit_status=code,
                                    result={"alias": "lab", "command": op, "status": status,
                                            "stdout": "x", "stderr": ""})
        # legacy health run: no result.status → counters fall back to exit_status
        for op, code in [("ssh_healthcheck:hostname", "0"), ("ssh_healthcheck:uptime", "1")]:
            itstore.record_evidence(run_id="run-health", target_identity=ti, scanner_vantage="v",
                                    operation=op, exit_status=code,
                                    result={"alias": "lab", "command": op, "stdout": "y", "stderr": ""})
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._flag.stop()
        itstore._DB_PATH_OVERRIDE = None

    def test_flag_off_evidence_endpoints_404(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            self.assertEqual(self.client.get("/api/itops/evidence/runs").status_code, 404)
            self.assertEqual(self.client.get("/api/itops/evidence?run_id=run-inv").status_code, 404)

    def test_runs_summary_counts_mixed_statuses(self):
        r = self.client.get("/api/itops/evidence/runs")
        self.assertEqual(r.status_code, 200, r.text)
        runs = {run["run_id"]: run for run in r.json()["runs"]}
        inv = runs["run-inv"]
        self.assertEqual(inv["adapter"], "linux_inventory")
        self.assertEqual((inv["ok"], inv["failed"], inv["unsupported"], inv["count"]), (2, 1, 1, 4))
        health = runs["run-health"]
        self.assertEqual(health["adapter"], "ssh_healthcheck")
        self.assertEqual((health["ok"], health["failed"]), (1, 1))   # exit_status fallback

    def test_evidence_detail_by_exact_run(self):
        r = self.client.get("/api/itops/evidence?run_id=run-inv")
        self.assertEqual(r.status_code, 200, r.text)
        ev = r.json()["evidence"]
        self.assertEqual(len(ev), 4)
        self.assertTrue(all(e["operation"].startswith("linux_inventory:") for e in ev))
        # read-only journal must never leak a secret / auth_ref
        self.assertNotIn("auth_ref", r.text)
        for e in ev:
            self.assertNotIn("auth_ref", e)
            self.assertNotIn("auth_ref", e.get("result", {}))

    def test_detail_projects_out_non_whitelisted_fields(self):
        # a polluted stored result must NOT leak through the read API — the public
        # projection whitelists only alias/command/status/stdout/stderr.
        itstore.record_evidence(run_id="run-dirty", target_identity="ssh-lab/prof-ok",
                                scanner_vantage="v", operation="ssh_healthcheck:hostname",
                                exit_status="0",
                                result={"alias": "lab", "command": "hostname", "status": "ok",
                                        "stdout": "host", "stderr": "",
                                        "auth_ref": "cred://SECRET", "password": "P@SSW0RD",
                                        "private_key": "-----BEGIN KEY-----"})
        r = self.client.get("/api/itops/evidence?run_id=run-dirty")
        self.assertEqual(r.status_code, 200, r.text)
        for leak in ("SECRET", "P@SSW0RD", "auth_ref", "password", "private_key", "BEGIN KEY"):
            self.assertNotIn(leak, r.text)
        rec = r.json()["evidence"][0]
        self.assertEqual(set(rec["result"].keys()), {"alias", "command", "status", "stdout", "stderr"})

    def test_network_nested_secrets_are_projected_out(self):
        # network evidence carries NESTED containers (ports/counts/caps). A secret
        # smuggled INSIDE a container must be dropped by the typed projection — the old
        # flat whitelist copied counts/caps wholesale and leaked them.
        itstore.record_evidence(
            run_id="run-net-dirty", target_identity="net/192.168.88.0/24",
            scanner_vantage="elira-local", operation="network_inventory:_summary",
            exit_status="0",
            result={"cidr": "192.168.88.0/24", "port_profile": "common-v1",
                    "ports": [22, 443, "auth_ref"], "vantage": "elira-local",
                    "source_ip": "192.168.88.99", "planned": 2032, "attempted": 2032,
                    "completed": 2032, "open_count": 1, "stop_reason": "complete",
                    "counts": {"open": 1, "refused": 5, "timeout": 10, "unreachable": 0,
                               "local_error": 0, "auth_ref": "cred://SECRET"},
                    "caps": {"rate_limit": 50, "total_timeout": 60.0, "per_connect_timeout": 1.0,
                             "in_flight": 64, "max_hosts": 254, "password": "P@SSW0RD"},
                    "auth_ref": "cred://TOPSECRET", "password": "P@SSW0RD"})
        r = self.client.get("/api/itops/evidence?run_id=run-net-dirty")
        self.assertEqual(r.status_code, 200, r.text)
        for leak in ("SECRET", "P@SSW0RD", "auth_ref", "password", "TOPSECRET"):
            self.assertNotIn(leak, r.text)
        res = r.json()["evidence"][0]["result"]
        self.assertEqual(set(res["counts"].keys()),
                         {"open", "refused", "timeout", "unreachable", "local_error"})
        self.assertEqual(set(res["caps"].keys()),
                         {"rate_limit", "total_timeout", "per_connect_timeout", "in_flight", "max_hosts"})
        self.assertEqual(res["ports"], [22, 443])       # the non-int entry is dropped

    def test_evidence_requires_run_id(self):
        self.assertEqual(self.client.get("/api/itops/evidence").status_code, 422)      # missing
        self.assertEqual(self.client.get("/api/itops/evidence?run_id=").status_code, 422)  # empty

    def test_runs_limit_capped(self):
        r = self.client.get("/api/itops/evidence/runs?limit=999")
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(r.json()["runs"]), 50)


class NetworkRouteTest(unittest.TestCase):
    """/network/start — CIDR parse/authorization + scope bind. Read-only, flag-gated."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.itops_routes import router
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        from app.application import feature_flags as ff
        self._flag = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag.start()
        self._env = unittest.mock.patch.dict("os.environ", {"ITOPS_NETWORK_ALLOWED_CIDRS": "192.168.88.0/24"})
        self._env.start()
        itstore.init_db()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._env.stop()
        self._flag.stop()
        itstore._DB_PATH_OVERRIDE = None

    def _start(self, **body):
        return self.client.post("/api/itops/network/start", json=body)

    def test_authorized_cidr_binds_network_scope(self):
        r = self._start(cidr="192.168.88.0/24")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["vantage"], "elira-local")     # not a client param
        self.assertEqual(body["hosts"], 254)
        self.assertEqual(body["profile"]["name"], "common-v1")
        scope = opscope.get_active_scope(body["run_id"])
        self.assertEqual(scope.target_kind, "network")
        self.assertEqual(scope.allowed_tool, NET)
        self.assertEqual(scope.network.cidr, "192.168.88.0/24")
        opscope.clear_scope(body["run_id"])

    def test_unauthorized_cidr_403(self):
        self.assertEqual(self._start(cidr="192.168.99.0/24").status_code, 403)   # not in allowlist

    def test_ipv6_400(self):
        self.assertEqual(self._start(cidr="fd00::/120").status_code, 400)

    def test_public_cidr_403(self):
        self.assertEqual(self._start(cidr="8.8.8.0/24").status_code, 403)

    def test_prefix_too_large_400(self):
        self.assertEqual(self._start(cidr="10.0.0.0/16").status_code, 400)

    def test_non_canonical_400(self):
        self.assertEqual(self._start(cidr="192.168.88.5/24").status_code, 400)   # host bits set

    def test_ports_not_accepted(self):
        self.assertEqual(self._start(cidr="192.168.88.0/24", ports=[22]).status_code, 422)

    def test_profile_endpoint_exposes_ports_and_allowlist(self):
        r = self.client.get("/api/itops/network/profile")
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(len(b["profile"]["ports"]), 8)
        self.assertIn("192.168.88.0/24", b["allowed_cidrs"])
        self.assertEqual(b["vantage"], "elira-local")

    def test_flag_off_404(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            self.assertEqual(self._start(cidr="192.168.88.0/24").status_code, 404)
            self.assertEqual(self.client.get("/api/itops/network/profile").status_code, 404)


class NetworkAdapterTest(unittest.TestCase):
    """The itops_network_inventory handler: open evidence + MANDATORY summary + ok semantics."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        itstore.init_db()
        # The handler re-verifies CIDR authorization against this allowlist (defence in
        # depth), so the bound CIDR must be authorized for the happy-path tests.
        self._env = unittest.mock.patch.dict("os.environ", {"ITOPS_NETWORK_ALLOWED_CIDRS": "192.168.88.0/24"})
        self._env.start()
        self.rid = "net-run-1"
        opscope.clear_scope(self.rid)
        opscope.bind_scope_network(self.rid, cidr="192.168.88.0/24",
                                   port_profile="common-v1", allowed_tool=NET)

    def tearDown(self):
        opscope.clear_scope(self.rid)
        self._env.stop()
        itstore._DB_PATH_OVERRIDE = None

    def _result(self, status, opens):
        from app.application.it_ops import net_inventory as ni
        n = 2032 if status == "complete" else 100
        return ni.ScanResult(cidr="192.168.88.0/24", profile="common-v1", vantage="elira-local",
                             source_ip="192.168.88.99", started_at=0.0, finished_at=1.0,
                             planned=2032, attempted=n, completed=n,
                             counts={"open": len(opens), "refused": 5, "timeout": 10,
                                     "unreachable": 0, "local_error": 0},
                             opens=opens, stop_reason=("complete" if status == "complete" else "timed_out"),
                             status=status)

    def _run(self, scan_result):
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.it_ops import net_inventory as ni
        from app.application.tool_providers import itops_provider
        tok = set_current_run_id(self.rid)
        try:
            with unittest.mock.patch.object(ni, "run_scan", return_value=scan_result):
                return itops_provider.tool_itops_network_inventory()
        finally:
            reset_current_run_id(tok)

    def test_complete_writes_opens_and_mandatory_summary(self):
        opens = [{"host": "192.168.88.10", "port": 22}, {"host": "192.168.88.10", "port": 443}]
        out = self._run(self._result("complete", opens))
        self.assertTrue(out["ok"], out)
        ops = [e["operation"] for e in itstore.list_evidence(self.rid)]
        self.assertEqual(ops.count("network_inventory:open"), 2)          # one per confirmed open
        self.assertEqual(ops.count("network_inventory:_summary"), 1)      # MANDATORY summary
        summ = next(e for e in itstore.list_evidence(self.rid)
                    if e["operation"] == "network_inventory:_summary")
        self.assertEqual(summ["result"]["status"], "complete")
        self.assertEqual(summ["result"]["planned"], 2032)

    def test_open_evidence_write_failure_makes_ok_false(self):
        # Mirror the SSH/inventory invariant: no confirmed-open may be reported without
        # its own durable proof row. A failed :open write (summary still OK) → ok=false.
        opens = [{"host": "192.168.88.10", "port": 22}]
        real = itstore.record_evidence

        def flaky(*a, **kw):
            if kw.get("operation") == "network_inventory:open":
                raise RuntimeError("disk full")     # the per-open proof row fails to persist
            return real(*a, **kw)                    # the mandatory summary still succeeds

        with unittest.mock.patch.object(itstore, "record_evidence", side_effect=flaky):
            out = self._run(self._result("complete", opens))
        self.assertFalse(out["ok"], out)                             # cannot claim success…
        self.assertEqual(out["error"], "evidence_persist_failed")    # …with an unproven open
        ops = [e["operation"] for e in itstore.list_evidence(self.rid)]
        self.assertEqual(ops.count("network_inventory:open"), 0)     # the open row never landed
        self.assertEqual(ops.count("network_inventory:_summary"), 1) # summary still mandatory

    def test_partial_is_not_ok_but_still_writes_summary(self):
        out = self._run(self._result("timed_out", []))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "scan_incomplete")                 # not a false "nothing found"
        summ = [e for e in itstore.list_evidence(self.rid)
                if e["operation"] == "network_inventory:_summary"]
        self.assertEqual(len(summ), 1)                                    # summary written on partial too
        self.assertEqual(summ[0]["result"]["status"], "timed_out")

    def test_no_bound_scope_refuses(self):
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.tool_providers import itops_provider
        tok = set_current_run_id("no-scope-run")
        try:
            out = itops_provider.tool_itops_network_inventory()
        finally:
            reset_current_run_id(tok)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_network_scope")

    def test_handler_refuses_scope_over_unauthorized_cidr(self):
        # Defence in depth: even a validly-shaped scope whose CIDR is NOT in the
        # allowlist must fail closed in the handler (route auth is not the only gate),
        # and it must NOT scan or write any evidence.
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.it_ops import net_inventory as ni
        from app.application.tool_providers import itops_provider
        rid = "net-unauth-run"
        opscope.clear_scope(rid)
        opscope.bind_scope_network(rid, cidr="10.9.9.0/24",           # a valid /24, NOT allowlisted
                                   port_profile="common-v1", allowed_tool=NET)
        tok = set_current_run_id(rid)
        try:
            with unittest.mock.patch.object(ni, "run_scan") as scan:
                out = itops_provider.tool_itops_network_inventory()
        finally:
            reset_current_run_id(tok)
            opscope.clear_scope(rid)
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["error"], "cidr_not_authorized")
        scan.assert_not_called()                                     # fail-closed BEFORE any scan
        self.assertEqual(itstore.list_evidence(rid), [])            # and no evidence written


if __name__ == "__main__":
    unittest.main()
