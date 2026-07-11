"""Main-backend change client + the flag-gated proxy route. The main backend only forwards
a target_id and reads capped status — it never plans/approves/applies or holds keys."""
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

    def test_reads_token_and_posts_bearer_to_loopback(self):
        captured: dict = {}

        class _Resp:
            def json(self_inner):
                return {"ok": True, "change_run_id": "chg-1", "status": "pending_approval"}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(url=url, headers=headers, json=json)
            return _Resp()

        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_IPC_TOKEN_FILE": self.tok,
                                                   "ELIRA_CHANGE_IPC_PORT": "8790"}), \
                unittest.mock.patch.object(change_client.requests, "post", side_effect=fake_post):
            out = change_client.request_plan("ai-server-netdata")
        self.assertEqual(out["change_run_id"], "chg-1")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer boot-secret")
        self.assertTrue(captured["url"].startswith("http://127.0.0.1:8790/request_plan"))
        self.assertEqual(captured["json"], {"target_id": "ai-server-netdata"})

    def test_missing_token_file_raises_unavailable(self):
        with unittest.mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            with self.assertRaises(change_client.ChangeExecutorUnavailable):
                change_client.request_plan("ai-server-netdata")


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
            self.assertEqual(self.client.get("/api/itops/change/chg-1/status").status_code, 404)


if __name__ == "__main__":
    unittest.main()
