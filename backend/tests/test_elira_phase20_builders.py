"""Tests for pure builder functions in app.application.elira_phase20.runtime.

All builder functions are pure (no DB, no FS, no HTTP) - testable directly.

Covers:
  BLOCKED_PARTS, ALLOWED_SUFFIXES constants
  dumps, loads (JSON round-trip helpers)
  build_reasoning, build_planner, build_coder,
  build_reviewer, build_tester, build_execution
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.elira_phase20.runtime import (  # noqa: E402
    BLOCKED_PARTS,
    ALLOWED_SUFFIXES,
    dumps,
    loads,
    build_reasoning,
    build_planner,
    build_coder,
    build_reviewer,
    build_tester,
    build_execution,
)


# ---
# Constants
# ---

class ConstantsTest(unittest.TestCase):
    def test_blocked_parts_is_set(self) -> None:
        self.assertIsInstance(BLOCKED_PARTS, (set, frozenset))

    def test_blocked_parts_contains_git(self) -> None:
        self.assertIn(".git", BLOCKED_PARTS)

    def test_blocked_parts_contains_node_modules(self) -> None:
        self.assertIn("node_modules", BLOCKED_PARTS)

    def test_allowed_suffixes_is_set(self) -> None:
        self.assertIsInstance(ALLOWED_SUFFIXES, (set, frozenset))

    def test_allowed_suffixes_contains_py(self) -> None:
        self.assertIn(".py", ALLOWED_SUFFIXES)

    def test_allowed_suffixes_contains_ts(self) -> None:
        self.assertIn(".ts", ALLOWED_SUFFIXES)


# ---
# dumps / loads
# ---

class DumpsLoadsTest(unittest.TestCase):
    def test_dumps_returns_string(self) -> None:
        self.assertIsInstance(dumps({"key": "value"}), str)

    def test_dumps_roundtrips_dict(self) -> None:
        data = {"a": 1, "b": [2, 3]}
        self.assertEqual(loads(dumps(data)), data)

    def test_dumps_handles_unicode(self) -> None:
        data = {"text": "Привет мир"}
        result = dumps(data)
        self.assertIn("Привет", result)

    def test_loads_none_returns_none(self) -> None:
        self.assertIsNone(loads(None))

    def test_loads_empty_string_returns_none(self) -> None:
        self.assertIsNone(loads(""))

    def test_loads_valid_json(self) -> None:
        self.assertEqual(loads('{"x": 42}'), {"x": 42})

    def test_loads_list(self) -> None:
        self.assertEqual(loads("[1, 2, 3]"), [1, 2, 3])


# ---
# build_reasoning
# ---

class BuildReasoningTest(unittest.TestCase):
    def _call(self, goal: str = "refactor auth module", paths=None, files=None) -> dict:
        return build_reasoning(
            goal=goal,
            selected_paths=paths or ["backend/auth.py"],
            project_files=files or ["backend/main.py"],
        )

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_has_scope(self) -> None:
        self.assertIn("scope", self._call())

    def test_has_goal_summary(self) -> None:
        self.assertIn("goal_summary", self._call())

    def test_has_selected_paths(self) -> None:
        self.assertIn("selected_paths", self._call())

    def test_has_advice_list(self) -> None:
        result = self._call()
        self.assertIsInstance(result["advice"], list)
        self.assertGreater(len(result["advice"]), 0)

    def test_backend_scope_for_api_goal(self) -> None:
        result = self._call(goal="add new API endpoint for auth")
        self.assertEqual(result["scope"], "backend")

    def test_ui_scope_for_button_goal(self) -> None:
        result = self._call(goal="add a new button component")
        self.assertEqual(result["scope"], "ui")

    def test_multi_file_scope_default(self) -> None:
        result = self._call(goal="refactor database connections")
        self.assertEqual(result["scope"], "multi-file")

    def test_goal_summary_truncated_at_280(self) -> None:
        long_goal = "A" * 400
        result = build_reasoning(long_goal, [], [])
        self.assertLessEqual(len(result["goal_summary"]), 280)

    def test_selected_paths_stored(self) -> None:
        result = self._call(paths=["a.py", "b.py"])
        self.assertEqual(result["selected_paths"], ["a.py", "b.py"])

    def test_project_context_sample_capped_at_30(self) -> None:
        many_files = [f"file{i}.py" for i in range(50)]
        result = self._call(files=many_files)
        self.assertLessEqual(len(result["project_context_sample"]), 30)


# ---
# build_planner
# ---

class BuildPlannerTest(unittest.TestCase):
    def _call(self, goal: str = "fix bug", paths=None, files=None) -> dict:
        return build_planner(
            goal=goal,
            selected_paths=paths or ["backend/app.py"],
            project_files=files or ["backend/main.py"],
        )

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_has_status_done(self) -> None:
        self.assertEqual(self._call()["status"], "done")

    def test_has_items_list(self) -> None:
        self.assertIsInstance(self._call()["items"], list)

    def test_selected_paths_become_modify_actions(self) -> None:
        result = self._call(paths=["a.py"])
        actions = [item["action"] for item in result["items"]]
        self.assertIn("modify", actions)

    def test_create_goal_adds_jsx_file(self) -> None:
        result = self._call(goal="create new component for dashboard")
        paths = [item["path"] for item in result["items"]]
        self.assertTrue(any(".jsx" in p for p in paths))

    def test_api_goal_adds_route_file(self) -> None:
        result = self._call(goal="add backend route for users API")
        paths = [item["path"] for item in result["items"]]
        self.assertTrue(any("route" in p for p in paths))

    def test_empty_paths_uses_first_project_file(self) -> None:
        result = build_planner("fix bug", [], ["fallback.py"])
        actions = [item["action"] for item in result["items"]]
        self.assertIn("inspect", actions)

    def test_items_capped_at_14(self) -> None:
        many_paths = [f"file{i}.py" for i in range(20)]
        result = self._call(paths=many_paths)
        self.assertLessEqual(len(result["items"]), 14)

    def test_each_item_has_action_and_path(self) -> None:
        result = self._call()
        for item in result["items"]:
            self.assertIn("action", item)
            self.assertIn("path", item)


# ---
# build_coder
# ---

class BuildCoderTest(unittest.TestCase):
    def _planner(self, items=None) -> dict:
        return {"status": "done", "items": items or [
            {"action": "modify", "path": "a.py", "reason": "x"},
            {"action": "create", "path": "b.py", "reason": "y"},
            {"action": "inspect", "path": "c.py", "reason": "z"},
        ]}

    def test_returns_dict(self) -> None:
        self.assertIsInstance(build_coder(self._planner()), dict)

    def test_status_ready(self) -> None:
        self.assertEqual(build_coder(self._planner())["status"], "ready")

    def test_has_operations(self) -> None:
        self.assertIsInstance(build_coder(self._planner())["operations"], list)

    def test_has_preview_targets(self) -> None:
        self.assertIsInstance(build_coder(self._planner())["preview_targets"], list)

    def test_modify_becomes_preview_edit(self) -> None:
        result = build_coder(self._planner())
        ops = [op["operation"] for op in result["operations"]]
        self.assertIn("preview-edit", ops)

    def test_create_becomes_create_file(self) -> None:
        result = build_coder(self._planner())
        ops = [op["operation"] for op in result["operations"]]
        self.assertIn("create-file", ops)

    def test_inspect_becomes_inspect(self) -> None:
        result = build_coder(self._planner())
        ops = [op["operation"] for op in result["operations"]]
        self.assertIn("inspect", ops)

    def test_modify_and_create_in_preview_targets(self) -> None:
        result = build_coder(self._planner())
        self.assertIn("a.py", result["preview_targets"])
        self.assertIn("b.py", result["preview_targets"])

    def test_empty_planner_empty_ops(self) -> None:
        result = build_coder({"items": []})
        self.assertEqual(result["operations"], [])


# ---
# build_reviewer
# ---

class BuildReviewerTest(unittest.TestCase):
    def _setup(self):
        planner = {"status": "done", "items": [
            {"action": "modify", "path": "a.py", "reason": "x"},
        ]}
        coder = build_coder(planner)
        return planner, coder

    def test_returns_dict(self) -> None:
        p, c = self._setup()
        self.assertIsInstance(build_reviewer(p, c), dict)

    def test_status_ready(self) -> None:
        p, c = self._setup()
        self.assertEqual(build_reviewer(p, c)["status"], "ready")

    def test_has_diff_targets(self) -> None:
        p, c = self._setup()
        result = build_reviewer(p, c)
        self.assertIsInstance(result["diff_targets"], list)

    def test_has_history_targets(self) -> None:
        p, c = self._setup()
        result = build_reviewer(p, c)
        self.assertIsInstance(result["history_targets"], list)

    def test_modify_paths_in_history_targets(self) -> None:
        p, c = self._setup()
        result = build_reviewer(p, c)
        self.assertIn("a.py", result["history_targets"])

    def test_notes_nonempty(self) -> None:
        p, c = self._setup()
        self.assertGreater(len(build_reviewer(p, c)["notes"]), 0)


# ---
# build_tester
# ---

class BuildTesterTest(unittest.TestCase):
    def _coder(self) -> dict:
        return {"status": "ready", "preview_targets": ["a.py", "b.py"], "operations": []}

    def test_returns_dict(self) -> None:
        self.assertIsInstance(build_tester(self._coder()), dict)

    def test_status_ready(self) -> None:
        self.assertEqual(build_tester(self._coder())["status"], "ready")

    def test_verify_targets_from_coder(self) -> None:
        result = build_tester(self._coder())
        self.assertEqual(result["verify_targets"], ["a.py", "b.py"])

    def test_checks_nonempty(self) -> None:
        self.assertGreater(len(build_tester(self._coder())["checks"]), 0)

    def test_empty_coder_empty_targets(self) -> None:
        result = build_tester({"preview_targets": []})
        self.assertEqual(result["verify_targets"], [])


# ---
# build_execution
# ---

class BuildExecutionTest(unittest.TestCase):
    def _build(self) -> dict:
        planner = {"status": "done", "items": [
            {"action": "modify", "path": "a.py", "reason": "x"},
        ]}
        coder = build_coder(planner)
        reviewer = build_reviewer(planner, coder)
        tester = build_tester(coder)
        return build_execution(planner, coder, reviewer, tester)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._build(), dict)

    def test_status_ready(self) -> None:
        self.assertEqual(self._build()["status"], "ready")

    def test_has_flow_list(self) -> None:
        self.assertIsInstance(self._build()["flow"], list)

    def test_flow_nonempty(self) -> None:
        self.assertGreater(len(self._build()["flow"]), 0)

    def test_has_preview_targets(self) -> None:
        self.assertIsInstance(self._build()["preview_targets"], list)

    def test_apply_recommended_true_when_targets(self) -> None:
        self.assertTrue(self._build()["apply_recommended"])

    def test_verify_recommended_true_when_targets(self) -> None:
        self.assertTrue(self._build()["verify_recommended"])

    def test_apply_recommended_false_when_no_targets(self) -> None:
        result = build_execution(
            {"items": []},
            {"preview_targets": [], "operations": []},
            {"diff_targets": [], "history_targets": [], "notes": []},
            {"verify_targets": []},
        )
        self.assertFalse(result["apply_recommended"])


if __name__ == "__main__":
    unittest.main()
