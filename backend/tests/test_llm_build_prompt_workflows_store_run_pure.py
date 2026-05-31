"""Tests for pure helpers across three modules.

  core/llm.py
    — build_system_prompt
    — get_available_models

  application/workflows/store.py
    — _row_to_template
    — _row_to_run

  application/workflows/execution.py
    — build_workflow_execution_state

All functions are pure or use injectable callables (no real DB / HTTP needed).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.llm import build_system_prompt, get_available_models  # noqa: E402
from app.application.workflows.store import (  # noqa: E402
    _row_to_template,
    _row_to_run,
)
from app.application.workflows.execution import (  # noqa: E402
    build_workflow_execution_state,
    WorkflowExecutionState,
)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — build_system_prompt
# ─────────────────────────────────────────────────────────────────────────────

class BuildSystemPromptTest(unittest.TestCase):

    def _call(self, profile="Аналитик", file_ctx="", proj_ctx="", web_ctx="",
              mem_ctx="", use_web=False, use_memory=False) -> str:
        return build_system_prompt(profile, file_ctx, proj_ctx, web_ctx, mem_ctx,
                                   use_web, use_memory)

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call(), str)

    def test_nonempty_without_contexts(self) -> None:
        self.assertGreater(len(self._call()), 0)

    # ── file context ──────────────────────────────────────────────────────────

    def test_file_context_included(self) -> None:
        result = self._call(file_ctx="file content here")
        self.assertIn("file content here", result)

    def test_empty_file_context_not_injected(self) -> None:
        # If file_ctx is blank, the section should not appear
        result = self._call(file_ctx="")
        self.assertNotIn("Контекст из загруженных файлов", result)

    # ── project context ───────────────────────────────────────────────────────

    def test_project_context_included(self) -> None:
        result = self._call(proj_ctx="project info")
        self.assertIn("project info", result)

    def test_empty_project_context_not_injected(self) -> None:
        result = self._call(proj_ctx="")
        self.assertNotIn("Контекст из папки проекта", result)

    # ── web context gating ────────────────────────────────────────────────────

    def test_web_context_excluded_when_use_web_false(self) -> None:
        result = self._call(web_ctx="web data", use_web=False)
        self.assertNotIn("web data", result)

    def test_web_context_included_when_use_web_true(self) -> None:
        result = self._call(web_ctx="web data", use_web=True)
        self.assertIn("web data", result)

    def test_web_context_excluded_even_if_nonempty_and_flag_false(self) -> None:
        result = self._call(web_ctx="important web stuff", use_web=False)
        self.assertNotIn("important web stuff", result)

    # ── memory context gating ─────────────────────────────────────────────────

    def test_memory_context_excluded_when_use_memory_false(self) -> None:
        result = self._call(mem_ctx="memory data", use_memory=False)
        self.assertNotIn("memory data", result)

    def test_memory_context_included_when_use_memory_true(self) -> None:
        result = self._call(mem_ctx="memory data", use_memory=True)
        self.assertIn("memory data", result)

    # ── unknown profile fallback ──────────────────────────────────────────────

    def test_unknown_profile_returns_string(self) -> None:
        result = self._call(profile="NonExistentProfile999")
        self.assertIsInstance(result, str)

    def test_unknown_profile_result_nonempty(self) -> None:
        result = self._call(profile="NonExistentProfile999")
        self.assertGreater(len(result), 0)

    # ── all contexts together ─────────────────────────────────────────────────

    def test_all_contexts_combined(self) -> None:
        result = build_system_prompt(
            "Аналитик", "F", "P", "W", "M", True, True
        )
        for token in ("F", "P", "W", "M"):
            self.assertIn(token, result)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — get_available_models
# ─────────────────────────────────────────────────────────────────────────────

class GetAvailableModelsTest(unittest.TestCase):

    def test_returns_dict(self) -> None:
        self.assertIsInstance(get_available_models(), dict)

    def test_nonempty(self) -> None:
        # At minimum the static models from config must be present
        self.assertGreater(len(get_available_models()), 0)

    def test_all_keys_strings(self) -> None:
        for key in get_available_models():
            self.assertIsInstance(key, str)

    def test_all_values_strings(self) -> None:
        for val in get_available_models().values():
            self.assertIsInstance(val, str)

    def test_deterministic(self) -> None:
        # get_available_models is @lru_cache — same object returned
        self.assertIs(get_available_models(), get_available_models())


# ─────────────────────────────────────────────────────────────────────────────
# application/workflows/store.py — _row_to_template
# ─────────────────────────────────────────────────────────────────────────────

class RowToTemplateTest(unittest.TestCase):

    def _row(self, **kw) -> dict:
        defaults = {
            "graph_json": "{}",
            "input_schema_json": "{}",
            "output_schema_json": "{}",
            "enabled": 1,
        }
        defaults.update(kw)
        return defaults

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_row_to_template(None))

    def test_returns_dict(self) -> None:
        self.assertIsInstance(_row_to_template(self._row()), dict)

    def test_graph_json_parsed(self) -> None:
        result = _row_to_template(self._row(graph_json='{"steps": []}'))
        self.assertEqual(result["graph"], {"steps": []})

    def test_input_schema_json_parsed(self) -> None:
        result = _row_to_template(self._row(input_schema_json='{"type": "object"}'))
        self.assertEqual(result["input_schema"], {"type": "object"})

    def test_output_schema_json_parsed(self) -> None:
        result = _row_to_template(self._row(output_schema_json='{"type": "string"}'))
        self.assertEqual(result["output_schema"], {"type": "string"})

    def test_graph_json_key_removed(self) -> None:
        result = _row_to_template(self._row())
        self.assertNotIn("graph_json", result)

    def test_input_schema_json_key_removed(self) -> None:
        result = _row_to_template(self._row())
        self.assertNotIn("input_schema_json", result)

    def test_output_schema_json_key_removed(self) -> None:
        result = _row_to_template(self._row())
        self.assertNotIn("output_schema_json", result)

    def test_enabled_coerced_to_bool(self) -> None:
        result = _row_to_template(self._row(enabled=1))
        self.assertIsInstance(result["enabled"], bool)

    def test_enabled_truthy(self) -> None:
        result = _row_to_template(self._row(enabled=1))
        self.assertTrue(result["enabled"])

    def test_enabled_falsy(self) -> None:
        result = _row_to_template(self._row(enabled=0))
        self.assertFalse(result["enabled"])

    def test_other_fields_preserved(self) -> None:
        result = _row_to_template(self._row(name="my_wf", version="1.0"))
        self.assertEqual(result.get("name"), "my_wf")

    def test_invalid_graph_json_falls_back_to_empty_dict(self) -> None:
        result = _row_to_template(self._row(graph_json="{bad}"))
        self.assertEqual(result["graph"], {})


# ─────────────────────────────────────────────────────────────────────────────
# application/workflows/store.py — _row_to_run
# ─────────────────────────────────────────────────────────────────────────────

class RowToRunTest(unittest.TestCase):

    def _row(self, **kw) -> dict:
        defaults = {
            "input_json": "{}",
            "context_json": "{}",
            "step_results_json": "{}",
            "pending_steps_json": "[]",
            "error_json": "{}",
            "requested_pause": 0,
        }
        defaults.update(kw)
        return defaults

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_row_to_run(None))

    def test_returns_dict(self) -> None:
        self.assertIsInstance(_row_to_run(self._row()), dict)

    def test_input_json_parsed(self) -> None:
        result = _row_to_run(self._row(input_json='{"task": "go"}'))
        self.assertEqual(result["input"], {"task": "go"})

    def test_context_json_parsed(self) -> None:
        result = _row_to_run(self._row(context_json='{"profile": "dev"}'))
        self.assertEqual(result["context"], {"profile": "dev"})

    def test_step_results_json_parsed(self) -> None:
        result = _row_to_run(self._row(step_results_json='{"s1": "done"}'))
        self.assertEqual(result["step_results"], {"s1": "done"})

    def test_pending_steps_json_parsed(self) -> None:
        result = _row_to_run(self._row(pending_steps_json='["s2", "s3"]'))
        self.assertEqual(result["pending_steps"], ["s2", "s3"])

    def test_error_json_parsed(self) -> None:
        result = _row_to_run(self._row(error_json='{"msg": "oops"}'))
        self.assertEqual(result["error"], {"msg": "oops"})

    def test_input_json_key_removed(self) -> None:
        result = _row_to_run(self._row())
        self.assertNotIn("input_json", result)

    def test_pending_steps_json_key_removed(self) -> None:
        result = _row_to_run(self._row())
        self.assertNotIn("pending_steps_json", result)

    def test_requested_pause_coerced_to_bool(self) -> None:
        result = _row_to_run(self._row(requested_pause=1))
        self.assertIsInstance(result["requested_pause"], bool)

    def test_requested_pause_false_when_zero(self) -> None:
        result = _row_to_run(self._row(requested_pause=0))
        self.assertFalse(result["requested_pause"])

    def test_requested_pause_true_when_one(self) -> None:
        result = _row_to_run(self._row(requested_pause=1))
        self.assertTrue(result["requested_pause"])

    def test_invalid_input_json_falls_back_to_empty_dict(self) -> None:
        result = _row_to_run(self._row(input_json="not valid json"))
        self.assertEqual(result["input"], {})


# ─────────────────────────────────────────────────────────────────────────────
# application/workflows/execution.py — build_workflow_execution_state
# ─────────────────────────────────────────────────────────────────────────────

class BuildWorkflowExecutionStateTest(unittest.TestCase):

    def _simple_run(self, step_ids=("s1", "s2")) -> dict:
        return {
            "run_id": "r-test",
            "input": {"task": "do something"},
            "context": {"profile": "dev"},
            "step_results": {},
            "current_step_id": step_ids[0] if step_ids else "",
        }

    def _simple_template(self, step_ids=("s1", "s2")) -> dict:
        steps = [{"id": sid, "type": "agent", "agent_id": "builtin-universal"}
                 for sid in step_ids]
        return {"graph": {"steps": steps}}

    def _call(self, run=None, template=None, get_run=None):
        if run is None:
            run = self._simple_run()
        if template is None:
            template = self._simple_template()
        if get_run is None:
            get_run = lambda _: None
        return build_workflow_execution_state(
            run=run,
            template=template,
            get_workflow_run=get_run,
        )

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_workflow_execution_state(self) -> None:
        self.assertIsInstance(self._call(), WorkflowExecutionState)

    # ── ordered_ids ───────────────────────────────────────────────────────────

    def test_ordered_ids_from_template_steps(self) -> None:
        state = self._call()
        self.assertEqual(state.ordered_ids, ["s1", "s2"])

    def test_total_steps_count(self) -> None:
        state = self._call()
        self.assertEqual(state.total_steps, 2)

    def test_single_step(self) -> None:
        state = self._call(
            run=self._simple_run(("only",)),
            template=self._simple_template(("only",)),
        )
        self.assertEqual(state.total_steps, 1)

    def test_empty_steps(self) -> None:
        state = self._call(
            run=self._simple_run(()),
            template={"graph": {"steps": []}},
        )
        self.assertEqual(state.total_steps, 0)

    # ── workflow_input ────────────────────────────────────────────────────────

    def test_workflow_input_from_run(self) -> None:
        state = self._call()
        self.assertEqual(state.workflow_input, {"task": "do something"})

    def test_workflow_input_missing_defaults_to_empty_dict(self) -> None:
        run = {"run_id": "r1", "context": {}, "step_results": {}, "current_step_id": ""}
        state = self._call(run=run)
        self.assertEqual(state.workflow_input, {})

    # ── current_step_id ───────────────────────────────────────────────────────

    def test_current_step_id_from_run(self) -> None:
        run = self._simple_run()
        run["current_step_id"] = "s2"
        state = self._call(run=run)
        self.assertEqual(state.current_step_id, "s2")

    def test_current_step_id_empty_when_missing(self) -> None:
        run = {"run_id": "r1", "input": {}, "context": {}, "step_results": {}}
        state = self._call(run=run)
        self.assertEqual(state.current_step_id, "")

    # ── steps_by_id ───────────────────────────────────────────────────────────

    def test_steps_by_id_keys_match_ordered_ids(self) -> None:
        state = self._call()
        self.assertEqual(sorted(state.steps_by_id.keys()), sorted(state.ordered_ids))

    def test_steps_by_id_values_are_dicts(self) -> None:
        state = self._call()
        for v in state.steps_by_id.values():
            self.assertIsInstance(v, dict)

    # ── graph ─────────────────────────────────────────────────────────────────

    def test_graph_is_dict(self) -> None:
        state = self._call()
        self.assertIsInstance(state.graph, dict)

    def test_graph_has_steps_key(self) -> None:
        state = self._call()
        self.assertIn("steps", state.graph)

    def test_no_graph_in_template_gives_empty_graph(self) -> None:
        state = self._call(template={})
        self.assertEqual(state.graph, {})

    # ── injectable get_workflow_run ───────────────────────────────────────────

    def test_get_workflow_run_return_used_as_run_state(self) -> None:
        mock_run = {"run_id": "r-test", "status": "running"}
        state = self._call(get_run=lambda _: mock_run)
        self.assertEqual(state.run_state, mock_run)

    def test_none_from_get_workflow_run_falls_back_to_run(self) -> None:
        run = self._simple_run()
        state = self._call(run=run, get_run=lambda _: None)
        # When get_workflow_run returns None, run_state should be the run itself
        self.assertEqual(state.run_state["run_id"], "r-test")


if __name__ == "__main__":
    unittest.main()
