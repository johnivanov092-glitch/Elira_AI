"""Tests for workflows/lifecycle — merge_resumed_context, fail_missing_step,
    pause_after_step, fail_step_and_finish, complete_after_step,
    advance_to_next_step, cancel_run (all accept callable deps → pure under mocks)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.workflows.lifecycle import (  # noqa: E402
    merge_resumed_context,
    fail_missing_step,
    pause_after_step,
    fail_step_and_finish,
    complete_after_step,
    advance_to_next_step,
    cancel_run,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper stubs
# ─────────────────────────────────────────────────────────────────────────────

def _updater(*args, **kwargs) -> dict[str, Any]:
    """Stub: echoes kwargs as the 'updated run' dict."""
    return {"run_id": args[0] if args else "r1", **kwargs}


def _recorder(*args, **kwargs) -> None:
    pass


def _emitter(event_type: str, workflow_id: str, run_id: str,
             payload: dict | None = None) -> None:
    pass


def _now() -> str:
    return "2025-01-01T00:00:00"


# ─────────────────────────────────────────────────────────────────────────────
# workflows/lifecycle — merge_resumed_context
# ─────────────────────────────────────────────────────────────────────────────

class MergeResumedContextTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        result = merge_resumed_context({"context": {}})
        self.assertIsInstance(result, dict)

    def test_base_context_is_merged(self) -> None:
        run = {"context": {"key": "value"}}
        result = merge_resumed_context(run)
        self.assertEqual(result["key"], "value")

    def test_patch_overrides_base(self) -> None:
        run = {"context": {"key": "old"}}
        result = merge_resumed_context(run, {"key": "new"})
        self.assertEqual(result["key"], "new")

    def test_patch_adds_new_keys(self) -> None:
        run = {"context": {"a": 1}}
        result = merge_resumed_context(run, {"b": 2})
        self.assertEqual(result["b"], 2)

    def test_no_context_key_treated_as_empty(self) -> None:
        result = merge_resumed_context({})
        self.assertIsInstance(result, dict)

    def test_none_patch_is_safe(self) -> None:
        run = {"context": {"x": 1}}
        result = merge_resumed_context(run, None)
        self.assertEqual(result["x"], 1)

    def test_original_run_not_mutated(self) -> None:
        ctx = {"x": 1}
        run = {"context": ctx}
        merge_resumed_context(run, {"x": 99})
        self.assertEqual(ctx["x"], 1)  # not mutated


# ─────────────────────────────────────────────────────────────────────────────
# workflows/lifecycle — fail_missing_step
# ─────────────────────────────────────────────────────────────────────────────

class FailMissingStepTest(unittest.TestCase):
    def _call(self, emit_calls=None):
        recorded = [] if emit_calls is None else emit_calls

        def emitter(event_type, workflow_id, run_id, payload=None):
            recorded.append(event_type)

        result = fail_missing_step(
            run_id="r1",
            workflow_id="w1",
            current_step_id="step_x",
            update_workflow_run=_updater,
            record_workflow_run_state=_recorder,
            emit_workflow_event=emitter,
            now_func=_now,
        )
        return result, recorded

    def test_returns_dict(self) -> None:
        result, _ = self._call()
        self.assertIsInstance(result, dict)

    def test_status_is_failed(self) -> None:
        result, _ = self._call()
        self.assertEqual(result["status"], "failed")

    def test_emits_step_failed_event(self) -> None:
        _, events = self._call()
        self.assertIn("workflow.step.failed", events)

    def test_emits_run_completed_event(self) -> None:
        _, events = self._call()
        self.assertIn("workflow.run.completed", events)

    def test_emits_control_plane_completed_event(self) -> None:
        _, events = self._call()
        self.assertIn("workflow/completed", events)

    def test_three_events_emitted(self) -> None:
        _, events = self._call()
        self.assertEqual(len(events), 3)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/lifecycle — pause_after_step
# ─────────────────────────────────────────────────────────────────────────────

class PauseAfterStepTest(unittest.TestCase):
    def _call(self, emit_calls=None):
        recorded = [] if emit_calls is None else emit_calls

        def emitter(event_type, *a, **kw):
            recorded.append(event_type)

        result = pause_after_step(
            run_id="r1",
            workflow_id="w1",
            current_step_id="step_a",
            next_step_id="step_b",
            step_results={"step_a": {"ok": True}},
            update_workflow_run=_updater,
            record_workflow_run_state=_recorder,
            emit_workflow_event=emitter,
        )
        return result, recorded

    def test_returns_dict(self) -> None:
        result, _ = self._call()
        self.assertIsInstance(result, dict)

    def test_status_is_paused(self) -> None:
        result, _ = self._call()
        self.assertEqual(result["status"], "paused")

    def test_emits_paused_event(self) -> None:
        _, events = self._call()
        self.assertIn("workflow.run.paused", events)

    def test_exactly_one_event(self) -> None:
        _, events = self._call()
        self.assertEqual(len(events), 1)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/lifecycle — fail_step_and_finish
# ─────────────────────────────────────────────────────────────────────────────

class FailStepAndFinishTest(unittest.TestCase):
    def _call(self):
        events: list[str] = []

        def emitter(event_type, *a, **kw):
            events.append(event_type)

        result = fail_step_and_finish(
            run_id="r1",
            workflow_id="w1",
            current_step_id="step_x",
            step_results={},
            error_message="something broke",
            update_workflow_run=_updater,
            record_workflow_run_state=_recorder,
            emit_workflow_event=emitter,
            now_func=_now,
        )
        return result, events

    def test_returns_dict(self) -> None:
        result, _ = self._call()
        self.assertIsInstance(result, dict)

    def test_status_is_failed(self) -> None:
        result, _ = self._call()
        self.assertEqual(result["status"], "failed")

    def test_emits_completed_event(self) -> None:
        _, events = self._call()
        self.assertIn("workflow.run.completed", events)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/lifecycle — complete_after_step
# ─────────────────────────────────────────────────────────────────────────────

class CompleteAfterStepTest(unittest.TestCase):
    def _call(self):
        events: list[str] = []

        def emitter(event_type, *a, **kw):
            events.append(event_type)

        result = complete_after_step(
            run_id="r1",
            workflow_id="w1",
            completed_from_step_id="step_z",
            step_results={"step_z": {"ok": True}},
            update_workflow_run=_updater,
            record_workflow_run_state=_recorder,
            emit_workflow_event=emitter,
            now_func=_now,
        )
        return result, events

    def test_returns_dict(self) -> None:
        result, _ = self._call()
        self.assertIsInstance(result, dict)

    def test_status_is_completed(self) -> None:
        result, _ = self._call()
        self.assertEqual(result["status"], "completed")

    def test_emits_completed_event(self) -> None:
        _, events = self._call()
        self.assertIn("workflow.run.completed", events)


# ─────────────────────────────────────────────────────────────────────────────
# workflows/lifecycle — advance_to_next_step
# ─────────────────────────────────────────────────────────────────────────────

class AdvanceToNextStepTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        result = advance_to_next_step(
            run_id="r1",
            next_step_id="step2",
            step_results={},
            update_workflow_run=_updater,
        )
        self.assertIsInstance(result, dict)

    def test_status_is_running(self) -> None:
        result = advance_to_next_step(
            run_id="r1",
            next_step_id="step2",
            step_results={},
            update_workflow_run=_updater,
        )
        self.assertEqual(result["status"], "running")

    def test_next_step_id_passed(self) -> None:
        result = advance_to_next_step(
            run_id="r1",
            next_step_id="step_next",
            step_results={},
            update_workflow_run=_updater,
        )
        self.assertEqual(result["current_step_id"], "step_next")


# ─────────────────────────────────────────────────────────────────────────────
# workflows/lifecycle — cancel_run
# ─────────────────────────────────────────────────────────────────────────────

class CancelRunTest(unittest.TestCase):
    def _call(self):
        events: list[str] = []

        def emitter(event_type, *a, **kw):
            events.append(event_type)

        result = cancel_run(
            run_id="r1",
            run={"workflow_id": "w1"},
            update_workflow_run=_updater,
            record_workflow_run_state=_recorder,
            emit_workflow_event=emitter,
            now_func=_now,
        )
        return result, events

    def test_returns_dict(self) -> None:
        result, _ = self._call()
        self.assertIsInstance(result, dict)

    def test_status_is_cancelled(self) -> None:
        result, _ = self._call()
        self.assertEqual(result["status"], "cancelled")

    def test_emits_cancelled_event(self) -> None:
        _, events = self._call()
        self.assertIn("workflow.run.cancelled", events)


if __name__ == "__main__":
    unittest.main()
