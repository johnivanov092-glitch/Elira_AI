from __future__ import annotations

import hashlib
import json
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
from app.application.it_ops import config_inspect as ci  # noqa: E402
from app.infrastructure.it_ops import store as itstore  # noqa: E402


CFG = "itops_config_inspect"


def _proc(out=b"", err=b"", code=0):
    p = unittest.mock.Mock()
    p.stdout, p.stderr, p.returncode = out, err, code
    return p


class ConfigInspectParserTest(unittest.TestCase):
    def setUp(self):
        self.spec = ci.resolve_config("netdata-main")

    def test_comment_only_config_has_stable_hash_and_no_values(self):
        raw = b"# Netdata uses built-in defaults.\n"
        out = ci.inspect_bytes(self.spec, raw)
        self.assertEqual(out["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(out["comment_only"])
        self.assertEqual(out["safe_settings"], {})
        self.assertNotIn("raw", out)
        self.assertNotIn("content", out)

    def test_only_whitelisted_typed_value_survives(self):
        raw = (b"[global]\nupdate every = 1\napi token = TOP-SECRET\n"
               b"[web]\npassword = ALSO-SECRET\n")
        out = ci.inspect_bytes(self.spec, raw)
        self.assertEqual(out["safe_settings"], {"global.update_every": 1})
        encoded = json.dumps(out)
        self.assertNotIn("TOP-SECRET", encoded)
        self.assertNotIn("ALSO-SECRET", encoded)
        self.assertNotIn("api token", encoded)
        self.assertNotIn("password", encoded)

    def test_invalid_or_oversized_content_fails_closed(self):
        cases = (
            (b"\xff", "config_not_utf8"),
            (b"not-an-ini-line", "config_parse_failed"),
            (b"[global]\nupdate every = zero\n", "invalid_update_every"),
            (b"[global]\nupdate every = 61\n", "invalid_update_every"),
            (b"x" * (ci.MAX_CONFIG_BYTES + 1), "config_too_large"),
        )
        for raw, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(ci.ConfigInspectError) as ctx:
                ci.inspect_bytes(self.spec, raw)
            self.assertEqual(ctx.exception.reason, reason)


class ConfigInspectRouteTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.itops_routes import router
        from app.application import feature_flags as ff

        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        self._flag = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag.start()
        itstore.init_db()
        itstore.upsert_asset(asset_id="linux", label="Linux", kind="linux", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-linux", asset_id="linux",
                                       transport="ssh", ssh_alias="lab")
        itstore.upsert_asset(asset_id="win", label="Windows", kind="windows", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-win", asset_id="win",
                                       transport="ssh", ssh_alias="winbox")
        itstore.upsert_asset(asset_id="draft", label="Draft", kind="linux", lifecycle_state="draft")
        itstore.put_connection_profile(profile_id="prof-draft", asset_id="draft",
                                       transport="ssh", ssh_alias="draftlab")
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._flag.stop()
        itstore._DB_PATH_OVERRIDE = None

    def _start(self, **body):
        return self.client.post("/api/itops/config/inspect/start", json=body)

    def test_valid_request_binds_exact_named_config(self):
        r = self._start(profile_id="prof-linux", config_id="netdata-main")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["tool"], CFG)
        scope = opscope.get_active_scope(body["run_id"])
        self.assertEqual(scope.target_kind, "config_file")
        self.assertEqual(scope.profile_id, "prof-linux")
        self.assertEqual(scope.config.config_id, "netdata-main")
        self.assertEqual(scope.allowed_tool, CFG)
        opscope.clear_scope(body["run_id"])

    def test_target_and_profile_boundaries(self):
        self.assertEqual(self._start(profile_id="prof-linux", config_id="other").status_code, 422)
        self.assertEqual(self._start(profile_id="prof-win", config_id="netdata-main").status_code, 409)
        self.assertEqual(self._start(profile_id="prof-draft", config_id="netdata-main").status_code, 409)
        self.assertEqual(self._start(profile_id="missing", config_id="netdata-main").status_code, 404)
        self.assertEqual(self._start(profile_id="prof-linux", config_id="netdata-main",
                                     path="/etc/shadow").status_code, 422)

    def test_flag_off_is_404(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            self.assertEqual(self._start(profile_id="prof-linux", config_id="netdata-main").status_code, 404)


class ConfigInspectHandlerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        itstore.init_db()
        itstore.upsert_asset(asset_id="linux", label="Linux", kind="linux", lifecycle_state="enabled")
        itstore.put_connection_profile(profile_id="prof-linux", asset_id="linux",
                                       transport="ssh", ssh_alias="lab")
        itstore.upsert_asset(asset_id="draft", label="Draft", kind="linux", lifecycle_state="draft")
        itstore.put_connection_profile(profile_id="prof-draft", asset_id="draft",
                                       transport="ssh", ssh_alias="draftlab")
        self.rid = "config-run"
        opscope.bind_scope_config(self.rid, profile_id="prof-linux", config_id="netdata-main",
                                  allowed_tool=CFG)

    def tearDown(self):
        opscope.clear_scope(self.rid)
        itstore._DB_PATH_OVERRIDE = None

    def _run(self, proc, rid=None):
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.tool_providers import itops_provider
        token = set_current_run_id(rid or self.rid)
        try:
            with unittest.mock.patch("subprocess.run", return_value=proc) as sp:
                return itops_provider.tool_itops_config_inspect(), sp
        finally:
            reset_current_run_id(token)

    def test_success_persists_typed_projection_without_raw_secret(self):
        raw = b"[global]\nupdate every = 1\napi token = NEVER-EXPOSE\n"
        out, sp = self._run(_proc(raw, b"", 0))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["config"]["safe_settings"], {"global.update_every": 1})
        argv = sp.call_args.args[0]
        self.assertEqual(
            argv[-5:],
            ["/usr/bin/head", "-c", str(ci.MAX_CONFIG_BYTES + 1), "--",
             "/etc/netdata/netdata.conf"],
        )
        evidence = itstore.list_evidence(self.rid)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["operation"], "config_inspect:netdata-main")
        encoded = json.dumps(evidence[0]["result"])
        self.assertNotIn("NEVER-EXPOSE", encoded)
        self.assertNotIn("api token", encoded)
        self.assertNotIn("stdout", encoded)

    def test_parse_or_transport_failure_is_honest_and_evidenced(self):
        out, _ = self._run(_proc(b"[global]\nupdate every = bad\n", b"", 0))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "invalid_update_every")
        self.assertEqual(itstore.list_evidence(self.rid)[0]["result"]["status"], "failed")

        rid = "config-run-transport"
        opscope.bind_scope_config(rid, profile_id="prof-linux", config_id="netdata-main",
                                  allowed_tool=CFG)
        out2, _ = self._run(_proc(b"", b"Permission denied", 1), rid)
        self.assertFalse(out2["ok"])
        self.assertEqual(out2["error"], "inspect_failed")
        self.assertEqual(itstore.list_evidence(rid)[0]["exit_status"], "1")
        opscope.clear_scope(rid)

    def test_draft_profile_is_refused_before_ssh(self):
        rid = "config-run-draft"
        opscope.bind_scope_config(rid, profile_id="prof-draft", config_id="netdata-main",
                                  allowed_tool=CFG)
        out, sp = self._run(_proc(), rid)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "profile_not_enabled")
        sp.assert_not_called()
        self.assertEqual(itstore.list_evidence(rid), [])
        opscope.clear_scope(rid)


if __name__ == "__main__":
    unittest.main()
