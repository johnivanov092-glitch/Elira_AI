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


if __name__ == "__main__":
    unittest.main()
