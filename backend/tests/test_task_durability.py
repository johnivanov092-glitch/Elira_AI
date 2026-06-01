"""Tests — Task durability: retry, dead_letter, waiting_approval (P5 Шаг 12)."""
from __future__ import annotations

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

from app.application.task_planner import runtime as planner_rt  # noqa: E402


def _make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = Path(tmp.name)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.close()
    return db


def _connect(db: Path):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def _setup(db: Path):
    planner_rt.init_db(connect_func=lambda: _connect(db))


def _create(db: Path, title: str = "test task") -> str:
    result = planner_rt.create_task(
        connect_func=lambda: _connect(db),
        id_func=lambda: "test-id",
        now_func=lambda: "2026-01-01T00:00:00",
        title=title,
    )
    return result["id"]


class TestMigrateDurability(unittest.TestCase):

    def test_migration_adds_columns(self):
        db = _make_db()
        try:
            _setup(db)
            con = _connect(db)
            cols = {row[1] for row in con.execute("PRAGMA table_info(tasks)").fetchall()}
            con.close()
            for col in ("idempotency_key", "retry_count", "max_retries",
                        "next_retry_at", "dead_letter"):
                self.assertIn(col, cols)
        finally:
            db.unlink(missing_ok=True)

    def test_migration_idempotent(self):
        db = _make_db()
        try:
            _setup(db)
            planner_rt.migrate_durability(connect_func=lambda: _connect(db))
        finally:
            db.unlink(missing_ok=True)


class TestBumpRetry(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _setup(self.db)
        self.tid = _create(self.db)

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _bump(self, **kwargs):
        return planner_rt.bump_retry(
            connect_func=lambda: _connect(self.db),
            now_func=lambda: "2026-01-01T00:00:00",
            tid=self.tid,
            backoff_base_seconds=10,
            **kwargs,
        )

    def test_first_retry_increments_count(self):
        result = self._bump()
        self.assertTrue(result["ok"])
        self.assertEqual(result["retry_count"], 1)

    def test_first_retry_sets_next_retry_at(self):
        result = self._bump()
        self.assertIsNotNone(result.get("next_retry_at"))

    def test_retry_resets_status_to_todo(self):
        result = self._bump()
        self.assertEqual(result["status"], "todo")

    def test_exceeds_max_retries_sets_dead_letter(self):
        # max_retries default is 3; bump 4 times
        for _ in range(4):
            result = self._bump()
        self.assertEqual(result["dead_letter"], 1)
        self.assertEqual(result["status"], "failed")

    def test_within_max_retries_not_dead_letter(self):
        for _ in range(3):
            result = self._bump()
        self.assertEqual(result["dead_letter"], 0)
        self.assertNotEqual(result["status"], "failed")


class TestSetWaitingApproval(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        _setup(self.db)
        self.tid = _create(self.db)

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_sets_waiting_approval_status(self):
        result = planner_rt.set_waiting_approval(
            connect_func=lambda: _connect(self.db),
            now_func=lambda: "2026-01-01T00:00:00",
            tid=self.tid,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "waiting_approval")

    def test_task_stays_waiting_until_updated(self):
        planner_rt.set_waiting_approval(
            connect_func=lambda: _connect(self.db),
            now_func=lambda: "2026-01-01T00:00:00",
            tid=self.tid,
        )
        con = _connect(self.db)
        row = con.execute("SELECT status FROM tasks WHERE id=?", (self.tid,)).fetchone()
        con.close()
        self.assertEqual(dict(row)["status"], "waiting_approval")


class TestTaskDurabilityRoutes(unittest.TestCase):

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.task_planner_routes import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

        self.db = _make_db()
        _setup(self.db)
        self.tid = _create(self.db, "Route test task")

        from app.application.task_planner import service as svc
        self._orig_db = svc.DB_PATH
        svc.DB_PATH = self.db
        self._patcher = mock.patch.object(svc, "DB_PATH", self.db)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.db.unlink(missing_ok=True)

    def test_retry_route(self):
        r = self.client.post(f"/api/tasks/{self.tid}/retry")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["retry_count"], 1)

    def test_waiting_approval_route(self):
        r = self.client.post(f"/api/tasks/{self.tid}/waiting_approval")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "waiting_approval")

    def test_dead_letter_after_max_retries(self):
        for _ in range(4):
            r = self.client.post(f"/api/tasks/{self.tid}/retry")
        self.assertEqual(r.json()["dead_letter"], 1)
        self.assertEqual(r.json()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
