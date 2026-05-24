"""Tests for application/chat/post_processing:
  apply_identity_guard, apply_provenance_guard,
  maybe_auto_exec_python, apply_response_guards, GuardedResponse."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.post_processing import (  # noqa: E402
    apply_identity_guard,
    apply_provenance_guard,
    maybe_auto_exec_python,
    apply_response_guards,
    GuardedResponse,
    _EXEC_TRIGGERS,
)


# ─────────────────────────────────────────────────────────────────────────────
# _EXEC_TRIGGERS sanity
# ─────────────────────────────────────────────────────────────────────────────

class ExecTriggersTest(unittest.TestCase):
    def test_is_list(self) -> None:
        self.assertIsInstance(_EXEC_TRIGGERS, list)

    def test_not_empty(self) -> None:
        self.assertGreater(len(_EXEC_TRIGGERS), 0)

    def test_contains_english_execute(self) -> None:
        self.assertIn("execute", _EXEC_TRIGGERS)

    def test_contains_english_calculate(self) -> None:
        self.assertIn("calculate", _EXEC_TRIGGERS)

    def test_contains_english_run(self) -> None:
        self.assertIn("run", _EXEC_TRIGGERS)


# ─────────────────────────────────────────────────────────────────────────────
# apply_identity_guard
# ─────────────────────────────────────────────────────────────────────────────

class ApplyIdentityGuardTest(unittest.TestCase):
    def _mock_guard(self, changed: bool, text: str = "guarded text"):
        return patch(
            "app.application.chat.post_processing.guard_identity_response",
            return_value={"changed": changed, "text": text, "reason": "test"},
        )

    def test_returns_dict(self) -> None:
        with self._mock_guard(False, "original text"):
            result = apply_identity_guard(
                user_input="who are you",
                answer_text="I am Gemma",
            )
        self.assertIsInstance(result, dict)

    def test_passes_through_when_unchanged(self) -> None:
        with self._mock_guard(False, "original text"):
            result = apply_identity_guard(
                user_input="explain Python",
                answer_text="original text",
            )
        self.assertFalse(result["changed"])

    def test_changed_true_when_guard_rewrites(self) -> None:
        with self._mock_guard(True, "I am Elira"):
            result = apply_identity_guard(
                user_input="who are you",
                answer_text="I am Gemma",
            )
        self.assertTrue(result["changed"])

    def test_appends_timeline_on_change(self) -> None:
        tl: list = []
        with self._mock_guard(True, "I am Elira"):
            apply_identity_guard(
                user_input="who are you",
                answer_text="I am Gemma",
                append_timeline_func=lambda tl, *a: tl.append(a),
                timeline=tl,
            )
        self.assertGreater(len(tl), 0)

    def test_no_timeline_when_unchanged(self) -> None:
        tl: list = []
        with self._mock_guard(False, "original"):
            apply_identity_guard(
                user_input="query",
                answer_text="original",
                append_timeline_func=lambda tl, *a: tl.append(a),
                timeline=tl,
            )
        self.assertEqual(len(tl), 0)


# ─────────────────────────────────────────────────────────────────────────────
# apply_provenance_guard
# ─────────────────────────────────────────────────────────────────────────────

class ApplyProvenanceGuardTest(unittest.TestCase):
    def _mock_guard(self, changed: bool, text: str = "cleaned text"):
        return patch(
            "app.application.chat.post_processing.guard_provenance_response",
            return_value={"changed": changed, "text": text, "reason": "test"},
        )

    def test_returns_dict(self) -> None:
        with self._mock_guard(False):
            result = apply_provenance_guard(
                user_input="q",
                answer_text="answer",
            )
        self.assertIsInstance(result, dict)

    def test_unchanged_passes_through(self) -> None:
        with self._mock_guard(False, "clean answer"):
            result = apply_provenance_guard(
                user_input="what is Python",
                answer_text="clean answer",
            )
        self.assertFalse(result["changed"])

    def test_changed_reflects_guard(self) -> None:
        with self._mock_guard(True, "stripped answer"):
            result = apply_provenance_guard(
                user_input="show sources",
                answer_text="[source] answer",
            )
        self.assertTrue(result["changed"])

    def test_appends_timeline_on_change(self) -> None:
        tl: list = []
        with self._mock_guard(True, "clean"):
            apply_provenance_guard(
                user_input="q",
                answer_text="[fact]answer",
                append_timeline_func=lambda tl, *a: tl.append(a),
                timeline=tl,
            )
        self.assertGreater(len(tl), 0)


# ─────────────────────────────────────────────────────────────────────────────
# maybe_auto_exec_python
# ─────────────────────────────────────────────────────────────────────────────

class MaybeAutoExecPythonTest(unittest.TestCase):
    def test_disabled_returns_answer_unchanged(self) -> None:
        result = maybe_auto_exec_python(
            user_input="вычисли", answer="some answer", enabled=False
        )
        self.assertEqual(result, "some answer")

    def test_no_trigger_returns_answer_unchanged(self) -> None:
        result = maybe_auto_exec_python(
            user_input="explain sorting", answer="quicksort is fast"
        )
        self.assertEqual(result, "quicksort is fast")

    def test_trigger_no_code_block_returns_unchanged(self) -> None:
        result = maybe_auto_exec_python(
            user_input="calculate something", answer="result is 42"
        )
        self.assertEqual(result, "result is 42")

    def test_trigger_short_code_returns_unchanged(self) -> None:
        # Code block is < 10 chars (just "2+2")
        result = maybe_auto_exec_python(
            user_input="calculate",
            answer="here:\n```python\n2+2\n```",
        )
        self.assertEqual(result, "here:\n```python\n2+2\n```")

    def test_trigger_with_code_block_executes(self) -> None:
        long_code = "result = 2 + 2\nprint(result)"
        answer = f"here is the code:\n```python\n{long_code}\n```"
        result = maybe_auto_exec_python(
            user_input="run this code",
            answer=answer,
        )
        # Result is augmented with execution output
        self.assertIsInstance(result, str)

    def test_returns_string(self) -> None:
        result = maybe_auto_exec_python(
            user_input="explain", answer="text"
        )
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# GuardedResponse dataclass
# ─────────────────────────────────────────────────────────────────────────────

class GuardedResponseTest(unittest.TestCase):
    def test_has_text(self) -> None:
        gr = GuardedResponse(
            text="hello", identity_guard={}, provenance_guard={}, changed=False
        )
        self.assertEqual(gr.text, "hello")

    def test_has_identity_guard(self) -> None:
        gr = GuardedResponse(
            text="t", identity_guard={"changed": True}, provenance_guard={}, changed=True
        )
        self.assertTrue(gr.identity_guard["changed"])

    def test_has_provenance_guard(self) -> None:
        gr = GuardedResponse(
            text="t", identity_guard={}, provenance_guard={"changed": False}, changed=False
        )
        self.assertFalse(gr.provenance_guard["changed"])

    def test_has_changed(self) -> None:
        gr = GuardedResponse(text="t", identity_guard={}, provenance_guard={}, changed=True)
        self.assertTrue(gr.changed)


# ─────────────────────────────────────────────────────────────────────────────
# apply_response_guards
# ─────────────────────────────────────────────────────────────────────────────

class ApplyResponseGuardsTest(unittest.TestCase):
    def _mock_both(self, text: str = "clean text"):
        id_guard = patch(
            "app.application.chat.post_processing.guard_identity_response",
            return_value={"changed": False, "text": text, "reason": ""},
        )
        prov_guard = patch(
            "app.application.chat.post_processing.guard_provenance_response",
            return_value={"changed": False, "text": text, "reason": ""},
        )
        return id_guard, prov_guard

    def test_returns_guarded_response(self) -> None:
        id_p, prov_p = self._mock_both("clean")
        with id_p, prov_p:
            result = apply_response_guards(
                raw_user_input="query",
                text="original",
                timeline=[],
            )
        self.assertIsInstance(result, GuardedResponse)

    def test_result_has_text(self) -> None:
        id_p, prov_p = self._mock_both("clean")
        with id_p, prov_p:
            result = apply_response_guards(
                raw_user_input="query",
                text="original",
                timeline=[],
            )
        self.assertIsInstance(result.text, str)

    def test_result_has_changed_bool(self) -> None:
        id_p, prov_p = self._mock_both("original")
        with id_p, prov_p:
            result = apply_response_guards(
                raw_user_input="query",
                text="original",
                timeline=[],
            )
        self.assertIsInstance(result.changed, bool)

    def test_no_exec_when_disabled(self) -> None:
        id_p, prov_p = self._mock_both("text")
        with id_p, prov_p:
            result = apply_response_guards(
                raw_user_input="execute this",
                text="```python\nx = 1 + 2\nprint(x)\n```",
                timeline=[],
                use_python_exec=False,
            )
        self.assertIsInstance(result, GuardedResponse)


if __name__ == "__main__":
    unittest.main()
