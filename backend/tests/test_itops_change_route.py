"""Main-backend change client + flag-gated routes for remote plan and local bypass."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.it_ops import change_client  # noqa: E402


class ChangeClientTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.tok = str(Path(self._tmp) / "ipc.token")
        Path(self.tok).write_text("boot-secret\n", encoding="utf-8")

    class _FakeSession:
        """Stand-in for requests.Session — records every post + honours trust_env/close."""
        posts: list = []

        def __init__(self):
            self.trust_env = True
            type(self).posts = []

        def post(self, url, json=None, headers=None, timeout=None, allow_redirects=None):
            type(self).posts.append(dict(url=url, headers=headers, json=json,
                                         timeout=timeout, allow_redirects=allow_redirects,
                                         trust_env=self.trust_env))

            class _Resp:
                def json(self_inner):
                    return {"ok": True, "change_run_id": "chg-1", "status": "pending_approval"}
            return _Resp()

        def close(self):
            pass

    def _patch_session(self):
        # patch requests.Session so _session() builds our fake but still sets trust_env=False
        return unittest.mock.patch.object(change_client.requests, "Session", self._FakeSession)

    def test_reads_token_and_posts_bearer_to_loopback(self):
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_IPC_TOKEN_FILE": self.tok,
                                                   "ELIRA_CHANGE_IPC_PORT": "8790"}), self._patch_session():
            out = change_client.request_plan("ai-server-netdata")
        self.assertEqual(out["change_run_id"], "chg-1")
        sent = self._FakeSession.posts[-1]
        self.assertEqual(sent["headers"]["Authorization"], "Bearer boot-secret")
        self.assertTrue(sent["url"].startswith("http://127.0.0.1:8790/request_plan"))
        self.assertEqual(sent["json"], {"target_id": "ai-server-netdata"})
        self.assertFalse(sent["trust_env"])          # proxy/netrc env ignored
        self.assertFalse(sent["allow_redirects"])    # a 3xx can't bounce the bearer off-box

    def test_apply_local_uses_distinct_loopback_path(self):
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_IPC_TOKEN_FILE": self.tok,
                                                   "ELIRA_CHANGE_IPC_PORT": "8790"}), self._patch_session():
            change_client.apply_local("ai-server-netdata")
        sent = self._FakeSession.posts[-1]
        self.assertTrue(sent["url"].endswith("/apply_local"))
        self.assertEqual(sent["json"], {"target_id": "ai-server-netdata"})
        self.assertEqual(sent["timeout"], change_client._LOCAL_APPLY_TIMEOUT)

    def test_apply_local_rejects_unknown_target_before_ipc(self):
        self._FakeSession.posts = []
        out = change_client.apply_local("arbitrary-command")
        self.assertEqual(out["error"], "target_not_allowed")
        self.assertEqual(self._FakeSession.posts, [])

    def test_missing_token_file_raises_unavailable(self):
        with unittest.mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            with self.assertRaises(change_client.ChangeExecutorUnavailable):
                change_client.request_plan("ai-server-netdata")

    def test_port_userinfo_injection_never_sends_bearer(self):
        # ELIRA_CHANGE_IPC_PORT=8790@attacker.example → URL host would be attacker.example.
        # The strict int guard must reject it BEFORE any request is built or token read.
        self._FakeSession.posts = []                    # the port guard raises before __init__ runs
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_IPC_TOKEN_FILE": self.tok,
                                                   "ELIRA_CHANGE_IPC_PORT": "8790@attacker.example"}), \
                self._patch_session():
            with self.assertRaises(change_client.ChangeExecutorUnavailable):
                change_client.request_plan("ai-server-netdata")
        self.assertEqual(self._FakeSession.posts, [])   # nothing ever sent

    def test_port_out_of_range_and_nonnumeric_rejected(self):
        for bad in ("0", "70000", "-1", "8790.0", "0x1", "  ", "८७९०"):
            with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_IPC_TOKEN_FILE": self.tok,
                                                       "ELIRA_CHANGE_IPC_PORT": bad}), self._patch_session():
                with self.assertRaises(change_client.ChangeExecutorUnavailable, msg=bad):
                    change_client.request_plan("ai-server-netdata")

    def test_http_proxy_env_is_ignored(self):
        # Even with HTTP(S)_PROXY set, the session must not trust env (else the bearer routes
        # through the proxy). trust_env=False is the guarantee.
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_IPC_TOKEN_FILE": self.tok,
                                                   "ELIRA_CHANGE_IPC_PORT": "8790",
                                                   "HTTP_PROXY": "http://attacker.example:3128",
                                                   "HTTPS_PROXY": "http://attacker.example:3128"}), \
                self._patch_session():
            change_client.request_plan("ai-server-netdata")
        self.assertFalse(self._FakeSession.posts[-1]["trust_env"])


class ChangeRouteTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.itops_routes import router
        from app.application import feature_flags as ff
        self._flag = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag.start()
        app = FastAPI()
        app.include_router(router)
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self._flag.stop()

    def test_plan_proxies_to_executor(self):
        with unittest.mock.patch("app.application.it_ops.change_client.request_plan",
                                 return_value={"ok": True, "change_run_id": "chg-1", "status": "pending_approval"}) as p:
            r = self.client.post("/api/itops/change/plan", json={"target_id": "ai-server-netdata"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["change_run_id"], "chg-1")
        p.assert_called_once_with("ai-server-netdata")

    def test_plan_503_when_executor_unavailable(self):
        with unittest.mock.patch("app.application.it_ops.change_client.request_plan",
                                 side_effect=change_client.ChangeExecutorUnavailable("down")):
            r = self.client.post("/api/itops/change/plan", json={"target_id": "x"})
        self.assertEqual(r.status_code, 503)

    def test_apply_local_proxies_without_remote_plan(self):
        with unittest.mock.patch("app.application.it_ops.change_client.apply_local",
                                 return_value={"ok": True, "change_run_id": "chg-local", "status": "applied"}) as p, \
                unittest.mock.patch("app.application.it_ops.change_client.request_plan") as remote:
            r = self.client.post("/api/itops/change/apply-local",
                                 json={"target_id": "ai-server-netdata"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "applied")
        p.assert_called_once_with("ai-server-netdata")
        remote.assert_not_called()

    def test_apply_local_rejects_non_allowlisted_target(self):
        r = self.client.post("/api/itops/change/apply-local",
                             json={"target_id": "arbitrary-command"})
        self.assertEqual(r.status_code, 400)

    def test_apply_local_is_loopback_only(self):
        from fastapi.testclient import TestClient
        remote = TestClient(self.app, client=("192.168.88.50", 50000))
        r = remote.post("/api/itops/change/apply-local",
                        json={"target_id": "ai-server-netdata"})
        self.assertEqual(r.status_code, 403)

    def test_plan_extra_field_422(self):
        r = self.client.post("/api/itops/change/plan", json={"target_id": "x", "unit": "sshd.service"})
        self.assertEqual(r.status_code, 422)     # client can't smuggle a unit/host/argv

    def test_status_proxies(self):
        with unittest.mock.patch("app.application.it_ops.change_client.get_status",
                                 return_value={"ok": True, "change_run_id": "chg-1", "status": "applied"}):
            r = self.client.get("/api/itops/change/chg-1/status")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "applied")

    def test_flag_off_404(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            self.assertEqual(self.client.post("/api/itops/change/plan",
                                              json={"target_id": "x"}).status_code, 404)
            self.assertEqual(self.client.post("/api/itops/change/apply-local",
                                              json={"target_id": "ai-server-netdata"}).status_code, 404)
            self.assertEqual(self.client.get("/api/itops/change/chg-1/status").status_code, 404)


class LocalChangeProviderTest(unittest.TestCase):
    def test_provider_rejects_unknown_target_before_ipc(self):
        from app.application.tool_providers.itops_provider import tool_itops_change_apply
        with unittest.mock.patch.object(change_client, "apply_local") as apply:
            out = tool_itops_change_apply("arbitrary-command")
        self.assertEqual(out["error"], "target_not_allowed")
        apply.assert_not_called()

    def test_provider_returns_capped_executor_status_and_evidence(self):
        from app.application.tool_providers.itops_provider import tool_itops_change_apply
        with unittest.mock.patch.object(
                change_client, "apply_local",
                return_value={"ok": True, "change_run_id": "chg-local", "status": "applied"}) as apply, \
                unittest.mock.patch.object(
                    change_client, "get_status",
                    return_value={"ok": True, "evidence": [{"operation": "systemd_change:postcheck"}]}) as status:
            out = tool_itops_change_apply("ai-server-netdata")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "applied")
        self.assertEqual(len(out["evidence"]), 1)
        apply.assert_called_once_with("ai-server-netdata")
        status.assert_called_once_with("chg-local")

    def test_remote_provider_requests_telegram_plan_without_local_apply(self):
        from app.application.tool_providers.itops_provider import tool_itops_change_apply
        with unittest.mock.patch(
                "app.application.agent_kernel.execution_context.get_execution_channel",
                return_value="remote"), \
                unittest.mock.patch.object(
                change_client, "request_plan",
                return_value={"ok": True, "change_run_id": "chg-remote",
                              "status": "pending_approval"}) as remote, \
                unittest.mock.patch.object(change_client, "apply_local") as local:
            out = tool_itops_change_apply("ai-server-netdata")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "pending_approval")
        remote.assert_called_once_with("ai-server-netdata")
        local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
