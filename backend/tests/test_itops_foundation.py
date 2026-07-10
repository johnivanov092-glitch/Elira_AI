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
        self.assertEqual(cols, {"secret_ref", "backend", "kind", "asset_id", "lifecycle",
                                "origin", "recovery_attempts", "last_recovery_at",
                                "created_at", "rotated_at", "revoked_at"})

    def test_secret_ref_state_never_returns_a_value(self):
        # default lifecycle is now 'provisioning' (the saga writes the recovery
        # record BEFORE the credential exists).
        itstore.put_secret_ref(secret_ref="sref_abc", kind="password", asset_id="a1")
        st = itstore.secret_ref_state("sref_abc")
        self.assertEqual(st["lifecycle"], "provisioning")
        self.assertEqual(st["backend"], "wincred")
        self.assertNotIn("value", st)
        itstore.set_secret_lifecycle("sref_abc", "temporary")   # complete it
        self.assertEqual(itstore.secret_ref_state("sref_abc")["lifecycle"], "temporary")
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

    def test_saga_store_fails_before_credwrite_no_credential(self):
        # Recovery record is written FIRST; if THAT fails, no credential is written.
        from app.infrastructure.secrets import vault
        with unittest.mock.patch.object(
                itstore, "put_secret_ref",
                side_effect=itstore.StoreUnavailable("db down")):
            with self.assertRaises(itstore.StoreUnavailable):
                vault.put_secret(kind="password", value="never-written-1")
        self.assertEqual(self._cm, {}, "no credential must exist if the record failed first")

    def test_saga_finalize_store_down_leaves_TRACKED_credential(self):
        # CredWrite ok, the CONDITIONAL finalize hits a store outage → the credential
        # remains but ONLY as a durable TRACKED provisioning record (recovery owns
        # teardown). Never an untracked credential; never a false success.
        from app.infrastructure.secrets import vault
        with unittest.mock.patch.object(
                itstore, "finalize_provisioning",
                side_effect=itstore.StoreUnavailable("db down")):
            with self.assertRaises(vault.VaultProvisioningError) as ctx:
                vault.put_secret(kind="password", value="tracked-not-orphan-1")
        ref = ctx.exception.secret_ref
        self.assertNotIn("tracked-not-orphan-1", str(ctx.exception))   # no value in error
        self.assertEqual(list(self._cm), [ref])                        # credential remained
        self.assertEqual(vault.state(ref)["lifecycle"], "provisioning")  # TRACKED
        with self.assertRaises(vault.SecretUnavailable):               # not resolvable
            vault.resolve(ref)

    def test_interleaving_recovery_claims_before_finalize_no_false_success(self):
        # RACE regression: credential created → a recovery pass CLAIMS and tears down
        # the record BEFORE put_secret finalizes. put_secret's conditional finalize
        # must fail (state conflict) — never a successful put_secret with a
        # deleted credential/ref.
        from app.infrastructure.secrets import vault, wincred

        def racing_write(ref, val):
            self._cm[ref] = val
            # recovery wins the race: claim (provisioning→recovering), delete cred+record
            self.assertTrue(itstore.claim_for_recovery(ref))
            self._cm.pop(ref, None)
            self.assertTrue(itstore.delete_claimed_recovery(ref))

        with unittest.mock.patch.object(wincred, "write_secret", side_effect=racing_write):
            with self.assertRaises(vault.VaultProvisioningError) as ctx:
                vault.put_secret(kind="password", value="raced-secret-1")
        self.assertIn("state conflict", str(ctx.exception))
        # no live credential and no live ref survived a "successful" put_secret
        self.assertEqual(self._cm, {})
        self.assertIsNone(vault.state(ctx.exception.secret_ref))

    def test_recovery_cleans_incomplete_records(self):
        # The recovery path claims, deletes the credential, and removes the record.
        from app.infrastructure.secrets import vault
        with unittest.mock.patch.object(
                itstore, "finalize_provisioning",
                side_effect=itstore.StoreUnavailable("db down")):
            try:
                vault.put_secret(kind="password", value="to-recover-1")
            except vault.VaultProvisioningError as e:
                ref = e.secret_ref
        self.assertEqual(vault.state(ref)["lifecycle"], "provisioning")  # incomplete
        summary = vault.recover_incomplete_secrets()
        self.assertIn(ref, summary["cleaned"])
        self.assertEqual(self._cm, {})                    # credential deleted
        self.assertIsNone(vault.state(ref))               # record removed


    def test_resolve_unknown_ref_fails_closed(self):
        from app.infrastructure.secrets import vault
        with self.assertRaises(vault.SecretUnavailable):
            vault.resolve("sref_nonexistent")

    def test_any_non_empty_value_accepted_only_empty_rejected(self):
        # John's fix #4: MIN_SECRET_LEN removed — accept ANY non-empty string
        # verbatim (short/whitespace ok); only "" is rejected.
        from app.infrastructure.secrets import vault
        for good in ("x", "ab", "   ", " a "):
            ref = vault.put_secret(kind="password", value=good)
            self.assertEqual(self._cm[ref], good)         # stored verbatim, no trim
        with self.assertRaises(ValueError):
            vault.put_secret(kind="password", value="")


class StoreCasTest(_TempStore):
    """The atomic CAS primitives the vault saga + recovery rely on."""

    def test_finalize_provisioning_is_conditional(self):
        itstore.put_secret_ref(secret_ref="s1", kind="password")   # → provisioning
        self.assertTrue(itstore.finalize_provisioning("s1", "temporary"))  # 1st wins
        self.assertFalse(itstore.finalize_provisioning("s1", "persistent"))  # no longer provisioning
        self.assertEqual(itstore.secret_ref_state("s1")["lifecycle"], "temporary")

    def test_claim_is_exclusive_and_delete_only_recovering(self):
        itstore.put_secret_ref(secret_ref="s2", kind="password")   # provisioning
        self.assertTrue(itstore.claim_for_recovery("s2"))          # claim wins
        self.assertFalse(itstore.claim_for_recovery("s2"))         # already recovering
        self.assertFalse(itstore.finalize_provisioning("s2", "temporary"))  # can't finalize a claimed rec
        self.assertTrue(itstore.delete_claimed_recovery("s2"))     # delete a recovering record
        self.assertFalse(itstore.delete_claimed_recovery("s2"))    # gone → conflict, not success
        self.assertIsNone(itstore.secret_ref_state("s2"))


class RecoveryPolicyTest(_TempStore):
    """Recovery is bounded (max attempts → terminal) and origin-scoped (compensating
    action of the secure-intake path only). All transitions via CAS."""

    def test_recovery_bounded_to_max_attempts_then_terminal(self):
        from app.infrastructure.secrets import vault, wincred
        itstore.put_secret_ref(secret_ref="sref_inc", kind="password")   # provisioning, att=0
        with unittest.mock.patch.object(
                wincred, "delete_secret",
                side_effect=wincred.WinCredUnavailable("vault down")):
            vault.recover_incomplete_secrets()                           # attempt 1
            self.assertEqual(itstore.secret_ref_state("sref_inc")["recovery_attempts"], 1)
            vault.recover_incomplete_secrets()                           # attempt 2
            self.assertEqual(itstore.secret_ref_state("sref_inc")["recovery_attempts"], 2)
            s3 = vault.recover_incomplete_secrets()                      # exhausted → terminal
        st = itstore.secret_ref_state("sref_inc")
        self.assertEqual(st["lifecycle"], "recovery_failed")            # visible terminal
        self.assertEqual(st["recovery_attempts"], 2)                    # never exceeds max
        self.assertIn("sref_inc", s3["exhausted"])
        # a further pass does nothing — no auto-delete/retry beyond the cap
        s4 = vault.recover_incomplete_secrets()
        self.assertEqual(s4, {"cleaned": [], "failed": [], "skipped": [], "exhausted": []})

    def test_recovery_only_touches_secure_intake_origin(self):
        from app.infrastructure.secrets import vault
        import time as _t
        # a record from a DIFFERENT origin (inserted raw, bypassing the origin
        # allowlist) must NOT be claimed or recovered.
        conn = sqlite3.connect(itstore._DB_PATH_OVERRIDE)
        conn.execute(
            "INSERT INTO secret_refs (secret_ref, backend, kind, lifecycle, origin,"
            " recovery_attempts, created_at) VALUES"
            " ('sref_ext','wincred','password','provisioning','external',0,?)", (_t.time(),))
        conn.commit(); conn.close()
        self.assertFalse(itstore.claim_for_recovery("sref_ext"))       # origin guard
        summary = vault.recover_incomplete_secrets()
        self.assertNotIn("sref_ext", summary["cleaned"] + summary["exhausted"])
        self.assertEqual(itstore.secret_ref_state("sref_ext")["lifecycle"], "provisioning")

    def test_exhausted_terminal_only_for_secure_intake(self):
        # even the terminal transition is origin-scoped.
        import time as _t
        conn = sqlite3.connect(itstore._DB_PATH_OVERRIDE)
        conn.execute(
            "INSERT INTO secret_refs (secret_ref, backend, kind, lifecycle, origin,"
            " recovery_attempts, created_at) VALUES"
            " ('sref_ext2','wincred','password','cleanup_pending','external',5,?)", (_t.time(),))
        conn.commit(); conn.close()
        self.assertFalse(itstore.mark_recovery_failed("sref_ext2"))     # not our origin
        self.assertEqual(itstore.secret_ref_state("sref_ext2")["lifecycle"], "cleanup_pending")


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

    def test_non_wincred_backend_rejected(self):
        # John's fix #3: Phase 0 vault backend is strictly wincred.
        with self.assertRaises(ValueError):
            itstore.put_secret_ref(secret_ref="s", kind="password", backend="dpapi")
        # wincred is accepted
        itstore.put_secret_ref(secret_ref="ok", kind="password", backend="wincred")
        self.assertEqual(itstore.secret_ref_state("ok")["backend"], "wincred")

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
    """Narrow, fail-closed migration model. it_ops v1 was never released, so there
    are no partial-v0 schemas to migrate: v0+empty → create canonical v1; v0+any
    table → refuse; v1 → verify exact contract; future → fail-closed. Real SQLite
    fixtures, no mocked ALTER."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._path = str(Path(self._tmp) / "old.sqlite3")
        itstore._DB_PATH_OVERRIDE = self._path

    def tearDown(self):
        itstore._DB_PATH_OVERRIDE = None

    def _user_version(self) -> int:
        conn = sqlite3.connect(self._path)
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        return v

    def _tables(self) -> set:
        conn = sqlite3.connect(self._path)
        t = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        return t

    def test_fresh_v0_creates_canonical_v1(self):
        # empty DB (user_version=0, no itops tables) → create v1 and stamp.
        itstore.init_db()
        self.assertEqual(self._user_version(), itstore._SCHEMA_VERSION)
        itstore.upsert_asset(asset_id="a", label="A", kind="linux")   # usable
        self.assertIsNotNone(itstore.get_asset("a"))
        for t in ("assets", "connection_profiles", "change_runs", "secret_refs"):
            self.assertIn(t, self._tables())

    def test_partial_v0_fails_untouched(self):
        # user_version=0 but SOME itops table already exists → refuse; do not touch
        # the DB or stamp the version (no partial/unknown schema support).
        conn = sqlite3.connect(self._path)
        conn.executescript(
            "CREATE TABLE assets (asset_id TEXT PRIMARY KEY, label TEXT NOT NULL,"
            " kind TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);")
        conn.execute("PRAGMA user_version=0")
        conn.commit(); conn.close()
        with self.assertRaises(itstore.StoreUnavailable):
            itstore.init_db()
        self.assertEqual(self._user_version(), 0)
        self.assertEqual(self._tables(), {"assets"})   # untouched — nothing else created

    def test_v1_wrong_fk_on_delete_fails_untouched(self):
        # build the canonical v1, then corrupt connection_profiles' FK to
        # ON DELETE RESTRICT and re-init → the v1 verify must reject it, untouched.
        itstore.init_db()
        conn = sqlite3.connect(self._path)
        conn.executescript(
            "DROP TABLE connection_profiles;"
            "CREATE TABLE connection_profiles (profile_id TEXT PRIMARY KEY,"
            " asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,"
            " transport TEXT NOT NULL, user TEXT DEFAULT '', auth_ref TEXT,"
            " ssh_alias TEXT DEFAULT '', host_key_fingerprint TEXT DEFAULT '',"
            " os_platform_meta TEXT NOT NULL DEFAULT '{}', last_health TEXT NOT NULL DEFAULT '{}',"
            " created_at REAL NOT NULL, updated_at REAL NOT NULL);")
        conn.commit(); conn.close()
        self.assertEqual(self._user_version(), itstore._SCHEMA_VERSION)  # still v1
        with self.assertRaises(itstore.StoreUnavailable):
            itstore.init_db()                          # verify rejects the wrong on_delete
        self.assertEqual(self._user_version(), itstore._SCHEMA_VERSION)  # untouched

    def test_v1_verify_passes_and_changes_nothing(self):
        itstore.init_db()                              # v1
        itstore.upsert_asset(asset_id="keep", label="L", kind="linux")
        itstore.init_db()                              # verify-only, no change
        self.assertIsNotNone(itstore.get_asset("keep"))
        self.assertEqual(self._user_version(), itstore._SCHEMA_VERSION)

    def test_future_user_version_fails_closed(self):
        itstore.init_db()                              # build v1
        conn = sqlite3.connect(self._path)
        conn.execute(f"PRAGMA user_version={itstore._SCHEMA_VERSION + 5}")
        conn.commit(); conn.close()
        with self.assertRaises(itstore.StoreUnavailable):
            itstore.init_db()
        self.assertEqual(self._user_version(), itstore._SCHEMA_VERSION + 5,
                         "a future version must be left untouched")


class ItopsStartupTest(unittest.TestCase):
    """Recovery is operationally wired: flag-gated, migrate→recover→(then intake)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp) / "it_ops.sqlite3")

    def tearDown(self):
        itstore._DB_PATH_OVERRIDE = None

    def test_flag_off_does_not_touch_schema_or_recover(self):
        from app.application.it_ops import startup
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False), \
             unittest.mock.patch.object(itstore, "init_db") as init, \
             unittest.mock.patch("app.infrastructure.secrets.vault.recover_incomplete_secrets") as rec:
            startup.itops_startup()
        init.assert_not_called()
        rec.assert_not_called()

    def test_flag_on_migrates_then_recovers(self):
        from app.application.it_ops import startup
        from app.application import feature_flags as ff
        order = []
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=True), \
             unittest.mock.patch.object(itstore, "init_db",
                                        side_effect=lambda: order.append("init")), \
             unittest.mock.patch("app.infrastructure.secrets.vault.recover_incomplete_secrets",
                                 side_effect=lambda: (order.append("recover"),
                                                      {"cleaned": [], "failed": [], "skipped": []})[1]):
            startup.itops_startup()
        self.assertEqual(order, ["init", "recover"])       # migrate BEFORE recover

    def test_startup_always_logs_bounded_summary_even_all_zero(self):
        from app.application.it_ops import startup
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=True), \
             unittest.mock.patch.object(itstore, "init_db"), \
             unittest.mock.patch(
                 "app.infrastructure.secrets.vault.recover_incomplete_secrets",
                 return_value={"cleaned": [], "failed": [], "skipped": [], "exhausted": []}):
            with self.assertLogs("app.application.it_ops.startup", level="INFO") as cap:
                startup.itops_startup()
        blob = "\n".join(cap.output)
        self.assertIn("cleaned=0", blob)
        self.assertIn("failed=0", blob)
        self.assertIn("skipped=0", blob)
        self.assertIn("exhausted=0", blob)

    def test_startup_is_not_a_registered_tool(self):
        # recovery must not be model-callable — it is a plain startup function, not
        # a ToolSpec in the registry.
        from app.application.tool_registry import runtime as reg
        names = {s.get("name") for s in reg.search_tool_specs("recover", limit=50)}
        self.assertNotIn("recover_incomplete_secrets", names)
        self.assertNotIn("itops_startup", names)


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
