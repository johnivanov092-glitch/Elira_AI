"""Disposable SQLite migration through the isolated approval/change executor."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import database_change, engine, registry, store as cs  # noqa: E402
from app.change_executor import telegram  # noqa: E402


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _create_canary(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA user_version=1;
            CREATE TABLE canary_items (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO canary_items(id, value) VALUES (1, 'TOP-SECRET');
            """
        )
        conn.commit()
    finally:
        conn.close()


class DatabaseChangeEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.database = self.tmp / "phase6_canary.sqlite3"
        self.backups = self.tmp / "backups"
        self.backups.mkdir()
        _create_canary(self.database)
        cs._DB_PATH_OVERRIDE = str(self.tmp / "change.sqlite3")
        cs.init_db()
        self.registry = self.tmp / "registry.json"
        self.registry.write_text(json.dumps({"targets": {
            "phase6-sqlite-canary": {
                "target_kind": "sqlite_migration",
                "database_id": "phase6-canary",
                "database_path": str(self.database),
                "backup_dir": str(self.backups),
                "migration_id": "canary_add_verified_at_v2",
                "operation": "migrate_v1_to_v2",
            }
        }}), encoding="utf-8")

    def tearDown(self) -> None:
        cs._DB_PATH_OVERRIDE = None

    def _plan(self) -> dict:
        return engine.plan(
            "phase6-sqlite-canary",
            registry_path=str(self.registry),
            clock=lambda: 1000.0,
        )

    def _approve(self, plan: dict) -> str:
        row = cs.consume_capability(
            capability_hash=_token_hash(plan["approve_token"]),
            approver="tg:42",
            apply_deadline_seconds=engine.APPLY_DEADLINE_SECONDS,
            now=1000.0,
        )
        self.assertEqual(row["change_run_status"], "applying")
        return plan["change_run_id"]

    def _version(self) -> int:
        conn = sqlite3.connect(self.database)
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()

    def _columns(self) -> list[str]:
        conn = sqlite3.connect(self.database)
        try:
            return [str(row[1]) for row in conn.execute("PRAGMA table_info(canary_items)")]
        finally:
            conn.close()

    def test_registry_is_fixed_to_one_canary_migration(self) -> None:
        target = registry.resolve("phase6-sqlite-canary", path=str(self.registry))
        self.assertEqual(target.database_id, "phase6-canary")
        self.assertEqual(target.migration_id, "canary_add_verified_at_v2")
        raw = json.loads(self.registry.read_text(encoding="utf-8"))
        for key, value in (
            ("database_id", "elira-state"),
            ("migration_id", "DROP TABLE canary_items"),
            ("operation", "arbitrary_sql"),
        ):
            bad = json.loads(json.dumps(raw))
            bad["targets"]["phase6-sqlite-canary"][key] = value
            self.registry.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(registry.RegistryError):
                registry.resolve("phase6-sqlite-canary", path=str(self.registry))
        self.registry.write_text(json.dumps(raw), encoding="utf-8")

        duplicate = json.loads(json.dumps(raw))
        duplicate["targets"]["duplicate-canary-alias"] = duplicate["targets"].pop(
            "phase6-sqlite-canary"
        )
        self.registry.write_text(json.dumps(duplicate), encoding="utf-8")
        with self.assertRaises(registry.RegistryError):
            registry.resolve("duplicate-canary-alias", path=str(self.registry))
        self.registry.write_text(json.dumps(raw), encoding="utf-8")

    def test_plan_contains_projection_not_path_sql_or_row_data(self) -> None:
        plan = self._plan()
        self.assertEqual(plan["snapshot"]["user_version"], 1)
        self.assertEqual(plan["snapshot"]["row_count"], 1)
        blob = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn(str(self.database), blob)
        self.assertNotIn("ALTER TABLE", blob)
        self.assertNotIn("TOP-SECRET", blob)
        self.assertEqual(
            plan["planned_argv"],
            ["sqlite-migration", "phase6-canary", "canary_add_verified_at_v2"],
        )
        message = telegram._format_plan(plan["change_run_id"], "phase6-sqlite-canary",
                                        plan["snapshot"])
        self.assertIn("backup: executor-owned, created before write", message)
        self.assertIn("rollback: automatic", message)
        self.assertNotIn(str(self.database), message)
        self.assertNotIn("TOP-SECRET", message)

    def test_success_creates_backup_before_write_verifies_and_removes_backup(self) -> None:
        plan = self._plan()
        cid = self._approve(plan)
        seen: dict[str, bool] = {}
        original_migrate = database_change.apply_fixed_migration

        def migrate(conn: sqlite3.Connection) -> None:
            seen["backup_exists"] = (self.backups / f"{cid}.sqlite3").is_file()
            original_migrate(conn)

        with mock.patch.object(database_change, "apply_fixed_migration", side_effect=migrate):
            self.assertEqual(
                engine.apply(cid, registry_path=str(self.registry)),
                "applied",
            )
        self.assertTrue(seen.get("backup_exists"))
        self.assertEqual(self._version(), 2)
        self.assertEqual(self._columns(), ["id", "value", "verified_at"])
        self.assertFalse((self.backups / f"{cid}.sqlite3").exists())
        evidence = json.dumps(cs.list_change_evidence(cid), ensure_ascii=False)
        self.assertNotIn(str(self.database), evidence)
        self.assertNotIn("TOP-SECRET", evidence)

    def test_preapply_drift_aborts_without_migration(self) -> None:
        cid = self._approve(self._plan())
        conn = sqlite3.connect(self.database)
        try:
            conn.execute("INSERT INTO canary_items(id, value) VALUES (2, 'drift')")
            conn.commit()
        finally:
            conn.close()
        with mock.patch.object(database_change, "apply_fixed_migration") as migrate:
            self.assertEqual(
                engine.apply(cid, registry_path=str(self.registry)),
                "aborted_before_apply",
            )
        migrate.assert_not_called()
        self.assertEqual(self._version(), 1)

    def test_failed_migration_rolls_back_transaction_and_reports_command_failed(self) -> None:
        cid = self._approve(self._plan())

        def fail(_conn: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("forced failure")

        with mock.patch.object(database_change, "apply_fixed_migration", side_effect=fail):
            self.assertEqual(
                engine.apply(cid, registry_path=str(self.registry)),
                "command_failed",
            )
        self.assertEqual(self._version(), 1)
        self.assertEqual(self._columns(), ["id", "value"])
        self.assertFalse((self.backups / f"{cid}.sqlite3").exists())

    def test_definite_failed_postcheck_restores_backup(self) -> None:
        cid = self._approve(self._plan())
        original = database_change.inspect
        calls = 0

        def failed_once(target):
            nonlocal calls
            calls += 1
            definite, exit_status, fields = original(target)
            if calls == 2 and definite:
                fields = dict(fields)
                fields["quick_check"] = "failed"
            return definite, exit_status, fields

        with mock.patch.object(database_change, "inspect", side_effect=failed_once):
            self.assertEqual(
                engine.apply(cid, registry_path=str(self.registry)),
                "rolled_back",
            )
        self.assertEqual(self._version(), 1)
        self.assertEqual(self._columns(), ["id", "value"])
        self.assertFalse((self.backups / f"{cid}.sqlite3").exists())
        result = json.loads(cs.list_change_evidence(cid)[-1]["result"])
        self.assertTrue(result["rollback_attempted"])

    def test_unknown_retains_backup_then_typed_resolution_cleans_it(self) -> None:
        cid = self._approve(self._plan())
        original = database_change.inspect
        calls = 0

        def unknown_after_precheck(target):
            nonlocal calls
            calls += 1
            return original(target) if calls == 1 else (False, "error", {})

        with mock.patch.object(database_change, "inspect", side_effect=unknown_after_precheck):
            self.assertEqual(
                engine.apply(cid, registry_path=str(self.registry)),
                "apply_unknown",
            )
        self.assertTrue((self.backups / f"{cid}.sqlite3").is_file())
        self.assertTrue(engine.resolve(cid, resolver="tg:42", registry_path=str(self.registry)))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "resolved_applied")
        self.assertFalse((self.backups / f"{cid}.sqlite3").exists())

    def test_tampered_backup_cannot_be_restored_and_target_stays_locked(self) -> None:
        cid = self._approve(self._plan())
        original_inspect = database_change.inspect
        original_restore = database_change._restore
        calls = 0

        def failed_postcheck(target):
            nonlocal calls
            calls += 1
            definite, exit_status, fields = original_inspect(target)
            if calls == 2 and definite:
                fields = dict(fields)
                fields["quick_check"] = "failed"
            return definite, exit_status, fields

        def tamper_then_restore(target, backup, expected_sha256):
            with backup.open("ab") as fh:
                fh.write(b"tampered")
            return original_restore(target, backup, expected_sha256)

        with mock.patch.object(database_change, "inspect", side_effect=failed_postcheck), \
                mock.patch.object(database_change, "_restore", side_effect=tamper_then_restore):
            self.assertEqual(
                engine.apply(cid, registry_path=str(self.registry)),
                "rollback_failed",
            )
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "rollback_failed")
        self.assertTrue((self.backups / f"{cid}.sqlite3").exists())
        with self.assertRaises(cs.ActiveTargetConflict):
            cs.create_plan_with_capabilities(
                change_run_id=cs.new_change_run_id(),
                target_id="phase6-sqlite-canary",
                unit="phase6-canary",
                operation="migrate_v1_to_v2",
                snapshot="{}",
                snapshot_main_pid=0,
                snapshot_hash="b" * 64,
                planned_argv_hash="c" * 64,
                planned_binding="{}",
                approve_hash="d" * 64,
                reject_hash="e" * 64,
                capability_expires_at=2_000.0,
            )
        self.assertTrue(engine.resolve(cid, resolver="tg:42", registry_path=str(self.registry)))
        self.assertEqual(cs.get_change_run(cid)["change_run_status"], "resolved_applied")
        self.assertFalse((self.backups / f"{cid}.sqlite3").exists())

    def test_deadline_sweep_wins_final_cas_and_backup_is_retained(self) -> None:
        cid = self._approve(self._plan())

        def sweep_wins(**kwargs):
            self.assertTrue(cs.mark_apply_unknown(
                change_run_id=kwargs["change_run_id"],
                attempt_token=kwargs["attempt_token"],
                verdict="deadline sweep",
            ))
            return False

        with mock.patch.object(cs, "finalize_apply", side_effect=sweep_wins):
            self.assertEqual(
                engine.apply(cid, registry_path=str(self.registry)),
                "apply_unknown",
            )
        self.assertEqual(self._version(), 2)
        self.assertTrue((self.backups / f"{cid}.sqlite3").is_file())
        self.assertTrue(engine.resolve(cid, resolver="tg:42", registry_path=str(self.registry)))
        self.assertFalse((self.backups / f"{cid}.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
