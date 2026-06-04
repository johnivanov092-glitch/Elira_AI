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
        # P9.2C: bounded auto-retry now requires an idempotency_key; key this task
        # so the retry-mechanism tests below exercise the allowed keyed path.
        con = _connect(self.db)
        con.execute("UPDATE tasks SET idempotency_key='durability-key' WHERE id=?", (self.tid,))
        con.commit()
        con.close()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_keyless_retry_is_blocked(self):
        # P9.2C: a task without an idempotency_key must not be auto-retried.
        keyless = planner_rt.create_task(
            connect_func=lambda: _connect(self.db),
            id_func=lambda: "keyless-id",
            now_func=lambda: "2026-01-01T00:00:00",
            title="keyless task",
        )["id"]
        result = planner_rt.bump_retry(
            connect_func=lambda: _connect(self.db),
            now_func=lambda: "2026-01-01T00:00:00",
            tid=keyless,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("error"), "retry_blocked_no_idempotency_key")
        self.assertEqual(result["status"], "blocked")

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


class TestStartupRecovery(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        _setup(self.db)

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _insert_task(
        self,
        tid: str,
        *,
        status: str = "in_progress",
        idempotency_key: str = "",
        retry_count: int = 0,
        max_retries: int = 3,
        updated_at: str = "2026-01-01T00:00:00",
    ) -> None:
        con = _connect(self.db)
        con.execute(
            """
            INSERT INTO tasks (
                id, title, description, category, priority, status, tags,
                created_at, updated_at, idempotency_key, retry_count, max_retries
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tid,
                tid,
                "",
                "general",
                "medium",
                status,
                "[]",
                "2026-01-01T00:00:00",
                updated_at,
                idempotency_key,
                retry_count,
                max_retries,
            ),
        )
        con.commit()
        con.close()

    def _recover(self, **kwargs):
        events: list[dict] = []
        result = planner_rt.recover_stale_tasks(
            connect_func=lambda: _connect(self.db),
            now_func=lambda: "2026-01-01T02:00:00",
            stale_after_seconds=3600,
            emit_event_func=lambda **kw: events.append(kw),
            **kwargs,
        )
        return result, events

    def _status(self, tid: str) -> str:
        con = _connect(self.db)
        row = con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
        con.close()
        return dict(row)["status"]

    def test_keyed_stale_in_progress_is_rescheduled(self):
        self._insert_task("stale-keyed", idempotency_key="idem-1")
        result, events = self._recover(backoff_base_seconds=10)
        self.assertEqual(result["rescheduled"], 1)
        self.assertEqual(self._status("stale-keyed"), "todo")
        self.assertEqual(events[0]["event_type"], "task.recovery.rescheduled")

    def test_keyless_stale_in_progress_is_blocked(self):
        self._insert_task("stale-keyless")
        result, events = self._recover()
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(self._status("stale-keyless"), "blocked")
        self.assertEqual(events[0]["event_type"], "task.recovery.blocked")

    def test_waiting_approval_is_left_paused(self):
        self._insert_task("approval", status="waiting_approval", idempotency_key="idem")
        result, events = self._recover()
        self.assertEqual(result["waiting_approval"], 1)
        self.assertEqual(result["rescheduled"], 0)
        self.assertEqual(events, [])
        self.assertEqual(self._status("approval"), "waiting_approval")

    def test_fresh_in_progress_is_not_recovered(self):
        self._insert_task("fresh", idempotency_key="idem", updated_at="2026-01-01T01:30:00")
        result, events = self._recover()
        self.assertEqual(result["skipped_fresh"], 1)
        self.assertEqual(result["rescheduled"], 0)
        self.assertEqual(events, [])
        self.assertEqual(self._status("fresh"), "in_progress")

    def test_stale_keyed_over_max_retries_goes_dead_letter(self):
        self._insert_task("dead", idempotency_key="idem", retry_count=3, max_retries=3)
        result, events = self._recover()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self._status("dead"), "failed")
        self.assertEqual(events[0]["event_type"], "task.recovery.dead_letter")

    def test_recovery_limit_is_respected(self):
        self._insert_task("a", idempotency_key="idem-a")
        self._insert_task("b", idempotency_key="idem-b")
        result, _events = self._recover(limit=1)
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["rescheduled"], 1)
        statuses = {tid: self._status(tid) for tid in ("a", "b")}
        self.assertEqual(list(statuses.values()).count("todo"), 1)
        self.assertEqual(list(statuses.values()).count("in_progress"), 1)


class TestRunChecklist(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        _setup(self.db)

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _update(self, **kwargs):
        events: list[dict] = []
        result = planner_rt.update_checklist(
            connect_func=lambda: _connect(self.db),
            id_func=lambda: "item-generated",
            now_func=lambda: "2026-01-01T00:00:00",
            emit_event_func=lambda **kw: events.append(kw),
            **kwargs,
        )
        return result, events

    def test_checklist_persists_across_init(self):
        result, events = self._update(
            run_id="run-1",
            items=[
                {"id": "plan", "text": "make plan", "status": "pending", "position": 1},
                {"id": "verify", "text": "run tests", "status": "pending", "position": 2},
            ],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(events[0]["event_type"], "task.checklist.updated")

        _setup(self.db)  # simulates backend restart / idempotent migration
        listed = planner_rt.list_checklist(connect_func=lambda: _connect(self.db), run_id="run-1")
        self.assertTrue(listed["ok"])
        self.assertEqual([item["id"] for item in listed["items"]], ["plan", "verify"])

    def test_updates_status_and_blocker(self):
        self._update(run_id="run-2", items=[{"id": "a", "text": "step a"}])
        result, _events = self._update(
            run_id="run-2",
            updates=[{"id": "a", "status": "blocked", "blocker": "needs approval"}],
        )
        self.assertTrue(result["ok"])
        item = result["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertEqual(item["blocker"], "needs approval")

    def test_invalid_status_does_not_partially_write(self):
        self._update(run_id="run-3", items=[{"id": "a", "text": "step a"}])
        result, _events = self._update(
            run_id="run-3",
            updates=[{"id": "a", "status": "nope"}],
        )
        self.assertFalse(result["ok"])
        listed = planner_rt.list_checklist(connect_func=lambda: _connect(self.db), run_id="run-3")
        self.assertEqual(listed["items"][0]["status"], "pending")


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
        # P9.2C: key the task so route-level bounded retry uses the allowed path.
        con = _connect(self.db)
        con.execute("UPDATE tasks SET idempotency_key='route-key' WHERE id=?", (self.tid,))
        con.commit()
        con.close()

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

    def test_recover_stale_route(self):
        con = _connect(self.db)
        con.execute(
            "UPDATE tasks SET status='in_progress', updated_at='2000-01-01T00:00:00' WHERE id=?",
            (self.tid,),
        )
        con.commit()
        con.close()
        r = self.client.post("/api/tasks/recover-stale", json={"stale_after_seconds": 3600, "limit": 10})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["rescheduled"], 1)

    def test_checklist_routes(self):
        r = self.client.post(
            "/api/tasks/checklist/route-run",
            json={"items": [{"id": "a", "text": "route item", "status": "pending"}]},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

        g = self.client.get("/api/tasks/checklist/route-run")
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.json()["items"][0]["id"], "a")


if __name__ == "__main__":
    unittest.main()
