"""Hardened loopback IPC: a per-boot bearer token (constant-time) gates the two calls; a
missing/wrong token makes NO engine call; body is capped 4 KiB JSON; only two paths; the
token never appears in a response."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import service, store as cs, transport  # noqa: E402

_TOKEN = "boot-secret-abc123"
_PROP = {"id": "Id", "load_state": "LoadState", "active_state": "ActiveState",
         "sub_state": "SubState", "main_pid": "MainPID"}


def _show(pid):
    f = {"id": "netdata.service", "load_state": "loaded", "active_state": "active",
         "sub_state": "running", "main_pid": pid}
    return ("\n".join(f"{_PROP[k]}={v}" for k, v in f.items()) + "\n").encode()


class FakeRunner:
    def __call__(self, argv, timeout):
        return transport.SshResult(0, _show(1238), b"")


class FakeSender:
    def __init__(self):
        self.sent = []

    def is_configured(self):
        return True

    def send_approval(self, **kw):
        self.sent.append(kw)


class IpcAuthTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        cs._DB_PATH_OVERRIDE = str(Path(self._tmp) / "change.sqlite3")
        cs.init_db()
        self.reg = str(Path(self._tmp) / "registry.json")
        Path(self.reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "h", "port": 22, "remote_user": "elira-change", "known_hosts": "/kh",
            "identity_file": "/id", "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")
        self.sender = FakeSender()

    def tearDown(self):
        cs._DB_PATH_OVERRIDE = None

    def _count(self):
        conn = cs._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM change_runs").fetchone()[0]
        finally:
            conn.close()

    def _req(self, auth, body, path="/request_plan"):
        return service.handle_request(path, auth, body if body is None else json.dumps(body).encode(),
                                       token=_TOKEN, sender=self.sender, rate_limiter=None,
                                       registry_path=self.reg, runner=FakeRunner())

    def test_missing_token_no_engine_call(self):
        code, res = self._req(None, {"target_id": "ai-server-netdata"})
        self.assertEqual(code, 401)
        self.assertEqual(res["error"], "unauthorized")
        self.assertEqual(self._count(), 0)          # request_plan never ran
        self.assertEqual(self.sender.sent, [])

    def test_wrong_token_no_engine_call(self):
        code, res = self._req("Bearer WRONG", {"target_id": "ai-server-netdata"})
        self.assertEqual(code, 401)
        self.assertEqual(self._count(), 0)

    def test_wrong_scheme_rejected(self):
        code, _ = self._req(f"Basic {_TOKEN}", {"target_id": "ai-server-netdata"})
        self.assertEqual(code, 401)

    def test_correct_token_runs_and_hides_token(self):
        code, res = self._req(f"Bearer {_TOKEN}", {"target_id": "ai-server-netdata"})
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(set(res.keys()), {"ok", "change_run_id", "status"})
        self.assertNotIn(_TOKEN, json.dumps(res))   # token never in a response
        self.assertEqual(self._count(), 1)

    def test_body_too_large(self):
        big = json.dumps({"target_id": "x" * 5000}).encode()
        code, res = service.handle_request("/request_plan", f"Bearer {_TOKEN}", big, token=_TOKEN,
                                           sender=self.sender, rate_limiter=None, registry_path=self.reg)
        self.assertEqual(code, 413)
        self.assertEqual(self._count(), 0)

    def test_bad_json(self):
        code, _ = service.handle_request("/request_plan", f"Bearer {_TOKEN}", b"{not json",
                                         token=_TOKEN, sender=self.sender, rate_limiter=None,
                                         registry_path=self.reg)
        self.assertEqual(code, 400)

    def test_unknown_path(self):
        code, _ = self._req(f"Bearer {_TOKEN}", {}, path="/apply")   # no such route
        self.assertEqual(code, 404)

    def test_get_status_requires_token(self):
        cid = self._req(f"Bearer {_TOKEN}", {"target_id": "ai-server-netdata"})[1]["change_run_id"]
        code, res = self._req(None, {"change_run_id": cid}, path="/get_status")
        self.assertEqual(code, 401)
        code, res = self._req(f"Bearer {_TOKEN}", {"change_run_id": cid}, path="/get_status")
        self.assertEqual(code, 200)
        self.assertEqual(res["change_run_id"], cid)

    def test_bind_host_is_hardcoded_loopback(self):
        self.assertEqual(service._BIND_HOST, "127.0.0.1")   # never a parameter

    def test_socket_level_bearer_enforced(self):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer

        def post(base, path, body, auth=None):
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["Authorization"] = auth
            req = urllib.request.Request(f"{base}{path}", data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode()
            except (urllib.error.URLError, OSError):
                return 0, ""     # connection dropped (e.g. oversized body rejected before read)

        server = ThreadingHTTPServer(("127.0.0.1", 0), service._make_handler(
            _TOKEN, self.sender, None, self.reg, runner=FakeRunner()))
        base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        good = json.dumps({"target_id": "ai-server-netdata"}).encode()
        try:
            # no bearer → 401, engine never ran
            code, _ = post(base, "/request_plan", good)
            self.assertEqual(code, 401)
            self.assertEqual(self._count(), 0)
            # wrong bearer → 401
            code, _ = post(base, "/request_plan", good, auth="Bearer nope")
            self.assertEqual(code, 401)
            self.assertEqual(self._count(), 0)
            # valid bearer → 200, engine ran, token not echoed
            code, text = post(base, "/request_plan", good, auth=f"Bearer {_TOKEN}")
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(text)["ok"])
            self.assertEqual(self._count(), 1)
            self.assertNotIn(_TOKEN, text)
            # >4 KiB rejected BEFORE the body is read — 413, or the connection is dropped
            # mid-send (either way the server never processed it) — and NO engine call.
            code, _ = post(base, "/request_plan", b"x" * 5000, auth=f"Bearer {_TOKEN}")
            self.assertIn(code, (413, 0))
            self.assertEqual(self._count(), 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
