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
from app.domain import it_ops as domain  # noqa: E402


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

    def test_revoke_deletes_then_marks_revoked(self):
        from app.infrastructure.secrets import vault
        ref = vault.put_secret(kind="token", value="tok-12345")
        vault.revoke(ref)
        self.assertNotIn(ref, self._cm)                    # deleted from vault first
        self.assertEqual(vault.state(ref)["lifecycle"], "revoked")
        with self.assertRaises(vault.SecretUnavailable):
            vault.resolve(ref)

    def test_revoke_infra_error_does_not_mark_revoked(self):
        # John's fix #2: a failed delete (infra error) must NOT look successful —
        # the error propagates and lifecycle stays NOT revoked.
        from app.infrastructure.secrets import vault, wincred
        ref = vault.put_secret(kind="token", value="tok-98765")
        with unittest.mock.patch.object(
                wincred, "delete_secret",
                side_effect=wincred.WinCredUnavailable("vault down")):
            with self.assertRaises(wincred.WinCredUnavailable):
                vault.revoke(ref)
        self.assertNotEqual(vault.state(ref)["lifecycle"], "revoked")

    def test_put_secret_atomic_orphan_deleted_on_metadata_failure(self):
        # John's fix #1: CredWrite succeeded but the metadata write raises →
        # compensating CredDelete removes the orphan; no credential remains.
        from app.infrastructure.secrets import vault
        with unittest.mock.patch.object(
                itstore, "put_secret_ref",
                side_effect=itstore.StoreUnavailable("db down")):
            with self.assertRaises(itstore.StoreUnavailable):
                vault.put_secret(kind="password", value="orphan-candidate-1")
        self.assertEqual(self._cm, {}, "orphan credential left in the vault")

    def test_resolve_unknown_ref_fails_closed(self):
        from app.infrastructure.secrets import vault
        with self.assertRaises(vault.SecretUnavailable):
            vault.resolve("sref_nonexistent")

    def test_short_and_whitespace_values_rejected_by_intake(self):
        from app.infrastructure.secrets import vault
        for bad in ("", "   ", "ab", " a "):
            with self.assertRaises(ValueError, msg=f"{bad!r} should be rejected"):
                vault.put_secret(kind="password", value=bad)


class WinCredHonestyTest(unittest.TestCase):
    """John's fix #2 at the wincred boundary: NOT_FOUND is distinct from an
    infrastructure/access error."""

    @unittest.skipUnless(sys.platform == "win32", "real Credential Manager only")
    def test_real_not_found_is_none_and_false(self):
        from app.infrastructure.secrets import wincred
        self.assertIsNone(wincred.read_secret("sref_definitely_absent_zzz"))
        self.assertFalse(wincred.delete_secret("sref_definitely_absent_zzz"))

    def test_non_not_found_error_raises(self):
        from app.infrastructure.secrets import wincred
        import ctypes
        # a fake advapi32 whose Cred* calls FAIL (return 0); MagicMock supports the
        # .argtypes/.restype assignment the wrapper does, and is callable.
        fake = unittest.mock.MagicMock()
        fake.CredReadW.return_value = 0
        fake.CredDeleteW.return_value = 0
        # a non-NOT_FOUND error code (e.g. ERROR_ACCESS_DENIED=5) must RAISE
        with unittest.mock.patch.object(wincred, "_advapi32", return_value=fake), \
             unittest.mock.patch.object(ctypes, "get_last_error", return_value=5):
            with self.assertRaises(wincred.WinCredUnavailable):
                wincred.read_secret("sref_x")
            with self.assertRaises(wincred.WinCredUnavailable):
                wincred.delete_secret("sref_x")
        # ERROR_NOT_FOUND (1168) → None / False, never an exception
        with unittest.mock.patch.object(wincred, "_advapi32", return_value=fake), \
             unittest.mock.patch.object(ctypes, "get_last_error",
                                        return_value=wincred.ERROR_NOT_FOUND):
            self.assertIsNone(wincred.read_secret("sref_x"))
            self.assertFalse(wincred.delete_secret("sref_x"))


class EnumEnforcementTest(_TempStore):
    """John's fix #3: the store validates enum values BEFORE SQL."""

    def test_invalid_asset_kind_and_lifecycle_rejected(self):
        with self.assertRaises(ValueError):
            itstore.upsert_asset(asset_id="a", label="A", kind="NOT_A_KIND")
        with self.assertRaises(ValueError):
            itstore.upsert_asset(asset_id="a", label="A", kind="linux",
                                 lifecycle_state="MADE_UP")

    def test_invalid_secret_kind_and_lifecycle_rejected(self):
        with self.assertRaises(ValueError):
            itstore.put_secret_ref(secret_ref="s", kind="TYPO")
        with self.assertRaises(ValueError):
            itstore.put_secret_ref(secret_ref="s", kind="password", lifecycle="NOT_A_STATE")

    def test_invalid_rollback_kind_and_status_axes_rejected(self):
        itstore.upsert_asset(asset_id="a", label="A", kind="linux")
        with self.assertRaises(ValueError):
            itstore.create_change_run(change_run_id="c", run_id="r", asset_id="a",
                                      rollback_kind="MAYBE")
        itstore.create_change_run(change_run_id="c2", run_id="r", asset_id="a")
        with self.assertRaises(ValueError):
            itstore.update_change_run("c2", change_run_status="NOT_A_STATE")
        with self.assertRaises(ValueError):
            itstore.update_change_run("c2", completion_status="MADE_UP")
        # cross-axis: a lifecycle value is NOT a valid completion_status
        with self.assertRaises(ValueError):
            itstore.update_change_run("c2", completion_status="rolled_back")


class MigrationRealTest(unittest.TestCase):
    """John's fix #4: real additive migration — an older shape migrates, keeps its
    row, and only then is the version stamped; an unrecoverable shape does not bump."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._path = str(Path(self._tmp) / "old.sqlite3")
        itstore._DB_PATH_OVERRIDE = self._path

    def tearDown(self):
        itstore._DB_PATH_OVERRIDE = None

    def test_v0_change_runs_missing_column_migrates_additively(self):
        # hand-build an OLD change_runs missing completion_status, user_version=0,
        # with an existing row.
        conn = sqlite3.connect(self._path)
        conn.executescript(
            "CREATE TABLE change_runs (change_run_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,"
            " asset_id TEXT NOT NULL, plan TEXT NOT NULL DEFAULT '{}', approval_id TEXT,"
            " snapshot_id TEXT, rollback_kind TEXT NOT NULL DEFAULT 'none',"
            " change_run_status TEXT NOT NULL DEFAULT 'planned', created_at REAL NOT NULL,"
            " updated_at REAL NOT NULL);")
        conn.execute("INSERT INTO change_runs (change_run_id, run_id, asset_id, created_at, updated_at)"
                     " VALUES ('old1','r','a',1.0,1.0)")
        conn.execute("PRAGMA user_version=0")
        conn.commit(); conn.close()

        itstore.init_db()                                  # additive migration

        cr = itstore.get_change_run("old1")
        self.assertIsNotNone(cr, "existing row must survive the migration")
        self.assertIsNone(cr["completion_status"])         # new column present, NULL
        conn = sqlite3.connect(self._path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(change_runs)").fetchall()}
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertIn("completion_status", cols)           # column added
        self.assertEqual(ver, itstore._SCHEMA_VERSION)     # stamped only after shape matches

    def test_unrecoverable_schema_raises_and_does_not_bump_version(self):
        # a change_runs table whose PK column has a conflicting type/shape that a
        # NOT NULL insert path can't reconcile → simulate by a wrong-typed table that
        # breaks the ALTER (use a reserved/duplicate). Here: make init fail by locking
        # the file into an incompatible object (a table named like an index target).
        conn = sqlite3.connect(self._path)
        # 'assets' created as something the CREATE-IF-NOT-EXISTS won't fix and whose
        # ADD COLUMN will fail: a table missing PK but with a NOT NULL no-default col
        # already populated differently is hard to force portably, so we assert the
        # honest-version contract via a forced sqlite error during migration.
        conn.executescript("CREATE TABLE assets (asset_id TEXT);")  # minimal old shape
        conn.execute("PRAGMA user_version=0")
        conn.commit(); conn.close()
        # patch ALTER to fail → migration must raise StoreUnavailable, version stays 0
        import app.infrastructure.it_ops.store as st
        orig_connect = st._connect

        class _FailingConn:
            def __init__(self, real): self._real = real
            def __getattr__(self, n): return getattr(self._real, n)
            def execute(self, sql, *a):
                if sql.strip().upper().startswith("ALTER TABLE"):
                    raise sqlite3.OperationalError("forced ALTER failure")
                return self._real.execute(sql, *a)
        with unittest.mock.patch.object(st, "_connect",
                                        side_effect=lambda: _FailingConn(orig_connect())):
            with self.assertRaises(st.StoreUnavailable):
                st.init_db()
        conn = sqlite3.connect(self._path)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertEqual(ver, 0, "version must NOT be bumped on a failed migration")


class StoreErrorContractTest(_TempStore):
    """John's fix #6: a sqlite error inside a PUBLIC store op becomes
    StoreUnavailable (not a raw sqlite error), with rollback/close guaranteed."""

    def test_public_op_wraps_sqlite_error(self):
        import app.infrastructure.it_ops.store as st

        class _BadConn:
            def execute(self, *a, **k): raise sqlite3.OperationalError("boom")
            def rollback(self): self.rolled_back = True
            def close(self): self.closed = True
        bad = _BadConn()
        with unittest.mock.patch.object(st, "_connect", return_value=bad):
            with self.assertRaises(st.StoreUnavailable):
                st.list_assets()                           # a real public operation
        self.assertTrue(getattr(bad, "rolled_back", False))
        self.assertTrue(getattr(bad, "closed", False))


class FlagApiTest(unittest.TestCase):
    """John's fix #5: PUT feature-flags name=itops must not 422 (Literal synced)."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.elira_state import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_put_itops_flag_not_422(self):
        from app.application import feature_flags as ff
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(ff, "CONFIG_PATH", Path(tmp) / "flags.json"):
            client = self._client()
            r = client.put("/api/elira/feature-flags", json={"name": "itops", "value": True})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json().get("itops"))
            # an unknown flag still 422s (Literal is a real allowlist)
            bad = client.put("/api/elira/feature-flags", json={"name": "bogus", "value": True})
            self.assertEqual(bad.status_code, 422)


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


class LeakClosingTest(unittest.TestCase):
    """Phase 0 prerequisite: close live secret-leak surfaces. Here: the
    GET /mcp/servers write-only redaction and the output canary."""

    def test_mcp_public_view_masks_env_and_secret_headers_keeps_keys(self):
        from app.application.tool_providers.mcp_runtime import public_server_view
        servers = [{
            "id": "github", "command": "npx", "args": ["-y", "x"],
            "env": {"GITHUB_TOKEN": "ghp_SUPERSECRET123456"},
            "secret_headers": {"Authorization": "Bearer tok_SECRET_789"},
            "url": "https://example.test", "status": "stopped",
        }]
        view = public_server_view(servers)[0]
        # values masked, keys kept
        self.assertEqual(view["env"], {"GITHUB_TOKEN": "●●●"})
        self.assertEqual(view["secret_headers"], {"Authorization": "●●●"})
        # no secret value anywhere in the serialized view
        import json as _j
        blob = _j.dumps(view, ensure_ascii=False)
        self.assertNotIn("ghp_SUPERSECRET123456", blob)
        self.assertNotIn("tok_SECRET_789", blob)
        # non-secret fields untouched
        self.assertEqual(view["id"], "github")
        self.assertEqual(view["url"], "https://example.test")

    def test_get_mcp_servers_route_masks_secrets_but_input_untouched(self):
        # Behavior test (no source inspection): call the real route with list_servers
        # patched to return a secret-bearing server; the RESPONSE must be masked,
        # while the raw list the launch path sees is left untouched.
        from app.api.routes import code_agent_routes as routes
        from app.application.tool_providers import mcp_runtime
        raw = [{"id": "github", "command": "npx",
                "env": {"GITHUB_TOKEN": "ghp_LEAKME_999"},
                "secret_headers": {"Authorization": "Bearer tok_LEAKME"}}]
        with unittest.mock.patch.object(mcp_runtime, "list_servers", return_value=raw):
            resp = routes.mcp_list_servers()
        body = __import__("json").dumps(resp, ensure_ascii=False)
        self.assertNotIn("ghp_LEAKME_999", body)          # value masked in the response
        self.assertNotIn("tok_LEAKME", body)
        self.assertEqual(resp["servers"][0]["env"], {"GITHUB_TOKEN": "●●●"})
        # public_server_view must not MUTATE its input (launch path keeps real env)
        self.assertEqual(raw[0]["env"], {"GITHUB_TOKEN": "ghp_LEAKME_999"})

    def test_output_canary_masks_resolved_value_anywhere(self):
        from app.core.redaction import mask_known_values, REDACTED
        secret = "resolved-P@ssw0rd-9931"
        text = f"connecting with {secret} to host — oddly-named-field={secret}"
        masked = mask_known_values(text, [secret])
        self.assertNotIn(secret, masked)
        self.assertEqual(masked.count(REDACTED), 2)
        # short values are ignored (avoid mangling unrelated text)
        self.assertEqual(mask_known_values("abc x abc", ["abc"]), "abc x abc")
        # non-str passes through
        self.assertEqual(mask_known_values(None, ["x"]), None)


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
