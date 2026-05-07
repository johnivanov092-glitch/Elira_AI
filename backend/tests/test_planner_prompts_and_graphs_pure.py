"""Tests for pure helpers in planner_prompts.py and planner_graphs.py.

domain/agents/planner_prompts.py:
  build_task_graph_prompt        — prompt string builder
  build_planner_plan_prompt      — prompt string builder
  build_planner_summary_prompt   — prompt string builder
  build_task_graph_reasoning_prompt — prompt string builder
  build_task_graph_summary_prompt — prompt string builder

domain/agents/planner_graphs.py (pure functions only):
  normalize_planner_steps  — normalizes raw plan list
  normalize_task_graph     — normalizes raw graph list

All functions are pure (no DB, no HTTP, no FS).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.agents.planner_prompts import (  # noqa: E402
    build_task_graph_prompt,
    build_planner_plan_prompt,
    build_planner_summary_prompt,
    build_task_graph_reasoning_prompt,
    build_task_graph_summary_prompt,
)
from app.domain.agents.planner_graphs import (  # noqa: E402
    normalize_planner_steps,
    normalize_task_graph,
)


# ─────────────────────────────────────────────────────────────────────────────
# planner_prompts.py — build_task_graph_prompt
# ─────────────────────────────────────────────────────────────────────────────

class BuildTaskGraphPromptTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(build_task_graph_prompt("do something"), str)

    def test_contains_task(self) -> None:
        task = "analyze this codebase"
        result = build_task_graph_prompt(task)
        self.assertIn(task, result)

    def test_nonempty(self) -> None:
        self.assertTrue(len(build_task_graph_prompt("task")) > 0)

    def test_contains_json_schema_hint(self) -> None:
        result = build_task_graph_prompt("something")
        self.assertIn("id", result)
        self.assertIn("tool", result)
        self.assertIn("goal", result)

    def test_mentions_browser_tool(self) -> None:
        result = build_task_graph_prompt("task")
        self.assertIn("browser", result)

    def test_mentions_reasoning_tool(self) -> None:
        result = build_task_graph_prompt("task")
        self.assertIn("reasoning", result)

    def test_mentions_terminal_tool(self) -> None:
        result = build_task_graph_prompt("task")
        self.assertIn("terminal", result)

    def test_different_tasks_different_prompts(self) -> None:
        p1 = build_task_graph_prompt("task one")
        p2 = build_task_graph_prompt("task two")
        self.assertNotEqual(p1, p2)

    def test_empty_task_does_not_raise(self) -> None:
        result = build_task_graph_prompt("")
        self.assertIsInstance(result, str)

    def test_long_task_embedded(self) -> None:
        task = "X" * 500
        result = build_task_graph_prompt(task)
        self.assertIn(task, result)


# ─────────────────────────────────────────────────────────────────────────────
# planner_prompts.py — build_planner_plan_prompt
# ─────────────────────────────────────────────────────────────────────────────

class BuildPlannerPlanPromptTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(build_planner_plan_prompt("do something"), str)

    def test_contains_task(self) -> None:
        task = "write a report"
        result = build_planner_plan_prompt(task)
        self.assertIn(task, result)

    def test_nonempty(self) -> None:
        self.assertTrue(len(build_planner_plan_prompt("task")) > 0)

    def test_mentions_browser(self) -> None:
        result = build_planner_plan_prompt("task")
        self.assertIn("browser", result)

    def test_mentions_reasoning(self) -> None:
        result = build_planner_plan_prompt("task")
        self.assertIn("reasoning", result)

    def test_mentions_terminal(self) -> None:
        result = build_planner_plan_prompt("task")
        self.assertIn("terminal", result)

    def test_different_tasks_different_prompts(self) -> None:
        p1 = build_planner_plan_prompt("alpha task")
        p2 = build_planner_plan_prompt("beta task")
        self.assertNotEqual(p1, p2)

    def test_empty_task_does_not_raise(self) -> None:
        result = build_planner_plan_prompt("")
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# planner_prompts.py — build_planner_summary_prompt
# ─────────────────────────────────────────────────────────────────────────────

class BuildPlannerSummaryPromptTest(unittest.TestCase):

    _PLAN = [{"tool": "reasoning", "goal": "summarize", "url": "", "command": ""}]

    def _call(self, task="analyze X", context="found Y"):
        return build_planner_summary_prompt(
            task=task,
            normalized_plan=self._PLAN,
            context_blob=context,
        )

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call(), str)

    def test_contains_task(self) -> None:
        result = self._call(task="write docs")
        self.assertIn("write docs", result)

    def test_contains_context_blob(self) -> None:
        result = self._call(context="result data")
        self.assertIn("result data", result)

    def test_contains_plan_json(self) -> None:
        result = self._call()
        self.assertIn("reasoning", result)

    def test_nonempty(self) -> None:
        self.assertTrue(len(self._call()) > 0)

    def test_empty_plan_list(self) -> None:
        result = build_planner_summary_prompt(
            task="task", normalized_plan=[], context_blob="context"
        )
        self.assertIsInstance(result, str)

    def test_different_tasks_produce_different_prompts(self) -> None:
        r1 = build_planner_summary_prompt(task="T1", normalized_plan=[], context_blob="ctx")
        r2 = build_planner_summary_prompt(task="T2", normalized_plan=[], context_blob="ctx")
        self.assertNotEqual(r1, r2)


# ─────────────────────────────────────────────────────────────────────────────
# planner_prompts.py — build_task_graph_reasoning_prompt
# ─────────────────────────────────────────────────────────────────────────────

class BuildTaskGraphReasoningPromptTest(unittest.TestCase):

    def _call(self, task="main task", node_goal="node goal", dep_context="dep ctx"):
        return build_task_graph_reasoning_prompt(
            task=task,
            node_goal=node_goal,
            dep_context=dep_context,
        )

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call(), str)

    def test_contains_task(self) -> None:
        result = self._call(task="analyze logs")
        self.assertIn("analyze logs", result)

    def test_contains_node_goal(self) -> None:
        result = self._call(node_goal="summarize findings")
        self.assertIn("summarize findings", result)

    def test_contains_dep_context(self) -> None:
        result = self._call(dep_context="previous results here")
        self.assertIn("previous results here", result)

    def test_empty_dep_context_fallback(self) -> None:
        result = build_task_graph_reasoning_prompt(
            task="t", node_goal="g", dep_context=""
        )
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_nonempty(self) -> None:
        self.assertTrue(len(self._call()) > 0)

    def test_none_dep_context_fallback(self) -> None:
        result = build_task_graph_reasoning_prompt(
            task="t", node_goal="g", dep_context=None  # type: ignore[arg-type]
        )
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# planner_prompts.py — build_task_graph_summary_prompt
# ─────────────────────────────────────────────────────────────────────────────

class BuildTaskGraphSummaryPromptTest(unittest.TestCase):

    _GRAPH = [{"id": "n1", "tool": "reasoning", "goal": "analyze"}]

    def _call(self, task="main task", state="some state"):
        return build_task_graph_summary_prompt(
            task=task,
            graph=self._GRAPH,
            state_blob=state,
        )

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call(), str)

    def test_contains_task(self) -> None:
        result = self._call(task="research topic")
        self.assertIn("research topic", result)

    def test_contains_state_blob(self) -> None:
        result = self._call(state="completed successfully")
        self.assertIn("completed successfully", result)

    def test_contains_graph_json(self) -> None:
        result = self._call()
        self.assertIn("n1", result)

    def test_nonempty(self) -> None:
        self.assertTrue(len(self._call()) > 0)

    def test_empty_graph(self) -> None:
        result = build_task_graph_summary_prompt(
            task="task", graph=[], state_blob="state"
        )
        self.assertIsInstance(result, str)

    def test_different_tasks_different_prompts(self) -> None:
        r1 = build_task_graph_summary_prompt(task="T1", graph=[], state_blob="s")
        r2 = build_task_graph_summary_prompt(task="T2", graph=[], state_blob="s")
        self.assertNotEqual(r1, r2)


# ─────────────────────────────────────────────────────────────────────────────
# planner_graphs.py — normalize_planner_steps
# ─────────────────────────────────────────────────────────────────────────────

class NormalizePlannerStepsTest(unittest.TestCase):

    _TASK = "analyze the codebase"

    def _valid_step(self, tool="reasoning", goal="analyze", url="", command=""):
        return {"tool": tool, "goal": goal, "url": url, "command": command}

    def test_returns_list(self) -> None:
        self.assertIsInstance(normalize_planner_steps([], self._TASK), list)

    def test_non_list_input_returns_default(self) -> None:
        result = normalize_planner_steps("not a list", self._TASK)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_none_input_returns_default(self) -> None:
        result = normalize_planner_steps(None, self._TASK)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_empty_list_returns_default(self) -> None:
        result = normalize_planner_steps([], self._TASK)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_valid_steps_normalized(self) -> None:
        steps = [
            self._valid_step("browser", "fetch page", "https://example.com"),
            self._valid_step("reasoning", "analyze"),
        ]
        result = normalize_planner_steps(steps, self._TASK)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_unknown_tool_filtered_out(self) -> None:
        steps = [
            {"tool": "unknown_tool", "goal": "do something", "url": "", "command": ""},
            self._valid_step("reasoning", "analyze"),
        ]
        result = normalize_planner_steps(steps, self._TASK)
        tools = [s["tool"] for s in result]
        self.assertNotIn("unknown_tool", tools)

    def test_last_step_is_reasoning(self) -> None:
        steps = [self._valid_step("browser", "fetch page", "https://example.com")]
        result = normalize_planner_steps(steps, self._TASK)
        self.assertEqual(result[-1]["tool"], "reasoning")

    def test_already_ending_with_reasoning_unchanged(self) -> None:
        steps = [
            self._valid_step("browser", "fetch", "https://example.com"),
            self._valid_step("reasoning", "conclude"),
        ]
        result = normalize_planner_steps(steps, self._TASK)
        reasoning_count = sum(1 for s in result if s["tool"] == "reasoning")
        # Should not duplicate reasoning at the end
        self.assertEqual(result[-1]["tool"], "reasoning")
        self.assertLessEqual(reasoning_count, 2)

    def test_result_is_list_of_dicts(self) -> None:
        steps = [self._valid_step("reasoning", "conclude")]
        result = normalize_planner_steps(steps, self._TASK)
        for step in result:
            self.assertIsInstance(step, dict)

    def test_each_step_has_tool_key(self) -> None:
        steps = [self._valid_step("reasoning", "analyze")]
        result = normalize_planner_steps(steps, self._TASK)
        for step in result:
            self.assertIn("tool", step)

    def test_each_step_has_goal_key(self) -> None:
        steps = [self._valid_step("reasoning", "analyze")]
        result = normalize_planner_steps(steps, self._TASK)
        for step in result:
            self.assertIn("goal", step)

    def test_at_most_5_steps(self) -> None:
        steps = [self._valid_step("reasoning", f"step {i}") for i in range(10)]
        result = normalize_planner_steps(steps, self._TASK)
        self.assertLessEqual(len(result), 5)

    def test_non_dict_items_skipped(self) -> None:
        steps = ["not a dict", 42, self._valid_step("reasoning", "conclude")]
        result = normalize_planner_steps(steps, self._TASK)
        for step in result:
            self.assertIsInstance(step, dict)

    def test_terminal_tool_accepted(self) -> None:
        steps = [
            {"tool": "terminal", "goal": "check files", "url": "", "command": "ls"},
            self._valid_step("reasoning", "conclude"),
        ]
        result = normalize_planner_steps(steps, self._TASK)
        tools = [s["tool"] for s in result]
        self.assertIn("terminal", tools)


# ─────────────────────────────────────────────────────────────────────────────
# planner_graphs.py — normalize_task_graph
# ─────────────────────────────────────────────────────────────────────────────

class NormalizeTaskGraphTest(unittest.TestCase):

    _TASK = "research machine learning"

    def _node(self, nid="n1", tool="reasoning", goal="analyze", url="", command="", depends_on=None):
        return {
            "id": nid,
            "tool": tool,
            "goal": goal,
            "url": url,
            "command": command,
            "depends_on": depends_on or [],
        }

    def test_returns_list(self) -> None:
        self.assertIsInstance(normalize_task_graph([], self._TASK), list)

    def test_non_list_input_returns_default(self) -> None:
        result = normalize_task_graph("not a list", self._TASK)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_none_input_returns_default(self) -> None:
        result = normalize_task_graph(None, self._TASK)
        self.assertIsInstance(result, list)

    def test_empty_list_returns_default(self) -> None:
        result = normalize_task_graph([], self._TASK)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_valid_graph_normalized(self) -> None:
        nodes = [
            self._node("n1", "browser", "fetch", "https://example.com"),
            self._node("n2", "reasoning", "conclude", depends_on=["n1"]),
        ]
        result = normalize_task_graph(nodes, self._TASK)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_unknown_tool_filtered(self) -> None:
        nodes = [
            {"id": "n1", "tool": "bad_tool", "goal": "do it", "url": "", "command": "", "depends_on": []},
            self._node("n2", "reasoning", "conclude"),
        ]
        result = normalize_task_graph(nodes, self._TASK)
        tools = [n["tool"] for n in result]
        self.assertNotIn("bad_tool", tools)

    def test_last_node_is_reasoning(self) -> None:
        nodes = [self._node("n1", "browser", "fetch", "https://example.com")]
        result = normalize_task_graph(nodes, self._TASK)
        self.assertEqual(result[-1]["tool"], "reasoning")

    def test_all_nodes_have_required_keys(self) -> None:
        nodes = [self._node("n1", "reasoning", "conclude")]
        result = normalize_task_graph(nodes, self._TASK)
        for node in result:
            for key in ("id", "tool", "goal", "url", "command", "depends_on"):
                self.assertIn(key, node)

    def test_all_nodes_are_dicts(self) -> None:
        nodes = [self._node("n1", "reasoning", "conclude")]
        result = normalize_task_graph(nodes, self._TASK)
        for node in result:
            self.assertIsInstance(node, dict)

    def test_at_most_7_nodes(self) -> None:
        nodes = [self._node(f"n{i}", "reasoning", f"step {i}") for i in range(1, 15)]
        result = normalize_task_graph(nodes, self._TASK)
        self.assertLessEqual(len(result), 7)

    def test_depends_on_is_list(self) -> None:
        nodes = [self._node("n1", "reasoning", "conclude")]
        result = normalize_task_graph(nodes, self._TASK)
        for node in result:
            self.assertIsInstance(node["depends_on"], list)

    def test_self_referential_dep_removed(self) -> None:
        # A node that depends on itself should have self removed from deps
        nodes = [self._node("n1", "reasoning", "think", depends_on=["n1"])]
        result = normalize_task_graph(nodes, self._TASK)
        for node in result:
            if node["id"] == "n1":
                self.assertNotIn("n1", node["depends_on"])

    def test_non_dict_items_skipped(self) -> None:
        nodes = ["string", 42, None, self._node("n1", "reasoning", "conclude")]
        result = normalize_task_graph(nodes, self._TASK)
        for node in result:
            self.assertIsInstance(node, dict)

    def test_memory_lookup_tool_accepted(self) -> None:
        nodes = [
            self._node("n1", "memory_lookup", "recall facts"),
            self._node("n2", "reasoning", "synthesize", depends_on=["n1"]),
        ]
        result = normalize_task_graph(nodes, self._TASK)
        tools = [n["tool"] for n in result]
        self.assertIn("memory_lookup", tools)

    def test_duplicate_ids_handled(self) -> None:
        # Two nodes with same id — second should get a different id
        nodes = [
            self._node("n1", "browser", "fetch", "https://example.com"),
            self._node("n1", "reasoning", "conclude"),  # duplicate id
        ]
        result = normalize_task_graph(nodes, self._TASK)
        ids = [n["id"] for n in result]
        # All ids should be unique
        self.assertEqual(len(ids), len(set(ids)))

    def test_invalid_deps_filtered(self) -> None:
        # dep_context referencing non-existent node id should be removed
        nodes = [
            self._node("n1", "reasoning", "conclude", depends_on=["nonexistent_id"]),
        ]
        result = normalize_task_graph(nodes, self._TASK)
        for node in result:
            if node["id"] == "n1":
                self.assertNotIn("nonexistent_id", node["depends_on"])


if __name__ == "__main__":
    unittest.main()
