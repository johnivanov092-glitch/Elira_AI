"""Tests for application/elira_supervisor and application/elira_patch runtimes."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.elira_supervisor.runtime as sv         # noqa: E402
import app.application.elira_patch.runtime as patch_rt        # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# elira_supervisor — pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class SupervisorHelpersTest(unittest.TestCase):
    def test_dumps_loads_round_trip(self) -> None:
        data = {"key": "value", "num": 42, "list": [1, 2, 3]}
        text = sv.dumps_json(data)
        self.assertIsInstance(text, str)
        self.assertEqual(sv.loads_json(text), data)

    def test_loads_json_none_returns_none(self) -> None:
        self.assertIsNone(sv.loads_json(None))

    def test_resolve_blocked_path(self) -> None:
        path, err = sv.resolve_project_path("node_modules/evil.js")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")

    def test_resolve_outside_root(self) -> None:
        path, err = sv.resolve_project_path("../../etc/passwd")
        self.assertIsNone(path)
        self.assertEqual(err, "outside_root")

    def test_resolve_valid_path_no_error(self) -> None:
        path, err = sv.resolve_project_path("src/main.py")
        self.assertIsNone(err)
        self.assertIsNotNone(path)

    def test_resolve_git_blocked(self) -> None:
        path, err = sv.resolve_project_path(".git/config")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")


# ─────────────────────────────────────────────────────────────────────────────
# elira_supervisor — build_plan / build_steps
# ─────────────────────────────────────────────────────────────────────────────

class SupervisorPlanTest(unittest.TestCase):
    def test_build_plan_empty_goal_adds_inspect(self) -> None:
        plan = sv.build_plan("some generic goal", None, [])
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["action"], "inspect")

    def test_build_plan_with_current_path(self) -> None:
        plan = sv.build_plan("fix the bug", "backend/main.py", [])
        actions = [p["action"] for p in plan]
        self.assertIn("modify", actions)
        paths = [p["path"] for p in plan]
        self.assertIn("backend/main.py", paths)

    def test_build_plan_create_keyword(self) -> None:
        plan = sv.build_plan("create a new component", None, [])
        actions = [p["action"] for p in plan]
        self.assertIn("create", actions)

    def test_build_plan_api_keyword(self) -> None:
        plan = sv.build_plan("add new api endpoint", None, [])
        paths = [p["path"] for p in plan]
        self.assertTrue(any("route" in p.lower() or "api" in p.lower() for p in paths))

    def test_build_plan_staged_paths_included(self) -> None:
        staged = ["file1.py", "file2.py"]
        plan = sv.build_plan("generic goal", None, staged)
        paths = [p["path"] for p in plan]
        self.assertIn("file1.py", paths)

    def test_build_plan_capped_at_12(self) -> None:
        staged = [f"f{i}.py" for i in range(20)]
        plan = sv.build_plan("api endpoint", None, staged)
        self.assertLessEqual(len(plan), 12)

    def test_build_steps_has_four_agents(self) -> None:
        steps = sv.build_steps([])
        agents = {s["agent"] for s in steps}
        self.assertEqual(agents, {"planner", "coder", "reviewer", "tester"})

    def test_build_steps_default_planner_done(self) -> None:
        steps = sv.build_steps([])
        planner = next(s for s in steps if s["agent"] == "planner")
        self.assertEqual(planner["status"], "done")

    def test_build_steps_override_coder_status(self) -> None:
        steps = sv.build_steps([], status_overrides={"coder": "done"})
        coder = next(s for s in steps if s["agent"] == "coder")
        self.assertEqual(coder["status"], "done")

    def test_build_steps_preview_targets_in_details(self) -> None:
        plan = [{"action": "modify", "path": "app.py", "reason": "x"}]
        steps = sv.build_steps(plan)
        coder = next(s for s in steps if s["agent"] == "coder")
        self.assertIn("app.py", coder["details"])


# ─────────────────────────────────────────────────────────────────────────────
# elira_supervisor — DB persistence (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class SupervisorPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_db = sv.DB_PATH
        sv.DB_PATH = tmp / "supervisor.db"
        sv.ensure_db()

    def tearDown(self) -> None:
        sv.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_persist_and_list_run(self) -> None:
        run_id = sv.persist_run("fix bug", "code", "main.py", "planned", [], [], {})
        self.assertIsNotNone(run_id)
        result = sv.list_runs()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["goal"], "fix bug")

    def test_get_run_by_id(self) -> None:
        run_id = sv.persist_run(
            "feature", "code", None, "planned",
            [{"action": "create", "path": "x.py"}], [], {"key": "val"},
        )
        data = sv.get_run(run_id)
        self.assertEqual(data["goal"], "feature")
        self.assertIsInstance(data["plan"], list)
        self.assertEqual(data["summary"]["key"], "val")

    def test_get_run_not_found(self) -> None:
        data = sv.get_run(9999)
        self.assertEqual(data["status"], "not_found")

    def test_multiple_runs_newest_first(self) -> None:
        sv.persist_run("first", "code", None, "planned", [], [], {})
        sv.persist_run("second", "code", None, "planned", [], [], {})
        result = sv.list_runs()
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["goal"], "second")

    def test_list_runs_limit(self) -> None:
        for i in range(5):
            sv.persist_run(f"goal {i}", "code", None, "planned", [], [], {})
        result = sv.list_runs(limit=2)
        self.assertLessEqual(len(result["items"]), 2)


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — path helpers (patched PROJECT_ROOT)
# ─────────────────────────────────────────────────────────────────────────────

class PatchPathHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name) / "project"
        tmp.mkdir()
        self._orig_root = patch_rt.PROJECT_ROOT
        patch_rt.PROJECT_ROOT = tmp
        self._fake_root = tmp

    def tearDown(self) -> None:
        patch_rt.PROJECT_ROOT = self._orig_root
        self._tmpdir.cleanup()
        super().tearDown()

    def test_resolve_valid_path(self) -> None:
        path, err = patch_rt.resolve_project_path("src/app.py")
        self.assertIsNone(err)
        self.assertIsNotNone(path)

    def test_resolve_blocked_node_modules(self) -> None:
        path, err = patch_rt.resolve_project_path("node_modules/evil.js")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")

    def test_resolve_outside_root(self) -> None:
        path, err = patch_rt.resolve_project_path("../../etc/passwd")
        self.assertIsNone(path)
        self.assertEqual(err, "outside_root")

    def test_backup_file_path_no_slashes_in_name(self) -> None:
        backup = patch_rt.backup_file_path("src/main.py")
        self.assertTrue(str(backup).endswith(".bak"))
        self.assertNotIn("/", backup.name)
        self.assertNotIn("\\", backup.name)


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — diff helpers (pure, no FS)
# ─────────────────────────────────────────────────────────────────────────────

class DiffHelpersTest(unittest.TestCase):
    def test_diff_stats_added_removed(self) -> None:
        diff = "+added line\n-removed line\n context line"
        stats = patch_rt.diff_stats(diff)
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["removed"], 1)

    def test_diff_stats_skips_headers(self) -> None:
        diff = "+++ new file\n--- old file\n@@ -1,3 +1,3 @@\n+new\n-old"
        stats = patch_rt.diff_stats(diff)
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["removed"], 1)

    def test_diff_stats_empty(self) -> None:
        stats = patch_rt.diff_stats("")
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["removed"], 0)

    def test_compute_diff_unchanged(self) -> None:
        result = patch_rt.compute_diff("file.py", "same\n", "same\n")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stats"]["added"], 0)
        self.assertEqual(result["stats"]["removed"], 0)

    def test_compute_diff_changed(self) -> None:
        result = patch_rt.compute_diff("file.py", "old line\n", "new line\n")
        self.assertEqual(result["status"], "ok")
        total = result["stats"]["added"] + result["stats"]["removed"]
        self.assertGreater(total, 0)

    def test_compute_diff_has_diff_text(self) -> None:
        result = patch_rt.compute_diff("f.py", "a\n", "b\n")
        self.assertIn("diff_text", result)
        self.assertIn("a", result["diff_text"] + result["path"])


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — FS operations base (all paths patched)
# ─────────────────────────────────────────────────────────────────────────────

class PatchFSBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self._orig_root   = patch_rt.PROJECT_ROOT
        self._orig_data   = patch_rt.DATA_ROOT
        self._orig_backup = patch_rt.BACKUP_ROOT
        self._orig_db     = patch_rt.DB_PATH

        # Mirror real structure: DATA_ROOT and BACKUP_ROOT must be inside
        # PROJECT_ROOT so backup.relative_to(PROJECT_ROOT) succeeds.
        fake_root    = tmp / "project"
        fake_root.mkdir()
        fake_data    = fake_root / "data"
        fake_data.mkdir()
        fake_backups = fake_data / "patch_backups"
        fake_backups.mkdir()

        patch_rt.PROJECT_ROOT = fake_root
        patch_rt.DATA_ROOT    = fake_data
        patch_rt.BACKUP_ROOT  = fake_backups
        patch_rt.DB_PATH      = fake_data / "elira_state.db"

        patch_rt.ensure_db()
        self._fake_root = fake_root

    def tearDown(self) -> None:
        patch_rt.PROJECT_ROOT = self._orig_root
        patch_rt.DATA_ROOT    = self._orig_data
        patch_rt.BACKUP_ROOT  = self._orig_backup
        patch_rt.DB_PATH      = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def _write(self, rel: str, content: str) -> Path:
        target = self._fake_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — history CRUD
# ─────────────────────────────────────────────────────────────────────────────

class PatchHistoryTest(PatchFSBase):
    def test_write_and_list(self) -> None:
        item_id = patch_rt.write_history("file.py", "apply", "before", "after")
        self.assertIsNotNone(item_id)
        result = patch_rt.list_history()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["action"], "apply")

    def test_get_history_item(self) -> None:
        item_id = patch_rt.write_history("x.py", "rollback", "a\n", "b\n")
        data = patch_rt.get_history_item(item_id)
        self.assertEqual(data["action"], "rollback")
        self.assertIn("stats", data)

    def test_get_history_not_found(self) -> None:
        data = patch_rt.get_history_item(9999)
        self.assertEqual(data["status"], "not_found")

    def test_list_history_filtered_by_path(self) -> None:
        patch_rt.write_history("a.py", "apply", "", "a")
        patch_rt.write_history("b.py", "apply", "", "b")
        result = patch_rt.list_history(path="a.py")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["path"], "a.py")


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — apply / rollback
# ─────────────────────────────────────────────────────────────────────────────

class ApplyPatchTest(PatchFSBase):
    def test_apply_writes_new_content(self) -> None:
        self._write("app.py", "old content")
        result, err = patch_rt.apply_patch("app.py", "new content")
        self.assertIsNone(err)
        self.assertEqual(result["status"], "ok")
        self.assertEqual((self._fake_root / "app.py").read_text(encoding="utf-8"), "new content")

    def test_apply_creates_backup(self) -> None:
        self._write("main.py", "original")
        patch_rt.apply_patch("main.py", "patched")
        backup = patch_rt.backup_file_path("main.py")
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), "original")

    def test_apply_records_history(self) -> None:
        self._write("hist.py", "before")
        result, err = patch_rt.apply_patch("hist.py", "after")
        self.assertIsNone(err)
        history = patch_rt.list_history(path="hist.py")
        self.assertEqual(len(history["items"]), 1)

    def test_apply_not_found(self) -> None:
        result, err = patch_rt.apply_patch("ghost.py", "x")
        self.assertIsNone(result)
        self.assertEqual(err, "not_found")

    def test_apply_blocked_path(self) -> None:
        result, err = patch_rt.apply_patch("node_modules/evil.js", "x")
        self.assertIsNone(result)
        self.assertEqual(err, "blocked")

    def test_rollback_restores_original(self) -> None:
        self._write("roll.py", "original")
        patch_rt.apply_patch("roll.py", "patched")
        result, err = patch_rt.rollback_patch("roll.py")
        self.assertIsNone(err)
        self.assertEqual(result["status"], "ok")
        self.assertEqual((self._fake_root / "roll.py").read_text(encoding="utf-8"), "original")

    def test_rollback_no_backup_returns_error(self) -> None:
        self._write("nobackup.py", "content")
        result, err = patch_rt.rollback_patch("nobackup.py")
        self.assertIsNone(result)
        self.assertEqual(err, "no_backup")


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — verify
# ─────────────────────────────────────────────────────────────────────────────

class VerifyPatchTest(PatchFSBase):
    def test_verify_unchanged(self) -> None:
        self._write("v.py", "content\n")
        result, err = patch_rt.verify_patch("v.py", "content\n")
        self.assertIsNone(err)
        self.assertFalse(result["changed_vs_disk"])

    def test_verify_changed(self) -> None:
        self._write("v2.py", "disk content\n")
        result, err = patch_rt.verify_patch("v2.py", "proposed content\n")
        self.assertIsNone(err)
        self.assertTrue(result["changed_vs_disk"])

    def test_verify_none_content_uses_disk(self) -> None:
        self._write("v3.py", "disk only\n")
        result, err = patch_rt.verify_patch("v3.py", None)
        self.assertIsNone(err)
        self.assertFalse(result["changed_vs_disk"])

    def test_verify_has_checks_list(self) -> None:
        self._write("v4.py", "x\n")
        result, _ = patch_rt.verify_patch("v4.py", "x\n")
        self.assertIsInstance(result["checks"], list)
        self.assertGreater(len(result["checks"]), 0)

    def test_verify_not_found(self) -> None:
        result, err = patch_rt.verify_patch("missing.py", "x")
        self.assertIsNone(result)
        self.assertEqual(err, "not_found")


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — batch apply / verify
# ─────────────────────────────────────────────────────────────────────────────

class BatchPatchTest(PatchFSBase):
    def test_batch_apply_success(self) -> None:
        self._write("f1.py", "old1")
        self._write("f2.py", "old2")
        items = [{"path": "f1.py", "content": "new1"}, {"path": "f2.py", "content": "new2"}]
        result, err, _ = patch_rt.batch_apply(items)
        self.assertIsNone(err)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 2)
        self.assertEqual((self._fake_root / "f1.py").read_text(encoding="utf-8"), "new1")

    def test_batch_apply_not_found_aborts_early(self) -> None:
        items = [{"path": "ghost.py", "content": "x"}]
        result, err, path = patch_rt.batch_apply(items)
        self.assertIsNone(result)
        self.assertEqual(err, "not_found")
        self.assertEqual(path, "ghost.py")

    def test_batch_verify_unchanged(self) -> None:
        self._write("g.py", "same\n")
        items = [{"path": "g.py", "content": "same\n"}]
        result, err, _ = patch_rt.batch_verify(items)
        self.assertIsNone(err)
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["items"][0]["changed_vs_disk"])

    def test_batch_verify_changed(self) -> None:
        self._write("h.py", "original\n")
        items = [{"path": "h.py", "content": "changed\n"}]
        result, _, _ = patch_rt.batch_verify(items)
        self.assertTrue(result["items"][0]["changed_vs_disk"])

    def test_batch_verify_summary_stats(self) -> None:
        self._write("s.py", "a\n")
        items = [{"path": "s.py", "content": "b\n"}]
        result, _, _ = patch_rt.batch_verify(items)
        self.assertIn("summary", result)
        total = result["summary"]["added"] + result["summary"]["removed"]
        self.assertGreater(total, 0)


if __name__ == "__main__":
    unittest.main()
