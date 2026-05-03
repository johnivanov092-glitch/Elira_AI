"""Tests for application/task_planner/runtime.py."""
from __future__ import annotations

import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.task_planner.runtime import (  # noqa: E402
    create_task,
    delete_task,
    get_task,
    init_db,
    list_tasks,
    task_stats,
    update_task,
)


# ── test helpers ──────────────────────────────────────────────────────────────

def _make_db():
    """Return (connect_func, keeper_conn) for a shared-cache in-memory DB."""
    db_name = f"file:test_tp_{uuid.uuid4().hex}?mode=memory&cache=shared"

    def connect():
        c = sqlite3.connect(db_name, uri=True)
        c.row_factory = sqlite3.Row
        return c

    keeper = sqlite3.connect(db_name, uri=True)
    init_db(connect_func=connect)
    return connect, keeper


_ID_COUNTER = 0


def _id_gen():
    global _ID_COUNTER
    _ID_COUNTER += 1
    return f"task-{_ID_COUNTER:04d}"


def _now():
    return datetime.now(tz=timezone.utc).isoformat()


# ── tests ─────────────────────────────────────────────────────────────────────

class TaskPlannerCRUDTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connect, self._keeper = _make_db()

    def tearDown(self) -> None:
        self._keeper.close()
        super().tearDown()

    def _create(self, title="Test Task", **kwargs):
        return create_task(
            connect_func=self.connect,
            id_func=_id_gen,
            now_func=_now,
            title=title,
            **kwargs,
        )

    # create ──────────────────────────────────────────────────────────────────

    def test_create_task_returns_ok(self) -> None:
        result = self._create("Buy milk")
        self.assertTrue(result["ok"])
        self.assertIn("id", result)
        self.assertEqual(result["title"], "Buy milk")

    def test_create_task_with_all_fields(self) -> None:
        result = self._create(
            "Write tests",
            description="Cover application modules",
            category="dev",
            priority="high",
            tags=["testing", "backend"],
        )
        self.assertTrue(result["ok"])

    def test_create_multiple_tasks(self) -> None:
        for i in range(5):
            self._create(f"Task {i}")
        listed = list_tasks(connect_func=self.connect)
        self.assertEqual(listed["count"], 5)

    # list ────────────────────────────────────────────────────────────────────

    def test_list_tasks_empty(self) -> None:
        result = list_tasks(connect_func=self.connect)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_list_filter_by_status(self) -> None:
        t1 = self._create("Todo task")
        t2 = self._create("Done task")
        update_task(connect_func=self.connect, now_func=_now, tid=t2["id"], status="done")

        todos = list_tasks(connect_func=self.connect, status="todo")
        dones = list_tasks(connect_func=self.connect, status="done")
        self.assertEqual(todos["count"], 1)
        self.assertEqual(dones["count"], 1)
        self.assertEqual(todos["tasks"][0]["id"], t1["id"])

    def test_list_filter_by_category(self) -> None:
        self._create("Work task", category="work")
        self._create("Personal task", category="personal")
        result = list_tasks(connect_func=self.connect, category="work")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tasks"][0]["title"], "Work task")

    def test_list_limit_respected(self) -> None:
        for i in range(10):
            self._create(f"Task {i}")
        result = list_tasks(connect_func=self.connect, limit=3)
        self.assertEqual(result["count"], 3)

    def test_list_tags_deserialized(self) -> None:
        self._create("Tagged", tags=["alpha", "beta"])
        result = list_tasks(connect_func=self.connect)
        tags = result["tasks"][0]["tags"]
        self.assertIsInstance(tags, list)
        self.assertIn("alpha", tags)

    def test_list_priority_ordering(self) -> None:
        self._create("Low priority task", priority="low")
        self._create("Urgent task", priority="urgent")
        self._create("High task", priority="high")
        result = list_tasks(connect_func=self.connect)
        priorities = [t["priority"] for t in result["tasks"]]
        # urgent must come before high, high before low
        self.assertLess(priorities.index("urgent"), priorities.index("high"))
        self.assertLess(priorities.index("high"), priorities.index("low"))

    # get ─────────────────────────────────────────────────────────────────────

    def test_get_existing_task(self) -> None:
        t = self._create("Find me")
        result = get_task(connect_func=self.connect, tid=t["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Find me")

    def test_get_nonexistent_task(self) -> None:
        result = get_task(connect_func=self.connect, tid="no-such-id")
        self.assertFalse(result["ok"])

    # update ──────────────────────────────────────────────────────────────────

    def test_update_title(self) -> None:
        t = self._create("Old title")
        update_task(connect_func=self.connect, now_func=_now, tid=t["id"], title="New title")
        result = get_task(connect_func=self.connect, tid=t["id"])
        self.assertEqual(result["title"], "New title")

    def test_update_status_to_done_sets_completed_at(self) -> None:
        t = self._create("Finish me")
        update_task(connect_func=self.connect, now_func=_now, tid=t["id"], status="done")
        result = get_task(connect_func=self.connect, tid=t["id"])
        self.assertEqual(result["status"], "done")
        self.assertIsNotNone(result["completed_at"])

    def test_update_unknown_field_ignored(self) -> None:
        t = self._create("Safe task")
        # Should not raise; unknown field is silently ignored
        update_task(connect_func=self.connect, now_func=_now, tid=t["id"], __evil__="drop table")
        result = get_task(connect_func=self.connect, tid=t["id"])
        self.assertTrue(result["ok"])

    def test_update_tags_as_list(self) -> None:
        t = self._create("Tag me")
        update_task(connect_func=self.connect, now_func=_now, tid=t["id"], tags=["new", "tag"])
        result = get_task(connect_func=self.connect, tid=t["id"])
        self.assertIn("new", result["tags"])

    # delete ──────────────────────────────────────────────────────────────────

    def test_delete_task(self) -> None:
        t = self._create("To delete")
        self.assertTrue(delete_task(connect_func=self.connect, tid=t["id"])["ok"])
        self.assertFalse(get_task(connect_func=self.connect, tid=t["id"])["ok"])

    def test_delete_nonexistent_does_not_raise(self) -> None:
        result = delete_task(connect_func=self.connect, tid="ghost-id")
        self.assertIsInstance(result, dict)

    # stats ───────────────────────────────────────────────────────────────────

    def test_stats_empty_db(self) -> None:
        result = task_stats(connect_func=self.connect, now_func=_now)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 0)

    def test_stats_counts_by_status(self) -> None:
        self._create("A")
        self._create("B")
        t = self._create("C")
        update_task(connect_func=self.connect, now_func=_now, tid=t["id"], status="done")
        result = task_stats(connect_func=self.connect, now_func=_now)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["by_status"].get("done", 0), 1)
        self.assertEqual(result["by_status"].get("todo", 0), 2)

    def test_stats_overdue_count(self) -> None:
        self._create("Past due", due_date="2000-01-01T00:00:00")
        self._create("Future", due_date="2099-01-01T00:00:00")
        result = task_stats(connect_func=self.connect, now_func=_now)
        self.assertEqual(result["overdue"], 1)


if __name__ == "__main__":
    unittest.main()
