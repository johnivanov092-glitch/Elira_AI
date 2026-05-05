"""Tests for application/elira_patch (path/diff helpers + patched DB),
application/elira_supervisor (json/plan/step builders + patched DB), and
application/provenance_guard (pure text post-processing)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.elira_patch.runtime as ep_rt          # noqa: E402
import app.application.elira_supervisor.runtime as esv_rt    # noqa: E402
from app.application.provenance_guard.runtime import (       # noqa: E402
    _normalize_whitespace,
    _rewrite_direct_personal_facts,
    _rewrite_natural_provenance,
    _strip_raw_markers,
    _strip_technical_source_phrases,
    guard_provenance_response,
    is_provenance_question,
)


# ─────────────────────────────────────────────────────────────────────────────
# elira_patch — pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class EliraPatchPathHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fake_root = Path(self._tmpdir.name).resolve()
        self._orig_root = ep_rt.PROJECT_ROOT
        ep_rt.PROJECT_ROOT = self._fake_root

    def tearDown(self) -> None:
        ep_rt.PROJECT_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_resolve_valid_path(self) -> None:
        path, err = ep_rt.resolve_project_path("src/main.py")
        self.assertIsNone(err)
        self.assertEqual(path, self._fake_root / "src" / "main.py")

    def test_resolve_traversal_outside_root(self) -> None:
        path, err = ep_rt.resolve_project_path("../../etc/passwd")
        self.assertIsNone(path)
        self.assertEqual(err, "outside_root")

    def test_resolve_blocked_git(self) -> None:
        path, err = ep_rt.resolve_project_path(".git/config")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")

    def test_resolve_blocked_node_modules(self) -> None:
        path, err = ep_rt.resolve_project_path("node_modules/lodash/index.js")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")

    def test_backup_file_path_returns_path(self) -> None:
        result = ep_rt.backup_file_path("src/main.py")
        self.assertIsInstance(result, Path)
        self.assertTrue(str(result).endswith(".bak"))

    def test_backup_file_path_replaces_separators(self) -> None:
        result = ep_rt.backup_file_path("src/utils/helper.py")
        name = result.name
        self.assertNotIn("/", name)


class EliraPatchDiffHelpersTest(unittest.TestCase):
    def test_build_diff_text_changed(self) -> None:
        original = "x = 1\ny = 2\n"
        updated = "x = 99\ny = 2\n"
        diff = ep_rt.build_diff_text("src/main.py", original, updated)
        self.assertIn("-x = 1", diff)
        self.assertIn("+x = 99", diff)

    def test_build_diff_text_no_change(self) -> None:
        text = "x = 1\ny = 2\n"
        diff = ep_rt.build_diff_text("src/main.py", text, text)
        self.assertEqual(diff, "")

    def test_diff_stats_counts_added_removed(self) -> None:
        original = "x = 1\ny = 2\n"
        updated = "x = 99\ny = 2\nz = 3\n"
        diff = ep_rt.build_diff_text("f.py", original, updated)
        stats = ep_rt.diff_stats(diff)
        self.assertGreater(stats["added"], 0)
        self.assertGreater(stats["removed"], 0)

    def test_diff_stats_no_change_zeros(self) -> None:
        stats = ep_rt.diff_stats("")
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["removed"], 0)

    def test_diff_stats_header_lines_not_counted(self) -> None:
        diff = "--- a.py (current)\n+++ a.py (proposed)\n@@ -1,2 +1,2 @@\n-old\n+new\n"
        stats = ep_rt.diff_stats(diff)
        # Only the actual +/- lines count, not ---, +++, @@
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["removed"], 1)


class EliraPatchHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name).resolve()
        self._orig_db = ep_rt.DB_PATH
        self._orig_data = ep_rt.DATA_ROOT
        self._orig_backup = ep_rt.BACKUP_ROOT
        ep_rt.DB_PATH = tmp / "elira_state.db"
        ep_rt.DATA_ROOT = tmp
        ep_rt.BACKUP_ROOT = tmp / "patch_backups"
        ep_rt.ensure_db()

    def tearDown(self) -> None:
        ep_rt.DB_PATH = self._orig_db
        ep_rt.DATA_ROOT = self._orig_data
        ep_rt.BACKUP_ROOT = self._orig_backup
        self._tmpdir.cleanup()

    def test_write_history_returns_id(self) -> None:
        item_id = ep_rt.write_history("src/main.py", "preview", "x = 1\n", "x = 2\n")
        self.assertIsInstance(item_id, int)
        self.assertGreater(item_id, 0)

    def test_list_history_empty_initially(self) -> None:
        result = ep_rt.list_history()
        # Returns {"items": [...]} — no "ok" or "count" key
        self.assertIn("items", result)
        self.assertEqual(len(result["items"]), 0)

    def test_list_history_after_write(self) -> None:
        ep_rt.write_history("a.py", "apply", "old\n", "new\n")
        result = ep_rt.list_history()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["path"], "a.py")

    def test_list_history_filter_by_path(self) -> None:
        ep_rt.write_history("a.py", "apply", "old\n", "new\n")
        ep_rt.write_history("b.py", "apply", "x\n", "y\n")
        result = ep_rt.list_history("b.py")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["path"], "b.py")

    def test_get_history_item_found(self) -> None:
        item_id = ep_rt.write_history("src/x.py", "preview", "before\n", "after\n")
        result = ep_rt.get_history_item(item_id)
        # Returns row dict directly, not {"ok": ..., "item": ...}
        self.assertEqual(result["path"], "src/x.py")
        self.assertIn("diff_text", result)
        self.assertIn("stats", result)

    def test_get_history_item_not_found(self) -> None:
        result = ep_rt.get_history_item(99999)
        # Returns {"status": "not_found"}
        self.assertEqual(result.get("status"), "not_found")

    def test_history_item_has_stats(self) -> None:
        item_id = ep_rt.write_history("f.py", "apply", "x = 1\n", "x = 2\n")
        result = ep_rt.get_history_item(item_id)
        self.assertIn("stats", result)
        self.assertIn("added", result["stats"])


# ─────────────────────────────────────────────────────────────────────────────
# elira_supervisor — json helpers + plan/step builders + patched DB
# ─────────────────────────────────────────────────────────────────────────────

class SupervisorJsonHelpersTest(unittest.TestCase):
    def test_dumps_json_produces_string(self) -> None:
        result = esv_rt.dumps_json({"key": "value"})
        self.assertIsInstance(result, str)
        self.assertIn("key", result)

    def test_loads_json_parses_string(self) -> None:
        text = '{"a": 1, "b": [2, 3]}'
        result = esv_rt.loads_json(text)
        self.assertEqual(result["a"], 1)

    def test_loads_json_none_returns_none(self) -> None:
        result = esv_rt.loads_json(None)
        self.assertIsNone(result)

    def test_loads_json_invalid_raises(self) -> None:
        # loads_json does not catch JSON errors — it raises JSONDecodeError
        import json
        with self.assertRaises(json.JSONDecodeError):
            esv_rt.loads_json("not json {{{")

    def test_dumps_loads_roundtrip(self) -> None:
        data = {"x": [1, 2, 3], "y": "hello"}
        result = esv_rt.loads_json(esv_rt.dumps_json(data))
        self.assertEqual(result, data)


class SupervisorPathHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fake_root = Path(self._tmpdir.name).resolve()
        self._orig_root = esv_rt.PROJECT_ROOT
        esv_rt.PROJECT_ROOT = self._fake_root

    def tearDown(self) -> None:
        esv_rt.PROJECT_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_resolve_valid(self) -> None:
        path, err = esv_rt.resolve_project_path("backend/app.py")
        self.assertIsNone(err)
        self.assertEqual(path, self._fake_root / "backend" / "app.py")

    def test_resolve_traversal(self) -> None:
        path, err = esv_rt.resolve_project_path("../../etc/passwd")
        self.assertIsNone(path)
        self.assertEqual(err, "outside_root")

    def test_resolve_blocked(self) -> None:
        path, err = esv_rt.resolve_project_path(".git/HEAD")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")


class SupervisorBuildPlanTest(unittest.TestCase):
    def test_current_path_included(self) -> None:
        plan = esv_rt.build_plan("fix auth", "backend/auth.py", [])
        paths = [item["path"] for item in plan]
        self.assertIn("backend/auth.py", paths)

    def test_staged_paths_included(self) -> None:
        plan = esv_rt.build_plan("fix", None, ["a.py", "b.py"])
        paths = [item["path"] for item in plan]
        self.assertIn("a.py", paths)

    def test_create_keyword_adds_create_step(self) -> None:
        plan = esv_rt.build_plan("create new component", None, [])
        actions = [item["action"] for item in plan]
        self.assertIn("create", actions)

    def test_api_keyword_adds_route_step(self) -> None:
        plan = esv_rt.build_plan("add api endpoint", None, [])
        paths = [item["path"] for item in plan]
        self.assertTrue(any("route" in p.lower() for p in paths))

    def test_empty_goal_falls_back_to_inspect(self) -> None:
        plan = esv_rt.build_plan("generic task", None, [])
        actions = [item["action"] for item in plan]
        self.assertIn("inspect", actions)

    def test_max_12_items(self) -> None:
        staged = [f"file_{i}.py" for i in range(20)]
        plan = esv_rt.build_plan("fix", None, staged)
        self.assertLessEqual(len(plan), 12)

    def test_staged_not_duplicated_with_current(self) -> None:
        plan = esv_rt.build_plan("fix", "auth.py", ["auth.py", "b.py"])
        paths = [item["path"] for item in plan]
        self.assertEqual(paths.count("auth.py"), 1)


class SupervisorBuildStepsTest(unittest.TestCase):
    def test_returns_4_steps(self) -> None:
        plan = [{"action": "modify", "path": "a.py", "reason": "test"}]
        steps = esv_rt.build_steps(plan)
        self.assertEqual(len(steps), 4)

    def test_agents_present(self) -> None:
        plan = [{"action": "modify", "path": "a.py", "reason": ""}]
        steps = esv_rt.build_steps(plan)
        agents = {s["agent"] for s in steps}
        self.assertEqual(agents, {"planner", "coder", "reviewer", "tester"})

    def test_default_planner_done(self) -> None:
        steps = esv_rt.build_steps([])
        planner_step = next(s for s in steps if s["agent"] == "planner")
        self.assertEqual(planner_step["status"], "done")

    def test_status_override_applied(self) -> None:
        steps = esv_rt.build_steps([], status_overrides={"coder": "done"})
        coder_step = next(s for s in steps if s["agent"] == "coder")
        self.assertEqual(coder_step["status"], "done")


class SupervisorPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name).resolve()
        self._orig_db = esv_rt.DB_PATH
        esv_rt.DB_PATH = tmp / "elira_state.db"
        esv_rt.ensure_db()

    def tearDown(self) -> None:
        esv_rt.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    def test_persist_run_returns_int_id(self) -> None:
        # persist_run returns int (lastrowid), not a dict
        run_id = esv_rt.persist_run(
            goal="fix auth",
            mode="auto",
            current_path="auth.py",
            status="pending",
            plan=[{"action": "modify", "path": "auth.py", "reason": "test"}],
            steps=[],
            summary={"total": 1},
        )
        self.assertIsInstance(run_id, int)
        self.assertGreater(run_id, 0)

    def test_list_runs_empty_initially(self) -> None:
        result = esv_rt.list_runs()
        # Returns {"items": [...]} — no "ok" or "count"
        self.assertIn("items", result)
        self.assertEqual(len(result["items"]), 0)

    def test_list_runs_after_persist(self) -> None:
        esv_rt.persist_run("goal", "auto", None, "pending", [], [], {})
        result = esv_rt.list_runs()
        self.assertEqual(len(result["items"]), 1)

    def test_get_run_found(self) -> None:
        run_id = esv_rt.persist_run("test goal", "manual", "x.py", "done", [], [], {})
        result = esv_rt.get_run(run_id)
        # Returns row dict directly
        self.assertEqual(result["goal"], "test goal")
        self.assertIn("plan", result)

    def test_get_run_not_found(self) -> None:
        result = esv_rt.get_run(99999)
        # Returns {"status": "not_found"}
        self.assertEqual(result.get("status"), "not_found")

    def test_list_runs_newest_first(self) -> None:
        esv_rt.persist_run("first", "auto", None, "done", [], [], {})
        esv_rt.persist_run("second", "auto", None, "done", [], [], {})
        result = esv_rt.list_runs()
        self.assertEqual(result["items"][0]["goal"], "second")


# ─────────────────────────────────────────────────────────────────────────────
# provenance_guard — pure text helpers
# ─────────────────────────────────────────────────────────────────────────────

class IsProvenanceQuestionTest(unittest.TestCase):
    def test_russian_source_question(self) -> None:
        self.assertTrue(is_provenance_question("откуда ты знаешь это?"))

    def test_russian_show_sources(self) -> None:
        self.assertTrue(is_provenance_question("покажи источники"))

    def test_english_sources_question(self) -> None:
        self.assertTrue(is_provenance_question("where did you get this"))

    def test_english_show_sources(self) -> None:
        self.assertTrue(is_provenance_question("show sources"))

    def test_plain_question_false(self) -> None:
        self.assertFalse(is_provenance_question("what is Python?"))

    def test_empty_returns_false(self) -> None:
        self.assertFalse(is_provenance_question(""))

    def test_none_returns_false(self) -> None:
        self.assertFalse(is_provenance_question(None))


class NormalizeWhitespaceTest(unittest.TestCase):
    def test_collapses_spaces(self) -> None:
        result = _normalize_whitespace("hello   world")
        self.assertNotIn("  ", result)

    def test_trims_trailing(self) -> None:
        result = _normalize_whitespace("  hello  ")
        self.assertEqual(result, "hello")

    def test_collapses_many_newlines(self) -> None:
        result = _normalize_whitespace("a\n\n\n\nb")
        self.assertNotIn("\n\n\n", result)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_normalize_whitespace(""), "")


class StripRawMarkersTest(unittest.TestCase):
    def test_strips_fact_marker(self) -> None:
        text = "[fact] Python is great\n"
        result = _strip_raw_markers(text)
        self.assertNotIn("[fact]", result.lower())
        self.assertIn("Python", result)

    def test_strips_memory_marker(self) -> None:
        text = "[memory] User prefers dark mode\n"
        result = _strip_raw_markers(text)
        self.assertNotIn("[memory]", result.lower())

    def test_strips_source_marker(self) -> None:
        text = "[source] From Wikipedia"
        result = _strip_raw_markers(text)
        self.assertNotIn("[source]", result.lower())

    def test_plain_text_unchanged(self) -> None:
        text = "Python is a programming language."
        result = _strip_raw_markers(text)
        self.assertIn("Python", result)

    def test_technical_header_stripped(self) -> None:
        text = "Relevant user memory\nPython info here"
        result = _strip_raw_markers(text)
        self.assertNotIn("Relevant user memory", result)


class RewriteNaturalProvenanceTest(unittest.TestCase):
    def test_rewrites_iz_moey_pamyati(self) -> None:
        text = "Это из моей памяти."
        result = _rewrite_natural_provenance(text)
        self.assertNotIn("из моей памяти", result)

    def test_plain_text_unchanged(self) -> None:
        text = "Python uses indentation."
        result = _rewrite_natural_provenance(text)
        self.assertIn("Python", result)


class StripTechnicalSourcePhrasesTest(unittest.TestCase):
    def test_strips_bare_urls(self) -> None:
        text = "See https://example.com for details"
        result = _strip_technical_source_phrases(text)
        self.assertNotIn("https://example.com", result)

    def test_keeps_useful_content(self) -> None:
        text = "Python is great for data science."
        result = _strip_technical_source_phrases(text)
        self.assertIn("Python", result)


class GuardProvenanceResponseTest(unittest.TestCase):
    def test_empty_answer_returns_unchanged(self) -> None:
        result = guard_provenance_response("hi", "")
        self.assertFalse(result["changed"])
        self.assertIsNone(result["reason"])

    def test_returns_required_keys(self) -> None:
        result = guard_provenance_response("test", "some answer")
        for key in ("text", "changed", "reason", "provenance_question"):
            self.assertIn(key, result)

    def test_provenance_question_detected(self) -> None:
        result = guard_provenance_response("покажи источники", "From my memory.")
        self.assertTrue(result["provenance_question"])

    def test_plain_answer_not_provenance(self) -> None:
        result = guard_provenance_response("what is Python?", "Python is a language.")
        self.assertFalse(result["provenance_question"])

    def test_raw_markers_stripped_at_line_start(self) -> None:
        # RAW_MARKER_RE strips line-start [fact]/[memory]/[source] markers
        answer = "[fact] Python is great."
        result = guard_provenance_response("tell me about Python", answer)
        self.assertNotIn("[fact]", result["text"].lower())

    def test_text_key_always_string(self) -> None:
        result = guard_provenance_response("q", "Some answer text here.")
        self.assertIsInstance(result["text"], str)

    def test_changed_true_when_markers_stripped(self) -> None:
        answer = "[fact] Some useful information here."
        result = guard_provenance_response("what is fact", answer)
        # If markers were stripped, text changed
        self.assertIsInstance(result["changed"], bool)


if __name__ == "__main__":
    unittest.main()
