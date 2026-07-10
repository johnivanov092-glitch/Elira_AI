"""IT-Ops SSH vertical v1 — enrollment + verify for an existing OpenSSH alias.

Behavior tests. v1 never touches ~/.ssh/keys/known_hosts/agent/Credential Manager,
stores no secret (auth_ref=NULL), and grants the model NO host access (there is no
SSH allowlist step — enroll never touches it). enroll saves a `draft` asset; verify
runs by SAVED profile_id (never a browser host) with STRICT host-key checking and
promotes the asset to `enabled` only on exit 0; an unverified profile stays visible.
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

    def test_alias_rejects_metachars_and_option_injection(self):
        # regression: an alias must be a plain Host token, NEVER an ssh option.
        for bad in ("host; rm -rf /", "a b", "bad$(x)",
                    "-oProxyCommand=calc.exe", "-Fmy.config", "-oStrictHostKeyChecking=no",
                    "--", "-x", "a=b", "he|llo"):
            self.assertFalse(ssh_enroll.alias_ok(bad), f"{bad!r} must be rejected")
        for good in ("ubuntu-lab", "ai-server", "host.example.com", "h_1", "203.0.113.10"):
            self.assertTrue(ssh_enroll.alias_ok(good), f"{good!r} must be accepted")
        with self.assertRaises(ssh_enroll.SshEnrollError):
            ssh_enroll.resolve_alias("-oProxyCommand=x")

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
        from app.application import feature_flags as ff
        self._flag_patch = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag_patch.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._flag_patch.stop()
        itstore._DB_PATH_OVERRIDE = None

    _EFFECTIVE = {"ok": True, "hostname": "203.0.113.10", "port": "22", "user": "root",
                  "identity_files": ["~/.ssh/id_ed25519"]}
    _OBSERVED = {"ok": True, "fingerprints": ["256 SHA256:abc 203.0.113.10 (ED25519)"],
                 "note": "OBSERVED fingerprint only — NOT proof of identity."}

    def _enroll(self, alias="ubuntu-lab", label="Lab", body=None):
        payload = body if body is not None else {
            "label": label, "ssh_alias": alias, "fingerprint_reviewed": True}
        with unittest.mock.patch.object(ssh_enroll, "resolve_alias", return_value=self._EFFECTIVE), \
             unittest.mock.patch.object(ssh_enroll, "observe_fingerprint", return_value=self._OBSERVED):
            return self.client.post("/api/itops/ssh/enroll", json=payload)

    def test_enroll_saves_draft_unverified_no_secret(self):
        r = self._enroll()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["asset"]["lifecycle_state"], "draft")    # NOT enabled yet
        self.assertEqual(body["profile"]["last_health"]["status"], "unverified")
        self.assertIsNone(body["profile"]["auth_ref"])                 # no secret

    def test_enroll_does_not_touch_ssh_acl(self):
        # regression: Settings/API must NOT grant the model host access.
        from app.application.tool_providers import ssh_acl
        before = list(ssh_acl.get_allowed_hosts())
        with unittest.mock.patch.object(ssh_acl, "set_allowed_hosts") as setter:
            self._enroll(alias="never-allowlisted-xyz")
        setter.assert_not_called()
        self.assertEqual(ssh_acl.get_allowed_hosts(), before)          # allowlist unchanged

    def test_no_client_supplied_fingerprint_proof(self):
        # regression: a client-supplied 'confirmed_fingerprint' is rejected (extra
        # forbidden); the stored fingerprint is the SERVER-observed one, advisory.
        with unittest.mock.patch.object(ssh_enroll, "resolve_alias", return_value=self._EFFECTIVE), \
             unittest.mock.patch.object(ssh_enroll, "observe_fingerprint", return_value=self._OBSERVED):
            bad = self.client.post("/api/itops/ssh/enroll",
                                   json={"label": "L", "ssh_alias": "ubuntu-lab",
                                         "fingerprint_reviewed": True,   # otherwise valid…
                                         "confirmed_fingerprint": "SHA256:attacker"})
        self.assertEqual(bad.status_code, 422)                         # …extra field is rejected
        prof = self._enroll().json()["profile"]
        self.assertIn("SHA256:abc", prof["host_key_fingerprint"])      # server-observed value
        self.assertNotIn("attacker", prof["host_key_fingerprint"])

    def test_enroll_requires_fingerprint_reviewed_attestation(self):
        # regression: enroll is impossible without the user's out-of-band review
        # attestation. A missing or false flag is a 422 (Literal[True] enforced at the
        # API boundary); true → 200 and the attestation is recorded on the profile.
        missing = self._enroll(body={"label": "L", "ssh_alias": "ubuntu-lab"})
        self.assertEqual(missing.status_code, 422)                     # required
        false_flag = self._enroll(body={"label": "L", "ssh_alias": "ubuntu-lab",
                                         "fingerprint_reviewed": False})
        self.assertEqual(false_flag.status_code, 422)                  # must be exactly True
        ok = self._enroll(body={"label": "L", "ssh_alias": "ubuntu-lab",
                                "fingerprint_reviewed": True})
        self.assertEqual(ok.status_code, 200, ok.text)
        meta = ok.json()["profile"]["os_platform_meta"]
        self.assertIs(meta["fingerprint_reviewed"], True)             # audit trail

    def test_enroll_refuses_when_no_fingerprint_observed(self):
        # regression: you cannot attest to a key the server never observed. If a fresh
        # observe_fingerprint() yields nothing, enroll is a 409 BEFORE any write —
        # a fingerprint_reviewed=True over an empty observation is never persisted.
        unobserved = {"ok": False, "fingerprints": [], "error": "no host key observed"}
        with unittest.mock.patch.object(ssh_enroll, "resolve_alias", return_value=self._EFFECTIVE), \
             unittest.mock.patch.object(ssh_enroll, "observe_fingerprint", return_value=unobserved):
            r = self.client.post("/api/itops/ssh/enroll",
                                 json={"label": "L", "ssh_alias": "ubuntu-lab",
                                       "fingerprint_reviewed": True})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.client.get("/api/itops/assets").json()["assets"], [])  # nothing written

    def test_verify_success_enables_asset_failure_leaves_draft(self):
        # regression: asset is enabled ONLY after a successful SSH verify.
        pid = self._enroll().json()["profile"]["profile_id"]
        aid = self._enroll().json()["asset"]["asset_id"]  # same asset_id (ssh-<alias>)
        # failure first → stays draft
        with unittest.mock.patch.object(
                ssh_enroll, "verify_alias",
                return_value={"ok": False, "reason": "host key not trusted", "exit": 255}):
            f = self.client.post("/api/itops/verify", json={"profile_id": pid})
        self.assertFalse(f.json()["ok"])
        self.assertEqual(itstore.get_asset(aid)["lifecycle_state"], "draft")
        self.assertEqual(f.json()["health"]["status"], "unverified")
        self.assertIn("host key", f.json()["health"]["reason"])
        # success → enabled
        with unittest.mock.patch.object(
                ssh_enroll, "verify_alias",
                return_value={"ok": True, "output": "labhost", "exit": 0}):
            ok = self.client.post("/api/itops/verify", json={"profile_id": pid})
        self.assertTrue(ok.json()["ok"])
        self.assertEqual(itstore.get_asset(aid)["lifecycle_state"], "enabled")
        self.assertEqual(ok.json()["health"]["status"], "verified")

    def test_verify_rejects_unknown_profile_and_never_takes_a_host(self):
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
