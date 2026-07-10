"""Scoped Read-Only SSH v1 — behavior tests.

Covers the operation-scope store, the executor capability-allowlist gate (a scoped
run may run ONLY tool_search + itops_ssh_healthcheck; everything else is blocked;
the scoped tool is blocked without a scope / on a wrong profile / on a draft asset
/ after its one use), and the /diagnostics/start route.
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
from app.application.tool_registry.runtime import seed_builtin_tools  # noqa: E402
from app.infrastructure.it_ops import store as itstore  # noqa: E402


class ScopeStoreTest(unittest.TestCase):
    def tearDown(self):
        for r in ("r-a", "r-b", "r-ttl", "r-old", "r-new"):
            opscope.clear_scope(r)

    def test_bind_get_is_scoped_clear(self):
        self.assertIsNone(opscope.get_active_scope("r-a"))
        self.assertFalse(opscope.is_scoped("r-a"))
        view = opscope.bind_scope("r-a", "prof-1")
        self.assertIsNotNone(view)
        self.assertEqual(view.profile_id, "prof-1")
        self.assertEqual(view.mode, "read_only")
        self.assertTrue(opscope.is_scoped("r-a"))
        opscope.clear_scope("r-a")
        self.assertIsNone(opscope.get_active_scope("r-a"))

    def test_empty_run_id_never_scoped(self):
        self.assertIsNone(opscope.bind_scope("", "prof-1"))
        self.assertIsNone(opscope.bind_scope("r-a", ""))
        self.assertFalse(opscope.is_scoped(""))

    def test_ttl_expiry_reads_as_absent(self):
        opscope.bind_scope("r-ttl", "prof-1", ttl_seconds=-1)  # already expired
        self.assertIsNone(opscope.get_active_scope("r-ttl"))
        self.assertFalse(opscope.reserve_healthcheck("r-ttl"))

    def test_reserve_healthcheck_is_once(self):
        opscope.bind_scope("r-b", "prof-1")
        self.assertTrue(opscope.reserve_healthcheck("r-b"))    # first wins
        self.assertFalse(opscope.reserve_healthcheck("r-b"))   # second refused
        self.assertFalse(opscope.reserve_healthcheck("r-none"))  # no scope

    def test_ttl_expiry_is_sticky_lockdown(self):
        # TTL expiry blocks the health check (no LIVE scope) but must NOT re-open the
        # capability lockdown — the run stays locked down until clear_scope.
        opscope.bind_scope("r-a", "prof-1", ttl_seconds=-1)   # expired immediately
        self.assertIsNone(opscope.get_active_scope("r-a"))     # no live scope
        self.assertFalse(opscope.is_scoped("r-a"))
        self.assertTrue(opscope.is_locked_down("r-a"))          # still locked down
        self.assertFalse(opscope.reserve_healthcheck("r-a"))    # health check refused
        opscope.clear_scope("r-a")
        self.assertFalse(opscope.is_locked_down("r-a"))         # cleared only by clear_scope

    def test_abandoned_entries_are_swept(self):
        opscope.bind_scope("r-old", "prof-1", ttl_seconds=-2000)  # long dead (no live run)
        self.assertTrue(opscope.is_locked_down("r-old"))
        opscope.bind_scope("r-new", "prof-2")                     # binding sweeps the dead one
        self.assertFalse(opscope.is_locked_down("r-old"))
        self.assertTrue(opscope.is_locked_down("r-new"))


class ScopedGateTest(unittest.TestCase):
    TOOL = "itops_ssh_healthcheck"

    def setUp(self):
        seed_builtin_tools()
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

    def _exec(self, tool, args, run_id=None):
        calls = []

        def dispatch(name, a):
            calls.append((name, dict(a)))
            return {"ok": True, "text": "dispatched"}
        req = ToolExecutionRequest(run_id=run_id or self.rid, agent_id="code-agent",
                                   project_scope_id="", tool_name=tool, args=args, source="code_agent")
        return execute_tool(req, dispatch), calls

    def test_healthcheck_without_scope_is_blocked(self):
        res, calls = self._exec(self.TOOL, {"profile_id": "prof-ok"})
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.error, "no_operation_scope")
        self.assertEqual(calls, [])                              # dispatch never reached

    def test_healthcheck_wrong_profile_is_blocked(self):
        opscope.bind_scope(self.rid, "prof-ok")
        res, calls = self._exec(self.TOOL, {"profile_id": "prof-other"})
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.error, "scope_mismatch")
        self.assertEqual(calls, [])

    def test_healthcheck_draft_profile_is_blocked(self):
        opscope.bind_scope(self.rid, "prof-draft")
        res, calls = self._exec(self.TOOL, {"profile_id": "prof-draft"})
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.error, "profile_not_enabled")
        self.assertEqual(calls, [])

    def test_healthcheck_scoped_enabled_dispatches_once(self):
        opscope.bind_scope(self.rid, "prof-ok")
        res, calls = self._exec(self.TOOL, {"profile_id": "prof-ok"})
        self.assertEqual(res.status, "ok", res.output)
        self.assertEqual(len(calls), 1)
        # executor re-pinned profile_id to the scope (run_id comes from context, not args)
        self.assertEqual(calls[0][1].get("profile_id"), "prof-ok")
        # second call in the same run → blocked (one health check per run)
        res2, calls2 = self._exec(self.TOOL, {"profile_id": "prof-ok"})
        self.assertEqual(res2.status, "blocked")
        self.assertEqual(res2.error, "healthcheck_already_used")
        self.assertEqual(calls2, [])

    def test_handler_takes_run_id_from_context_not_args(self):
        # point 2: the handler derives run_id from the runtime context (authoritative,
        # bound by the executor from request.run_id), IGNORING any model-supplied
        # run_id in args. Evidence lands under the context id, never the spoofed one.
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.tool_providers import itops_provider
        fake = unittest.mock.Mock()
        fake.stdout, fake.stderr, fake.returncode = b"labhost\n", b"", 0
        tok = set_current_run_id("ctx-run-xyz")
        try:
            with unittest.mock.patch("subprocess.run", return_value=fake):
                out = itops_provider.tool_itops_ssh_healthcheck(profile_id="prof-ok", run_id="SPOOFED")
        finally:
            reset_current_run_id(tok)
        self.assertTrue(out["ok"], out)
        self.assertTrue(itstore.list_evidence("ctx-run-xyz"))      # recorded under context id
        self.assertEqual(itstore.list_evidence("SPOOFED"), [])     # model-supplied id ignored

    def test_scoped_run_blocks_other_tools(self):
        # capability allowlist: a DIFFERENT tool in a scoped run is blocked in the
        # executor (not merely hidden), so the model cannot escape the scope.
        opscope.bind_scope(self.rid, "prof-ok")
        res, calls = self._exec("run_bash", {"command": "whoami"})
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.error, "scope_restricted")
        self.assertEqual(calls, [])

    def test_expired_scope_still_blocks_other_tools_and_healthcheck(self):
        # P2 regression: an expired TTL must NOT lift the lockdown for a still-open
        # run. Other tools stay blocked (sticky); the health check is blocked too
        # (no live scope). TTL only tightens, never loosens.
        opscope.bind_scope(self.rid, "prof-ok", ttl_seconds=-1)   # expired, still locked
        res, calls = self._exec("run_bash", {"command": "whoami"})
        self.assertEqual(res.error, "scope_restricted")           # still blocked
        self.assertEqual(calls, [])
        res2, calls2 = self._exec(self.TOOL, {"profile_id": "prof-ok"})
        self.assertEqual(res2.status, "blocked")
        self.assertEqual(res2.error, "no_operation_scope")        # health check needs live scope
        self.assertEqual(calls2, [])

    def test_unscoped_run_leaves_other_tools_untouched(self):
        # No scope on this run → the gate is inert; run_bash follows its normal path
        # (here: reaches its own approval gate, i.e. NOT scope_restricted).
        res, calls = self._exec("run_bash", {"command": "whoami"}, run_id="run-plain")
        self.assertNotEqual(res.error, "scope_restricted")


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
        itstore.upsert_asset(asset_id="ssh-draft", label="D", kind="linux", lifecycle_state="draft")
        itstore.put_connection_profile(profile_id="prof-draft", asset_id="ssh-draft",
                                       transport="ssh", ssh_alias="draftlab")
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._flag.stop()
        itstore._DB_PATH_OVERRIDE = None

    def test_start_binds_scope_for_enabled_profile(self):
        r = self.client.post("/api/itops/diagnostics/start", json={"profile_id": "prof-ok"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        run_id = body["run_id"]
        self.assertTrue(run_id.startswith("itops-diag-"))
        self.assertEqual(body["profile_id"], "prof-ok")
        self.assertIn("itops_ssh_healthcheck", body["message"])
        scope = opscope.get_active_scope(run_id)
        self.assertIsNotNone(scope)
        self.assertEqual(scope.profile_id, "prof-ok")
        self.assertEqual(scope.mode, "read_only")
        opscope.clear_scope(run_id)

    def test_start_refuses_draft_profile(self):
        r = self.client.post("/api/itops/diagnostics/start", json={"profile_id": "prof-draft"})
        self.assertEqual(r.status_code, 409)

    def test_start_refuses_unknown_profile(self):
        r = self.client.post("/api/itops/diagnostics/start", json={"profile_id": "prof-nope"})
        self.assertEqual(r.status_code, 404)

    def test_start_rejects_extra_field(self):
        r = self.client.post("/api/itops/diagnostics/start",
                             json={"profile_id": "prof-ok", "host": "evil"})
        self.assertEqual(r.status_code, 422)

    def test_flag_off_disables_route(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            r = self.client.post("/api/itops/diagnostics/start", json={"profile_id": "prof-ok"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
