from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.agent_kernel import operation_scope as opscope  # noqa: E402
from app.application.it_ops import database_inspect as di  # noqa: E402
from app.infrastructure.it_ops import store as itstore  # noqa: E402


DB_TOOL = "itops_database_inspect"


def _create_state_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            INSERT INTO chats VALUES ('chat-1', 'PRIVATE TITLE', 1, 1);
            INSERT INTO messages VALUES ('msg-1', 'chat-1', 'user', 'PRIVATE MESSAGE', 1);
            PRAGMA user_version=0;
            """
        )
        conn.commit()
    finally:
        conn.close()


class DatabaseInspectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "elira_state.db"
        _create_state_db(self.db)
        self._old_data_dir = di.DATA_DIR
        di.DATA_DIR = self.root
        self.spec = di.resolve_database("elira-state")

    def tearDown(self):
        di.DATA_DIR = self._old_data_dir
        self._tmp.cleanup()

    def test_bounded_projection_has_counts_not_row_data_or_connection_details(self):
        before_hash = hashlib.sha256(self.db.read_bytes()).hexdigest()
        before_stat = self.db.stat()
        before_sidecars = sorted(p.name for p in self.root.glob("elira_state.db-*"))

        out = di.inspect_database(self.spec)

        self.assertEqual(out["quick_check"], "ok")
        self.assertEqual(out["user_version"], 0)
        self.assertEqual(out["migration_state"], "unversioned")
        self.assertEqual(out["safe_query"]["profile"], "conversation-counts")
        self.assertEqual(out["safe_query"]["chat_count"], 1)
        self.assertEqual(out["safe_query"]["message_count"], 1)
        self.assertIn("chats", {t["name"] for t in out["tables"]})

        encoded = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("PRIVATE TITLE", encoded)
        self.assertNotIn("PRIVATE MESSAGE", encoded)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("file:", encoded)
        self.assertNotIn("SELECT ", encoded)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(self.db.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(sorted(p.name for p in self.root.glob("elira_state.db-*")), before_sidecars)

    def test_connection_denies_write_attach_and_unsafe_pragma(self):
        with di._snapshot_connection(self.db) as (conn, _meta):
            for statement in (
                "CREATE TABLE forbidden(id INTEGER)",
                "ATTACH DATABASE ':memory:' AS other",
                "PRAGMA writable_schema=ON",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(statement)

    def test_live_wal_is_included_via_consistent_online_snapshot(self):
        writer = sqlite3.connect(self.db)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute(
                "INSERT INTO messages VALUES ('msg-wal', 'chat-1', 'assistant', 'WAL SECRET', 2)"
            )
            writer.commit()
            wal = Path(f"{self.db}-wal")
            shm = Path(f"{self.db}-shm")
            self.assertTrue(wal.exists())
            self.assertTrue(shm.exists())
            before_names = sorted(p.name for p in self.root.glob("elira_state.db-*"))
            before_main_hash = hashlib.sha256(self.db.read_bytes()).hexdigest()

            out = di.inspect_database(self.spec)

            self.assertEqual(out["safe_query"]["message_count"], 2)
            self.assertNotIn("WAL SECRET", json.dumps(out, ensure_ascii=False))
            self.assertEqual(sorted(p.name for p in self.root.glob("elira_state.db-*")), before_names)
            self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before_main_hash)
        finally:
            writer.close()

    def test_backup_freshness_is_honest_and_path_free(self):
        missing = di.inspect_database(self.spec)["backup"]
        self.assertEqual(missing, {
            "status": "missing", "count": 0,
            "stale_after_seconds": di.BACKUP_STALE_SECONDS,
        })

        backup = self.root / "elira_state.db.bak-1"
        backup.write_bytes(b"backup")
        fresh = di.inspect_database(self.spec)["backup"]
        self.assertEqual(fresh["status"], "fresh")
        self.assertNotIn("path", fresh)
        self.assertNotIn("name", fresh)

        old = time.time() - di.BACKUP_STALE_SECONDS - 60
        os.utime(backup, (old, old))
        stale = di.inspect_database(self.spec)["backup"]
        self.assertEqual(stale["status"], "stale")
        self.assertGreater(stale["latest_age_seconds"], di.BACKUP_STALE_SECONDS)

    def test_schema_and_total_structured_output_are_bounded(self):
        conn = sqlite3.connect(self.db)
        try:
            columns = ", ".join(f"column_{i:02d}_{'x' * 32} TEXT" for i in range(40))
            for table_index in range(40):
                conn.execute(f"CREATE TABLE extra_{table_index:02d} ({columns})")
            conn.commit()
        finally:
            conn.close()

        out = di.inspect_database(self.spec)
        self.assertTrue(out["schema_truncated"])
        self.assertLessEqual(
            len(json.dumps(out["tables"], ensure_ascii=False, separators=(",", ":"))),
            di.MAX_SCHEMA_JSON_CHARS,
        )
        self.assertLessEqual(len(out["schema_summary"]), di.MAX_SCHEMA_SUMMARY_CHARS)
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False)), 10000)

    def test_unknown_missing_or_wrong_schema_fails_closed(self):
        with self.assertRaises(di.DatabaseInspectError) as unknown:
            di.resolve_database("other")
        self.assertEqual(unknown.exception.reason, "unknown_database")

        self.db.unlink()
        with self.assertRaises(di.DatabaseInspectError) as missing:
            di.inspect_database(self.spec)
        self.assertEqual(missing.exception.reason, "database_unavailable")

        _create_state_db(self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TABLE messages")
        conn.commit()
        conn.close()
        with self.assertRaises(di.DatabaseInspectError) as wrong:
            di.inspect_database(self.spec)
        self.assertEqual(wrong.exception.reason, "required_table_missing")


class DatabaseInspectRouteTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.itops_routes import router
        from app.application import feature_flags as ff

        self._tmp = tempfile.TemporaryDirectory()
        itstore._DB_PATH_OVERRIDE = str(Path(self._tmp.name) / "it_ops.sqlite3")
        self._flag = unittest.mock.patch.object(ff, "flag_enabled", return_value=True)
        self._flag.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self._flag.stop()
        itstore._DB_PATH_OVERRIDE = None
        self._tmp.cleanup()

    def _start(self, **body):
        return self.client.post("/api/itops/database/inspect/start", json=body)

    def test_valid_request_binds_exact_database_and_tool(self):
        response = self._start(database_id="elira-state")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["tool"], DB_TOOL)
        scope = opscope.get_active_scope(body["run_id"])
        self.assertEqual(scope.target_kind, "database")
        self.assertEqual(scope.database.database_id, "elira-state")
        self.assertEqual(scope.allowed_tool, DB_TOOL)
        opscope.clear_scope(body["run_id"])

    def test_client_cannot_supply_path_sql_or_schema(self):
        for extra in (
            {"path": "C:/other.db"},
            {"sql": "SELECT content FROM messages"},
            {"schema": "temp"},
        ):
            with self.subTest(extra=extra):
                response = self._start(database_id="elira-state", **extra)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self._start(database_id="other").status_code, 422)

    def test_flag_off_is_404(self):
        from app.application import feature_flags as ff
        with unittest.mock.patch.object(ff, "flag_enabled", return_value=False):
            self.assertEqual(self._start(database_id="elira-state").status_code, 404)


class DatabaseInspectHandlerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "elira_state.db"
        _create_state_db(self.db)
        self._old_data_dir = di.DATA_DIR
        di.DATA_DIR = self.root
        itstore._DB_PATH_OVERRIDE = str(self.root / "it_ops.sqlite3")
        itstore.init_db()
        self.rid = "database-run"
        opscope.bind_scope_database(self.rid, database_id="elira-state", allowed_tool=DB_TOOL)

    def tearDown(self):
        opscope.clear_scope(self.rid)
        itstore._DB_PATH_OVERRIDE = None
        di.DATA_DIR = self._old_data_dir
        self._tmp.cleanup()

    def _run(self, rid: str | None = None):
        from app.application.code_agent.tools import reset_current_run_id, set_current_run_id
        from app.application.tool_providers import itops_provider

        token = set_current_run_id(rid or self.rid)
        try:
            return itops_provider.tool_itops_database_inspect()
        finally:
            reset_current_run_id(token)

    def test_success_persists_flat_safe_evidence(self):
        out = self._run()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["database"]["safe_query"]["message_count"], 1)
        evidence = itstore.list_evidence(self.rid)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["operation"], "database_inspect:elira-state")
        result = evidence[0]["result"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["message_count"], 1)
        self.assertIn("chats(", result["schema_summary"])
        self.assertIn("Schema: chats(", out["text"])
        encoded = json.dumps(result, ensure_ascii=False)
        for forbidden in ("PRIVATE", str(self.root), "file:", "SELECT "):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue({"path", "sql", "connection_string", "dsn"}.isdisjoint(result))

    def test_missing_database_is_failed_and_evidenced(self):
        self.db.unlink()
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "database_unavailable")
        evidence = itstore.list_evidence(self.rid)
        self.assertEqual(evidence[0]["result"]["status"], "failed")
        self.assertEqual(evidence[0]["exit_status"], "1")

    def test_evidence_failure_never_reports_success(self):
        with unittest.mock.patch.object(itstore, "record_evidence", side_effect=RuntimeError("disk full")):
            out = self._run()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "evidence_persist_failed")


class DatabaseEvidenceProjectionTest(unittest.TestCase):
    def test_public_projection_drops_polluted_database_fields(self):
        from app.api.routes.itops_routes import _public_evidence

        public = _public_evidence({
            "evidence_id": "ev-1", "run_id": "r", "target_identity": "database:elira-state",
            "scanner_vantage": "elira-local:sqlite-ro", "operation": "database_inspect:elira-state",
            "exit_status": "0", "captured_at": 1,
            "result": {
                "status": "ok", "database_id": "elira-state", "engine": "sqlite",
                "message_count": 2, "schema_summary": "chats(id,title)",
                "path": "C:/secret.db", "connection_string": "secret",
                "sql": "SELECT content FROM messages", "password": "secret",
            },
        })
        self.assertEqual(public["result"], {
            "status": "ok", "database_id": "elira-state", "engine": "sqlite",
            "message_count": 2, "schema_summary": "chats(id,title)",
        })


if __name__ == "__main__":
    unittest.main()
