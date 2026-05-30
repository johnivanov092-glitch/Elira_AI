"""Tests for pure helpers in domain/agents/orchestrator_runtime.py.

Covers:
  normalize_v8_route             - normalize/fallback route dict
  select_v8_graph                - pick graph node list by strategy/mode
  build_v8_state                 - build initial orchestrator state dict
  build_run_agent_v8_result      - build run-result dict
  build_self_improving_result    - build self-improve result dict

All functions are pure (no DB, no HTTP, no FS).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.agents.orchestrator_runtime import (  # noqa: E402
    normalize_v8_route,
    select_v8_graph,
    build_v8_state,
    build_run_agent_v8_result,
    build_self_improving_result,
)


# normalize_v8_route

class NormalizeV8RouteTest(unittest.TestCase):

    def test_returns_dict(self) -> None:
        self.assertIsInstance(normalize_v8_route({}), dict)

    def test_dict_returned_as_is(self) -> None:
        route = {"mode": "chat", "agent": "chat_agent"}
        result = normalize_v8_route(route)
        self.assertEqual(result["mode"], "chat")

    def test_none_returns_fallback(self) -> None:
        result = normalize_v8_route(None)
        self.assertIsInstance(result, dict)
        self.assertIn("mode", result)

    def test_string_returns_fallback(self) -> None:
        result = normalize_v8_route("not a dict")
        self.assertIsInstance(result, dict)
        self.assertIn("mode", result)

    def test_list_returns_fallback(self) -> None:
        result = normalize_v8_route([1, 2, 3])
        self.assertIsInstance(result, dict)

    def test_fallback_has_mode_key(self) -> None:
        result = normalize_v8_route(None)
        self.assertIn("mode", result)

    def test_fallback_has_confidence_key(self) -> None:
        result = normalize_v8_route(42)
        self.assertIn("confidence", result)

    def test_dict_returned_is_same_object(self) -> None:
        route = {"key": "value"}
        result = normalize_v8_route(route)
        self.assertIs(result, route)

    def test_empty_dict_returned_as_is(self) -> None:
        result = normalize_v8_route({})
        self.assertEqual(result, {})


# select_v8_graph

class SelectV8GraphTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(select_v8_graph("direct", "chat"), list)

    def test_direct_strategy_returns_list(self) -> None:
        result = select_v8_graph("direct", "chat")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_planner_strategy_returns_list(self) -> None:
        result = select_v8_graph("planner", "chat")
        self.assertIsInstance(result, list)
        self.assertIn("planner", result)

    def test_task_graph_strategy_returns_list(self) -> None:
        result = select_v8_graph("task_graph", "chat")
        self.assertIsInstance(result, list)
        self.assertIn("task_graph", result)

    def test_self_improve_strategy_returns_list(self) -> None:
        result = select_v8_graph("self_improve", "chat")
        self.assertIsInstance(result, list)
        self.assertIn("self_improve", result)

    def test_unknown_strategy_falls_back_to_mode(self) -> None:
        # Unknown strategy -> tries TASK_GRAPH_TEMPLATES_V8[mode]
        result = select_v8_graph("unknown_strategy", "chat")
        self.assertIsInstance(result, list)
        self.assertIn("finalize", result)

    def test_unknown_strategy_unknown_mode_returns_default(self) -> None:
        result = select_v8_graph("unknown_strategy", "unknown_mode")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("finalize", result)

    def test_result_contains_strings(self) -> None:
        for item in select_v8_graph("direct", "chat"):
            self.assertIsInstance(item, str)

    def test_all_graphs_end_with_finalize(self) -> None:
        for strategy in ("direct", "planner", "task_graph", "multi_agent", "self_improve"):
            result = select_v8_graph(strategy, "chat")
            self.assertEqual(result[-1], "finalize", f"{strategy} should end with finalize")

    def test_chat_mode_falls_back_to_template(self) -> None:
        result = select_v8_graph("not_a_strategy", "chat")
        self.assertIn("retrieve_memory", result)


# build_v8_state

class BuildV8StateTest(unittest.TestCase):

    def _call(self, **kwargs):
        defaults = dict(
            run_id="run-1",
            task="analyze code",
            model_name="llama3",
            memory_profile="default",
            route={"mode": "chat"},
            mode="chat",
            strategy={"name": "direct"},
            selected_strategy="direct",
            graph=["retrieve_memory", "finalize"],
        )
        defaults.update(kwargs)
        return build_v8_state(**defaults)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_run_id_stored(self) -> None:
        result = self._call(run_id="my-run")
        self.assertEqual(result["run_id"], "my-run")

    def test_task_stored(self) -> None:
        result = self._call(task="my task")
        self.assertEqual(result["task"], "my task")

    def test_model_name_stored(self) -> None:
        result = self._call(model_name="mistral")
        self.assertEqual(result["model_name"], "mistral")

    def test_selected_strategy_stored(self) -> None:
        result = self._call(selected_strategy="planner")
        self.assertEqual(result["selected_strategy"], "planner")

    def test_graph_stored(self) -> None:
        graph = ["retrieve_memory", "planner", "finalize"]
        result = self._call(graph=graph)
        self.assertEqual(result["graph"], graph)

    def test_answer_starts_empty(self) -> None:
        self.assertEqual(self._call()["answer"], "")

    def test_errors_starts_as_list(self) -> None:
        self.assertIsInstance(self._call()["errors"], list)
        self.assertEqual(self._call()["errors"], [])

    def test_timeline_starts_as_list(self) -> None:
        self.assertIsInstance(self._call()["timeline"], list)

    def test_reflection_starts_as_dict(self) -> None:
        self.assertIsInstance(self._call()["reflection"], dict)

    def test_required_keys_present(self) -> None:
        result = self._call()
        required = [
            "run_id", "task", "model_name", "memory_profile",
            "route", "mode", "strategy", "selected_strategy", "graph",
            "answer", "reflection", "errors", "timeline",
        ]
        for key in required:
            self.assertIn(key, result)


# build_run_agent_v8_result

class BuildRunAgentV8ResultTest(unittest.TestCase):

    def _state(self, answer="test answer", **extra):
        s = {
            "answer": answer,
            "reflection": {"complete": True},
            "task_graph_result": None,
            "plan_result": None,
            "multi_agent_result": None,
            "self_improve_result": None,
            "errors": [],
            "timeline": [],
            "failed_node": "",
        }
        s.update(extra)
        return s

    def _call(self, **kwargs):
        defaults = dict(
            run_id="run-1",
            mode="chat",
            route={"mode": "chat"},
            strategy={"name": "direct"},
            selected_strategy="direct",
            graph=["retrieve_memory", "finalize"],
            state=self._state(),
            latency=1.23,
            persona_meta=None,
        )
        defaults.update(kwargs)
        return build_run_agent_v8_result(**defaults)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_run_id_stored(self) -> None:
        result = self._call(run_id="my-run")
        self.assertEqual(result["run_id"], "my-run")

    def test_mode_stored(self) -> None:
        result = self._call(mode="research")
        self.assertEqual(result["mode"], "research")

    def test_answer_from_state(self) -> None:
        result = self._call(state=self._state(answer="great response"))
        self.assertEqual(result["answer"], "great response")

    def test_latency_stored(self) -> None:
        result = self._call(latency=2.5)
        self.assertAlmostEqual(result["latency_seconds"], 2.5)

    def test_errors_from_state(self) -> None:
        result = self._call(state=self._state(errors=["err1"]))
        self.assertIn("err1", result["errors"])

    def test_delegated_strategy_stored(self) -> None:
        result = self._call(selected_strategy="planner")
        self.assertEqual(result["delegated_strategy"], "planner")

    def test_required_keys_present(self) -> None:
        result = self._call()
        for key in ("run_id", "mode", "route", "answer", "latency_seconds", "errors", "timeline"):
            self.assertIn(key, result)

    def test_graph_stored(self) -> None:
        graph = ["a", "b", "c"]
        result = self._call(graph=graph)
        self.assertEqual(result["graph"], graph)


# build_self_improving_result

class BuildSelfImprovingResultTest(unittest.TestCase):

    def _base(self, **extra):
        b = {
            "mode": "chat",
            "route": {"mode": "chat"},
            "graph": ["finalize"],
            "timeline": [],
            "errors": [],
        }
        b.update(extra)
        return b

    def _call(self, **kwargs):
        defaults = dict(
            run_id="run-1",
            base=self._base(),
            answer="final answer",
            iterations=[{"step": 1, "score": 0.8}],
            reflection={"complete": True},
            persona_meta=None,
        )
        defaults.update(kwargs)
        return build_self_improving_result(**defaults)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_run_id_stored(self) -> None:
        result = self._call(run_id="my-run")
        self.assertEqual(result["run_id"], "my-run")

    def test_answer_stored(self) -> None:
        result = self._call(answer="great answer")
        self.assertEqual(result["answer"], "great answer")

    def test_iterations_stored(self) -> None:
        iters = [{"step": 1}, {"step": 2}]
        result = self._call(iterations=iters)
        self.assertEqual(result["iterations"], iters)

    def test_reflection_stored_as_final_reflection(self) -> None:
        refl = {"complete": True, "score": 0.9}
        result = self._call(reflection=refl)
        self.assertEqual(result["final_reflection"], refl)

    def test_mode_from_base(self) -> None:
        base = self._base(mode="research")
        result = self._call(base=base)
        self.assertEqual(result["mode"], "research")

    def test_required_keys_present(self) -> None:
        result = self._call()
        for key in ("run_id", "base", "answer", "iterations", "final_reflection",
                    "mode", "route", "graph", "errors", "timeline"):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
