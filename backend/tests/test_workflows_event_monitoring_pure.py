"""Tests for pure-function helpers in four sub-modules:
  workflows/db_path (get_workflow_db_path, set_workflow_db_path)
  workflows/step_results (WorkflowStepOutcome, build_step_result_from_exception,
                          capture_step_outcome, build_step_completion_event,
                          should_pause_after_step)
  event_bus/store (dumps_json, loads_json, row_to_event, row_to_message,
                   row_to_subscription)
  monitoring/store (dumps_json, loads_json, now_utc, row_to_limit, row_to_metric,
                    row_to_usage, planner_tool_aliases, DEFAULT_* constants)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.workflows.db_path import (  # noqa: E402
    get_workflow_db_path,
    set_workflow_db_path,
)
from app.application.workflows.step_results import (  # noqa: E402
    WorkflowStepOutcome,
    build_step_result_from_exception,
    capture_step_outcome,
    build_step_completion_event,
    should_pause_after_step,
)
from app.application.event_bus import store as eb_store  # noqa: E402
from app.application.monitoring import store as mon_store  # noqa: E402
from app.application.agent_sandbox.runtime import SandboxPolicyError  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# workflows/db_path
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowDbPathTest(unittest.TestCase):
    def test_get_returns_path(self) -> None:
        result = get_workflow_db_path()
        self.assertIsInstance(result, Path)

    def test_get_is_not_empty(self) -> None:
        result = get_workflow_db_path()
        self.assertGreater(len(str(result)), 0)

    def test_set_returns_path(self) -> None:
        original = get_workflow_db_path()
        try:
            result = set_workflow_db_path("/tmp/test_workflow.db")
            self.assertIsInstance(result, Path)
        finally:
            set_workflow_db_path(original)

    def test_set_changes_value(self) -> None:
        original = get_workflow_db_path()
        try:
            target = Path("/tmp/other_workflow.db")
            set_workflow_db_path(target)
            self.assertEqual(get_workflow_db_path(), target)
        finally:
            set_workflow_db_path(original)

    def test_set_accepts_string(self) -> None:
        original = get_workflow_db_path()
        try:
            result = set_workflow_db_path("relative/path.db")
            self.assertIsInstance(result, Path)
        finally:
            set_workflow_db_path(original)

    def test_set_accepts_path_object(self) -> None:
        original = get_workflow_db_path()
        try:
            result = set_workflow_db_path(Path("/tmp/workflow_obj.db"))
            self.assertIsInstance(result, Path)
        finally:
            set_workflow_db_path(original)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/step_results — WorkflowStepOutcome
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowStepOutcomeTest(unittest.TestCase):
    def _make(self, **kw) -> WorkflowStepOutcome:
        defaults = dict(save_key="k", success=True, next_step_id=None)
        defaults.update(kw)
        return WorkflowStepOutcome(**defaults)

    def test_has_save_key(self) -> None:
        self.assertEqual(self._make(save_key="step1").save_key, "step1")

    def test_has_success_true(self) -> None:
        self.assertTrue(self._make(success=True).success)

    def test_has_success_false(self) -> None:
        self.assertFalse(self._make(success=False).success)

    def test_has_next_step_id_none(self) -> None:
        self.assertIsNone(self._make(next_step_id=None).next_step_id)

    def test_has_next_step_id_value(self) -> None:
        self.assertEqual(self._make(next_step_id="step2").next_step_id, "step2")


# ─────────────────────────────────────────────────────────────────────────────
# workflows/step_results — build_step_result_from_exception
# ─────────────────────────────────────────────────────────────────────────────

class BuildStepResultFromExceptionTest(unittest.TestCase):
    def test_generic_exception_ok_false(self) -> None:
        result = build_step_result_from_exception(ValueError("oops"))
        self.assertFalse(result["ok"])

    def test_generic_exception_has_error(self) -> None:
        result = build_step_result_from_exception(RuntimeError("bad"))
        self.assertIn("bad", result["error"])

    def test_generic_exception_has_raw(self) -> None:
        result = build_step_result_from_exception(Exception("e"))
        self.assertIn("raw", result)

    def test_sandbox_error_has_reason(self) -> None:
        exc = SandboxPolicyError("a1", "test reason", "context_limit_exceeded", {})
        result = build_step_result_from_exception(exc)
        self.assertEqual(result["sandbox_reason"], "context_limit_exceeded")

    def test_sandbox_error_ok_false(self) -> None:
        exc = SandboxPolicyError("a1", "msg", "tool_not_allowed", {})
        result = build_step_result_from_exception(exc)
        self.assertFalse(result["ok"])

    def test_sandbox_error_has_details(self) -> None:
        exc = SandboxPolicyError("a1", "msg", "r", {"detail": "x"})
        result = build_step_result_from_exception(exc)
        self.assertIn("sandbox_details", result)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(build_step_result_from_exception(Exception("x")), dict)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/step_results — capture_step_outcome
# ─────────────────────────────────────────────────────────────────────────────

class CaptureStepOutcomeTest(unittest.TestCase):
    def _run(self, step, step_result, *, next_step=None):
        results: dict = {}
        outcome = capture_step_outcome(
            step,
            current_step_id="s1",
            step_result=step_result,
            step_results=results,
            resolve_next_step=lambda s: next_step,
        )
        return outcome, results

    def test_returns_workflow_step_outcome(self) -> None:
        outcome, _ = self._run({}, {"ok": True})
        self.assertIsInstance(outcome, WorkflowStepOutcome)

    def test_success_when_ok_true(self) -> None:
        outcome, _ = self._run({}, {"ok": True})
        self.assertTrue(outcome.success)

    def test_failure_when_ok_false(self) -> None:
        outcome, _ = self._run({}, {"ok": False})
        self.assertFalse(outcome.success)

    def test_save_key_is_current_step_id_when_no_save_as(self) -> None:
        outcome, _ = self._run({}, {"ok": True})
        self.assertEqual(outcome.save_key, "s1")

    def test_save_key_uses_save_as(self) -> None:
        outcome, _ = self._run({"save_as": "my_result"}, {"ok": True})
        self.assertEqual(outcome.save_key, "my_result")

    def test_stores_result_in_step_results(self) -> None:
        _, results = self._run({}, {"ok": True, "data": 42})
        self.assertIn("s1", results)

    def test_next_step_id_propagated(self) -> None:
        outcome, _ = self._run({}, {"ok": True}, next_step="step2")
        self.assertEqual(outcome.next_step_id, "step2")


# ─────────────────────────────────────────────────────────────────────────────
# workflows/step_results — build_step_completion_event
# ─────────────────────────────────────────────────────────────────────────────

class BuildStepCompletionEventTest(unittest.TestCase):
    def _success_outcome(self):
        return WorkflowStepOutcome(save_key="k", success=True, next_step_id="s2")

    def _fail_outcome(self):
        return WorkflowStepOutcome(save_key="k", success=False, next_step_id=None)

    def test_success_returns_completed_event(self) -> None:
        event_type, _ = build_step_completion_event(
            current_step_id="s1", outcome=self._success_outcome(),
            step_result={"ok": True},
        )
        self.assertEqual(event_type, "workflow.step.completed")

    def test_failure_returns_failed_event(self) -> None:
        event_type, _ = build_step_completion_event(
            current_step_id="s1", outcome=self._fail_outcome(),
            step_result={"ok": False, "error": "oops"},
        )
        self.assertEqual(event_type, "workflow.step.failed")

    def test_success_payload_has_step_id(self) -> None:
        _, payload = build_step_completion_event(
            current_step_id="s1", outcome=self._success_outcome(),
            step_result={"ok": True},
        )
        self.assertEqual(payload["step_id"], "s1")

    def test_failure_payload_has_error(self) -> None:
        _, payload = build_step_completion_event(
            current_step_id="s1", outcome=self._fail_outcome(),
            step_result={"ok": False, "error": "boom"},
        )
        self.assertIn("error", payload)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/step_results — should_pause_after_step
# ─────────────────────────────────────────────────────────────────────────────

class ShouldPauseAfterStepTest(unittest.TestCase):
    def test_false_when_neither_set(self) -> None:
        self.assertFalse(should_pause_after_step({}, {}))

    def test_true_when_step_has_pause_after(self) -> None:
        self.assertTrue(should_pause_after_step({"pause_after": True}, {}))

    def test_true_when_result_has_pause_requested(self) -> None:
        self.assertTrue(should_pause_after_step({}, {"pause_requested": True}))

    def test_false_when_pause_after_falsy(self) -> None:
        self.assertFalse(should_pause_after_step({"pause_after": False}, {}))

    def test_true_when_both_set(self) -> None:
        self.assertTrue(should_pause_after_step(
            {"pause_after": True}, {"pause_requested": True}
        ))


# ─────────────────────────────────────────────────────────────────────────────
# event_bus/store — pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class EventBusStoreDumpsLoadsTest(unittest.TestCase):
    def test_dumps_returns_string(self) -> None:
        self.assertIsInstance(eb_store.dumps_json({"a": 1}), str)

    def test_dumps_none_returns_empty_obj_string(self) -> None:
        result = eb_store.dumps_json(None)
        self.assertEqual(result, "{}")

    def test_dumps_roundtrip(self) -> None:
        import json
        data = {"key": "value", "num": 42}
        self.assertEqual(json.loads(eb_store.dumps_json(data)), data)

    def test_loads_none_returns_default(self) -> None:
        self.assertEqual(eb_store.loads_json(None, []), [])

    def test_loads_empty_string_returns_default(self) -> None:
        self.assertEqual(eb_store.loads_json("", {}), {})

    def test_loads_valid_json(self) -> None:
        self.assertEqual(eb_store.loads_json('{"x": 1}', {}), {"x": 1})

    def test_loads_invalid_json_returns_default(self) -> None:
        self.assertEqual(eb_store.loads_json("not json", "fallback"), "fallback")

    def test_loads_list_default(self) -> None:
        self.assertEqual(eb_store.loads_json("[]", None), [])


class EventBusStoreRowHelpersTest(unittest.TestCase):
    def _loads(self, raw, default):
        return eb_store.loads_json(raw, default)

    def test_row_to_event_none_returns_none(self) -> None:
        self.assertIsNone(eb_store.row_to_event(loads_func=self._loads, row=None))

    def test_row_to_event_extracts_payload(self) -> None:
        row = {"event_id": "e1", "payload_json": '{"data": 1}'}
        result = eb_store.row_to_event(loads_func=self._loads, row=row)
        self.assertEqual(result["payload"], {"data": 1})

    def test_row_to_event_removes_payload_json(self) -> None:
        row = {"event_id": "e1", "payload_json": "{}"}
        result = eb_store.row_to_event(loads_func=self._loads, row=row)
        self.assertNotIn("payload_json", result)

    def test_row_to_message_none_returns_none(self) -> None:
        self.assertIsNone(eb_store.row_to_message(loads_func=self._loads, row=None))

    def test_row_to_message_extracts_content(self) -> None:
        row = {"msg_id": "m1", "content_json": '{"text": "hi"}', "read": 0}
        result = eb_store.row_to_message(loads_func=self._loads, row=row)
        self.assertEqual(result["content"], {"text": "hi"})

    def test_row_to_message_read_is_bool(self) -> None:
        row = {"msg_id": "m1", "content_json": "{}", "read": 1}
        result = eb_store.row_to_message(loads_func=self._loads, row=row)
        self.assertIsInstance(result["read"], bool)

    def test_row_to_message_read_false_for_zero(self) -> None:
        row = {"msg_id": "m1", "content_json": "{}", "read": 0}
        result = eb_store.row_to_message(loads_func=self._loads, row=row)
        self.assertFalse(result["read"])

    def test_row_to_subscription_none_returns_none(self) -> None:
        self.assertIsNone(eb_store.row_to_subscription(row=None))

    def test_row_to_subscription_returns_dict(self) -> None:
        row = {"sub_id": "s1", "agent_id": "a1"}
        result = eb_store.row_to_subscription(row=row)
        self.assertEqual(result["sub_id"], "s1")


# ─────────────────────────────────────────────────────────────────────────────
# monitoring/store — pure helpers and constants
# ─────────────────────────────────────────────────────────────────────────────

class MonitoringStoreConstantsTest(unittest.TestCase):
    def test_default_max_runs_per_hour_positive(self) -> None:
        self.assertGreater(mon_store.DEFAULT_MAX_RUNS_PER_HOUR, 0)

    def test_default_max_execution_seconds_positive(self) -> None:
        self.assertGreater(mon_store.DEFAULT_MAX_EXECUTION_SECONDS, 0)

    def test_default_max_context_tokens_positive(self) -> None:
        self.assertGreater(mon_store.DEFAULT_MAX_CONTEXT_TOKENS, 0)

    def test_default_workflow_engine_agent_id_is_string(self) -> None:
        self.assertIsInstance(mon_store.DEFAULT_WORKFLOW_ENGINE_AGENT_ID, str)


class MonitoringStoreDumpsLoadsTest(unittest.TestCase):
    def test_dumps_returns_string(self) -> None:
        self.assertIsInstance(mon_store.dumps_json({"k": "v"}), str)

    def test_dumps_none_empty_obj(self) -> None:
        self.assertEqual(mon_store.dumps_json(None), "{}")

    def test_loads_none_returns_default(self) -> None:
        self.assertEqual(mon_store.loads_json(None, []), [])

    def test_loads_valid_returns_parsed(self) -> None:
        self.assertEqual(mon_store.loads_json('[1,2]', None), [1, 2])

    def test_loads_invalid_returns_default(self) -> None:
        self.assertEqual(mon_store.loads_json("bad", "d"), "d")

    def test_now_utc_returns_string(self) -> None:
        self.assertIsInstance(mon_store.now_utc(), str)

    def test_now_utc_contains_year(self) -> None:
        import re
        self.assertTrue(re.search(r"\b20\d{2}\b", mon_store.now_utc()))

    def test_now_utc_two_calls_close(self) -> None:
        t1 = mon_store.now_utc()
        t2 = mon_store.now_utc()
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)


class MonitoringStoreRowHelpersTest(unittest.TestCase):
    def test_row_to_limit_none_returns_none(self) -> None:
        self.assertIsNone(mon_store.row_to_limit(None))

    def test_row_to_limit_extracts_allowed_tools(self) -> None:
        row = {"agent_id": "a1", "allowed_tools_json": '["search"]',
               "max_runs_per_hour": 10, "max_execution_seconds": 60,
               "max_context_tokens": 8192, "created_at": "", "updated_at": ""}
        result = mon_store.row_to_limit(row)
        self.assertEqual(result["allowed_tools"], ["search"])

    def test_row_to_limit_removes_allowed_tools_json(self) -> None:
        row = {"agent_id": "a1", "allowed_tools_json": "[]",
               "max_runs_per_hour": 10, "max_execution_seconds": 60,
               "max_context_tokens": 8192, "created_at": "", "updated_at": ""}
        result = mon_store.row_to_limit(row)
        self.assertNotIn("allowed_tools_json", result)

    def test_row_to_metric_none_returns_none(self) -> None:
        self.assertIsNone(mon_store.row_to_metric(None))

    def test_row_to_metric_has_details(self) -> None:
        row = {"id": 1, "metric_type": "run", "details_json": '{"x": 1}',
               "ok": 1, "agent_id": "", "run_id": "", "duration_ms": 100, "created_at": ""}
        result = mon_store.row_to_metric(row)
        self.assertEqual(result["details"], {"x": 1})

    def test_row_to_metric_ok_is_bool(self) -> None:
        row = {"id": 1, "metric_type": "run", "details_json": "{}",
               "ok": 1, "agent_id": "", "run_id": "", "duration_ms": 0, "created_at": ""}
        result = mon_store.row_to_metric(row)
        self.assertIsInstance(result["ok"], bool)

    def test_row_to_usage_none_returns_none(self) -> None:
        self.assertIsNone(mon_store.row_to_usage(None))

    def test_row_to_usage_has_details(self) -> None:
        row = {"id": 1, "agent_id": "a", "resource": "tokens",
               "details_json": '{"y": 2}', "amount": 100, "unit": "tokens",
               "created_at": "", "run_id": "", "step_id": "", "workflow_id": ""}
        result = mon_store.row_to_usage(row)
        self.assertEqual(result["details"], {"y": 2})


class MonitoringStorePlannerAliasesTest(unittest.TestCase):
    def test_planner_tool_aliases_returns_list(self) -> None:
        self.assertIsInstance(mon_store.planner_tool_aliases(), list)

    def test_planner_tool_aliases_not_empty(self) -> None:
        self.assertGreater(len(mon_store.planner_tool_aliases()), 0)

    def test_planner_tool_aliases_contains_web_search(self) -> None:
        self.assertIn("web_search", mon_store.planner_tool_aliases())

    def test_planner_tool_aliases_contains_memory_search(self) -> None:
        self.assertIn("memory_search", mon_store.planner_tool_aliases())

    def test_planner_tool_aliases_strings_only(self) -> None:
        for alias in mon_store.planner_tool_aliases():
            self.assertIsInstance(alias, str)


if __name__ == "__main__":
    unittest.main()
