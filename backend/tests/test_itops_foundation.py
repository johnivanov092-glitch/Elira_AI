"""Phase 0 (Foundation) — increment 1: it_ops store + itops flag.

Behavior tests, not label checks: the two status axes are INDEPENDENT columns,
migrations are forward-only + idempotent, the secret_refs table holds no value,
and the itops flag is OFF by default. (Vault / intake / leak-closing / UI arrive
in later Phase-0 increments.)
"""
from __future__ import annotations

import sqlite3
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
from app.application.it_ops import domain  # noqa: E402


class _TempStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")
        itstore.init_db()

    def tearDown(self):
        itstore._DB_PATH_OVERRIDE = None


class SchemaTest(_TempStore):
    def test_all_six_tables_plus_secret_refs_exist(self):
        conn = sqlite3.connect(itstore._DB_PATH_OVERRIDE)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        for t in ("assets", "connection_profiles", "operation_scopes", "snapshots",
                  "evidence", "change_runs", "secret_refs"):
            self.assertIn(t, names)

    def test_asset_round_trip(self):
        itstore.upsert_asset(asset_id="ubuntu-client-01", label="Lab Ubuntu",
                             kind="linux", endpoint="203.0.113.10", tags=["lab"],
                             owner_scope="scope-x")
        a = itstore.get_asset("ubuntu-client-01")
        self.assertEqual(a["kind"], "linux")
        self.assertEqual(a["tags"], ["lab"])
        self.assertEqual(len(itstore.list_assets()), 1)


class TwoAxisTest(_TempStore):
    def test_status_axes_are_independent_columns(self):
        # John's review 1: completion_status (task) and change_run_status
        # (lifecycle) are SEPARATE — a row holds both without coercion.
        itstore.upsert_asset(asset_id="a1", label="A", kind="linux")
        itstore.create_change_run(change_run_id="cr1", run_id="run1", asset_id="a1",
                                  rollback_kind="automatic")
        cr = itstore.get_change_run("cr1")
        self.assertEqual(cr["change_run_status"], "planned")
        self.assertIsNone(cr["completion_status"])
        # verifier red + automatic rollback succeeded
        itstore.update_change_run("cr1", completion_status="failed",
                                  change_run_status="rolled_back")
        cr = itstore.get_change_run("cr1")
        self.assertEqual(cr["completion_status"], "failed")
        self.assertEqual(cr["change_run_status"], "rolled_back")
        # verifier red + rollback failed → the OTHER lifecycle terminal, same task axis
        itstore.update_change_run("cr1", change_run_status="rollback_failed")
        cr = itstore.get_change_run("cr1")
        self.assertEqual(cr["completion_status"], "failed")      # unchanged
        self.assertEqual(cr["change_run_status"], "rollback_failed")

    def test_applied_pending_verification_terminal(self):
        itstore.upsert_asset(asset_id="a2", label="A", kind="linux")
        itstore.create_change_run(change_run_id="cr2", run_id="run2", asset_id="a2")
        itstore.update_change_run("cr2", change_run_status="applied_pending_verification",
                                  completion_status="partial")
        cr = itstore.get_change_run("cr2")
        self.assertEqual(cr["change_run_status"], "applied_pending_verification")
        self.assertEqual(cr["completion_status"], "partial")

    def test_domain_enums_cover_both_axes(self):
        self.assertIn("rollback_failed", domain.CHANGE_RUN_STATUS)
        self.assertIn("applied_pending_verification", domain.CHANGE_RUN_STATUS)
        self.assertEqual(set(domain.COMPLETION_STATUS),
                         {"confirmed", "partial", "failed", "unverified", "n/a"})
        # the lifecycle axis must NOT contain task-axis values (no axis mixing)
        for v in ("confirmed", "unverified", "n/a"):
            self.assertNotIn(v, domain.CHANGE_RUN_STATUS)


class SecretRefStateTest(_TempStore):
    def test_secret_refs_table_has_no_value_column(self):
        # the value lives ONLY in Windows Credential Manager
        conn = sqlite3.connect(itstore._DB_PATH_OVERRIDE)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(secret_refs)").fetchall()}
        conn.close()
        for forbidden in ("value", "secret", "ciphertext", "dpapi_blob", "password", "blob"):
            self.assertNotIn(forbidden, cols, f"secret_refs must not have a {forbidden!r} column")
        self.assertEqual(cols, {"secret_ref", "backend", "kind", "asset_id",
                                "lifecycle", "created_at", "rotated_at", "revoked_at"})

    def test_secret_ref_state_never_returns_a_value(self):
        itstore.put_secret_ref(secret_ref="sref_abc", kind="password", asset_id="a1")
        st = itstore.secret_ref_state("sref_abc")
        self.assertEqual(st["lifecycle"], "temporary")
        self.assertEqual(st["backend"], "wincred")
        self.assertNotIn("value", st)
        itstore.mark_secret_revoked("sref_abc")
        self.assertEqual(itstore.secret_ref_state("sref_abc")["lifecycle"], "revoked")


class MigrationTest(_TempStore):
    def test_init_db_idempotent_forward_only(self):
        # running init twice must not drop or duplicate; user_version is a ladder
        itstore.upsert_asset(asset_id="keep", label="K", kind="linux")
        itstore.init_db()
        itstore.init_db()
        self.assertIsNotNone(itstore.get_asset("keep"))     # row survived
        conn = sqlite3.connect(itstore._DB_PATH_OVERRIDE)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertEqual(ver, itstore._SCHEMA_VERSION)

    def test_fail_soft_on_bad_path(self):
        itstore._DB_PATH_OVERRIDE = "\x00::not-a-valid-path::"
        with self.assertRaises(itstore.StoreUnavailable):
            itstore._connect()


class VaultTest(_TempStore):
    """Secret vault: value lives ONLY in Windows Credential Manager; the it_ops DB
    holds only state. Credential Manager is faked in-memory so tests run headless."""

    def setUp(self):
        super().setUp()
        from app.infrastructure.secrets import wincred
        self._cm: dict[str, str] = {}
        self._patches = [
            unittest.mock.patch.object(wincred, "write_secret",
                                       side_effect=lambda ref, val: self._cm.__setitem__(ref, val)),
            unittest.mock.patch.object(wincred, "read_secret",
                                       side_effect=lambda ref: self._cm.get(ref)),
            unittest.mock.patch.object(wincred, "delete_secret",
                                       side_effect=lambda ref: self._cm.pop(ref, None) is not None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        super().tearDown()

    def test_put_resolve_round_trip_value_only_in_cred_manager(self):
        from app.infrastructure.secrets import vault
        SECRET = "S3cr3t-P@ss-DO-NOT-LEAK-4242"
        ref = vault.put_secret(kind="password", value=SECRET, asset_id="a1")
        self.assertTrue(ref.startswith("sref_"))
        self.assertEqual(self._cm[ref], SECRET)            # in Credential Manager
        self.assertEqual(vault.resolve(ref), SECRET)       # runtime resolve works
        # the value must NOT appear anywhere in the it_ops SQLite file
        blob = Path(itstore._DB_PATH_OVERRIDE).read_bytes()
        self.assertNotIn(SECRET.encode("utf-8"), blob, "secret value leaked into it_ops DB")
        # state carries no value
        st = vault.state(ref)
        self.assertEqual(st["kind"], "password")
        self.assertNotIn(SECRET, str(st))

    def test_revoke_fails_closed(self):
        from app.infrastructure.secrets import vault
        ref = vault.put_secret(kind="token", value="tok-123")
        vault.revoke(ref)
        self.assertEqual(vault.state(ref)["lifecycle"], "revoked")
        with self.assertRaises(vault.SecretUnavailable):
            vault.resolve(ref)

    def test_resolve_unknown_ref_fails_closed(self):
        from app.infrastructure.secrets import vault
        with self.assertRaises(vault.SecretUnavailable):
            vault.resolve("sref_nonexistent")

    def test_empty_value_rejected(self):
        from app.infrastructure.secrets import vault
        with self.assertRaises(ValueError):
            vault.put_secret(kind="password", value="")


class VaultRealCredManagerTest(_TempStore):
    """A real Windows Credential Manager round-trip (Windows-only; cleans up)."""

    @unittest.skipUnless(sys.platform == "win32", "Credential Manager is Windows-only")
    def test_real_write_read_delete(self):
        from app.infrastructure.secrets import wincred, vault
        self.assertTrue(wincred.available())
        ref = vault.put_secret(kind="password", value="real-roundtrip-777")
        try:
            self.assertEqual(vault.resolve(ref), "real-roundtrip-777")
        finally:
            vault.revoke(ref)
        with self.assertRaises(vault.SecretUnavailable):
            vault.resolve(ref)


class FlagTest(unittest.TestCase):
    def test_itops_flag_registered_and_off_by_default(self):
        from app.application import feature_flags as ff
        self.assertIn("itops", ff._ENV_VAR)
        # registered in the canonical set and OFF unless explicitly enabled
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELIRA_ITOPS", None)
            flags = ff.get_flags()
            self.assertIn("itops", flags)
            self.assertFalse(flags["itops"])


if __name__ == "__main__":
    unittest.main()
