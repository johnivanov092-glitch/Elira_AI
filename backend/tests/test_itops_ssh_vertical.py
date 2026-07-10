"""IT-Ops SSH vertical v1 — enrollment + verify for an existing OpenSSH alias.

Behavior tests. v1 never touches ~/.ssh/keys/known_hosts/agent/Credential Manager
and stores no secret; the allowlist grows only after an explicit fingerprint
confirm; verify runs by SAVED profile_id (never a browser host) with STRICT
host-key checking; an unverified profile stays visible.
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

from app.infrastructure.it_ops import store as itstore  # noqa: E402
from app.application.it_ops import ssh_enroll  # noqa: E402


def _completed(stdout=b"", stderr=b"", code=0):
    m = unittest.mock.Mock()
    m.stdout, m.stderr, m.returncode = stdout, stderr, code
    return m


class SshEnrollUnitTest(unittest.TestCase):
    def test_resolve_alias_parses_ssh_G(self):
        out = b"hostname 203.0.113.10\nport 2222\nuser root\nidentityfile ~/.ssh/id_ed25519\n"
        with unittest.mock.patch("subprocess.run", return_value=_completed(stdout=out)):
            r = ssh_enroll.resolve_alias("ubuntu-lab")
        self.assertTrue(r["ok"])
        self.assertEqual(r["hostname"], "203.0.113.10")
        self.assertEqual(r["port"], "2222")
        self.assertEqual(r["user"], "root")
        self.assertIn("~/.ssh/id_ed25519", r["identity_files"])

    def test_alias_with_metachars_rejected(self):
        self.assertFalse(ssh_enroll.alias_ok("host; rm -rf /"))
        self.assertFalse(ssh_enroll.alias_ok("a b"))
        with self.assertRaises(ssh_enroll.SshEnrollError):
            ssh_enroll.resolve_alias("bad$(x)")

    def test_verify_uses_batchmode_and_strict_hostkey(self):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return _completed(stdout=b"labhost\n", code=0)
        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            r = ssh_enroll.verify_alias("ubuntu-lab")
        self.assertTrue(r["ok"])
        self.assertEqual(r["output"], "labhost")
        self.assertIn("BatchMode=yes", seen["argv"])
        self.assertIn("StrictHostKeyChecking=yes", seen["argv"])
        self.assertNotIn("StrictHostKeyChecking=accept-new", seen["argv"])
        self.assertNotIn("sshpass", seen["argv"])

    def test_verify_host_key_untrusted_gives_clear_reason(self):
        err = b"Host key verification failed.\n"
        with unittest.mock.patch("subprocess.run", return_value=_completed(stderr=err, code=255)):
            r = ssh_enroll.verify_alias("ubuntu-lab")
        self.assertFalse(r["ok"])
        self.assertIn("known_hosts", r["reason"])

    def test_observe_fingerprint_is_advisory(self):
        keyscan = b"203.0.113.10 ssh-ed25519 AAAA...\n"
        fp = b"256 SHA256:abc123 203.0.113.10 (ED25519)\n"
        with unittest.mock.patch("subprocess.run",
                                 side_effect=[_completed(stdout=keyscan), _completed(stdout=fp)]):
            r = ssh_enroll.observe_fingerprint("203.0.113.10", "22")
        self.assertTrue(r["ok"])
        self.assertTrue(any("SHA256:abc123" in f for f in r["fingerprints"]))
        self.assertIn("NOT proof", r["note"])


class SshVerticalRouteTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.itops_routes import router
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        # isolate the SSH allowlist file
        from app.application.tool_providers import ssh_acl
        self._acl = ssh_acl
        self._acl_patch = unittest.mock.patch.object(
            ssh_acl, "ACL_PATH", Path(self._tmp) / "ssh_acl.json")
        self._acl_patch.start()
        # flag ON for these tests
        from app.application import feature_flags as ff
        self._flag_patch = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag_patch.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._flag_patch.stop()
        self._acl_patch.stop()
        itstore._DB_PATH_OVERRIDE = None

    _EFFECTIVE = {"ok": True, "hostname": "203.0.113.10", "port": "22", "user": "root",
                  "identity_files": ["~/.ssh/id_ed25519"]}

    def test_enroll_requires_confirm_and_allowlists_only_after(self):
        with unittest.mock.patch.object(ssh_enroll, "resolve_alias", return_value=self._EFFECTIVE):
            # missing confirmation → 400, and NOT allowlisted
            r = self.client.post("/api/itops/ssh/enroll",
                                 json={"label": "Lab", "ssh_alias": "ubuntu-lab",
                                       "confirmed_fingerprint": ""})
            self.assertEqual(r.status_code, 400)
            self.assertNotIn("ubuntu-lab", self._acl.get_allowed_hosts())
            # with confirmation → saved unverified + allowlisted
            ok = self.client.post("/api/itops/ssh/enroll",
                                  json={"label": "Lab", "ssh_alias": "ubuntu-lab",
                                        "confirmed_fingerprint": "SHA256:abc123"})
            self.assertEqual(ok.status_code, 200, ok.text)
        body = ok.json()
        self.assertEqual(body["profile"]["last_health"]["status"], "unverified")
        self.assertIsNone(body["profile"]["auth_ref"])                 # no secret stored
        self.assertEqual(body["profile"]["host_key_fingerprint"], "SHA256:abc123")
        self.assertIn("ubuntu-lab", self._acl.get_allowed_hosts())     # allowlisted only now

    def test_verify_by_profile_id_only_success_updates_health(self):
        with unittest.mock.patch.object(ssh_enroll, "resolve_alias", return_value=self._EFFECTIVE):
            prof = self.client.post("/api/itops/ssh/enroll",
                                    json={"label": "Lab", "ssh_alias": "ubuntu-lab",
                                          "confirmed_fingerprint": "SHA256:abc123"}).json()["profile"]
        pid = prof["profile_id"]
        with unittest.mock.patch.object(ssh_enroll, "verify_alias",
                                        return_value={"ok": True, "output": "labhost", "exit": 0}):
            r = self.client.post("/api/itops/verify", json={"profile_id": pid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["health"]["status"], "verified")
        # persisted
        assets = self.client.get("/api/itops/assets").json()["assets"]
        prof_now = assets[0]["profiles"][0]
        self.assertEqual(prof_now["last_health"]["status"], "verified")

    def test_verify_failure_leaves_unverified_with_reason(self):
        with unittest.mock.patch.object(ssh_enroll, "resolve_alias", return_value=self._EFFECTIVE):
            pid = self.client.post("/api/itops/ssh/enroll",
                                   json={"label": "Lab", "ssh_alias": "ubuntu-lab",
                                         "confirmed_fingerprint": "SHA256:abc123"}).json()["profile"]["profile_id"]
        with unittest.mock.patch.object(
                ssh_enroll, "verify_alias",
                return_value={"ok": False, "reason": "host key not trusted", "exit": 255}):
            r = self.client.post("/api/itops/verify", json={"profile_id": pid})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(r.json()["health"]["status"], "unverified")
        self.assertIn("host key", r.json()["health"]["reason"])

    def test_verify_rejects_unknown_profile_and_never_takes_a_host(self):
        # unknown profile id → 404; there is no way to pass a raw host at all
        r = self.client.post("/api/itops/verify", json={"profile_id": "prof-nope"})
        self.assertEqual(r.status_code, 404)
        bad = self.client.post("/api/itops/verify", json={"host": "evil.example"})
        self.assertEqual(bad.status_code, 422)                         # host is not an accepted field

    def test_flag_off_disables_routes(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            for method, path, body in (
                    ("post", "/api/itops/ssh/preview", {"ssh_alias": "x"}),
                    ("post", "/api/itops/verify", {"profile_id": "p"}),
                    ("get", "/api/itops/assets", None)):
                resp = (self.client.get(path) if method == "get"
                        else self.client.post(path, json=body))
                self.assertEqual(resp.status_code, 404, f"{path} should be disabled when flag off")


if __name__ == "__main__":
    unittest.main()
