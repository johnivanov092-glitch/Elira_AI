"""R1 — the verifier catalog as a runtime ASSIST (flag `catalog_assist`, default OFF).

Pins the contract: (1) bit-identical behavior with the flag off (no `unsupported`
key, unchanged closure hints); (2) with the flag on — honest «нет верификатора»
labels for generic criteria, catalog notes appended to closure hints, drift
detection for intents the catalog doesn't know; (3) fail-open on a broken/missing
catalog; (4) the anti-drift canary: every intent the classifier can emit is known
to the catalog.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import catalog  # noqa: E402
from app.application.code_agent import criterion_closure as cc  # noqa: E402
from app.application.code_agent.taskspec import CriteriaTracker, TaskSpec  # noqa: E402


def _fresh_cache():
    catalog._CACHE.update(mtime=catalog._UNREAD, index=None, known=frozenset())


class CatalogIndexTest(unittest.TestCase):
    def setUp(self):
        _fresh_cache()

    def test_known_intents_cover_every_classifier_intent(self):
        """Anti-drift canary: every intent _criterion_intent can emit is known to the
        catalog (meta.intents or a category row). A new intent added in code without a
        catalog row fails HERE, deterministically — not at runtime."""
        classifier_intents = {
            "report", "file_exists", "file_not_exists", "content_contains",
            "content_not_contains", "dom_contains", "viewport_layout", "page_open",
            "server_started", "command_check", "command_output", "generic",
            "behavior_test",
        }
        self.assertEqual(catalog.drift_intents(classifier_intents), [])

    def test_supported_intents_are_not_unsupported(self):
        for intent in ("file_exists", "file_not_exists", "content_contains",
                       "dom_contains", "viewport_layout", "command_output",
                       "command_check", "server_started", "page_open",
                       "behavior_test"):
            self.assertFalse(catalog.unsupported_intent(intent), intent)

    def test_generic_and_report_are_unsupported_by_design(self):
        self.assertTrue(catalog.unsupported_intent("generic"))
        self.assertTrue(catalog.unsupported_intent("report"))

    def test_drift_detects_unknown_intent(self):
        self.assertEqual(catalog.drift_intents({"file_exists", "quantum_check"}),
                         ["quantum_check"])

    def test_intent_note_prose_only(self):
        # viewport row carries PROSE does_not_close → note; the dom row's entries are
        # bare intent tokens (machine cross-references) → filtered out, no garbage in
        # a nudge (review F2/F7).
        self.assertTrue(catalog.intent_note("viewport_layout"))
        self.assertEqual(catalog.intent_note("dom_contains"), "")

    def test_fail_open_on_missing_catalog(self):
        _fresh_cache()
        with patch.object(catalog, "_CATALOG_PATH", Path("Z:/no/such/catalog.yaml")):
            self.assertFalse(catalog.unsupported_intent("file_exists"))
            self.assertTrue(catalog.unsupported_intent("generic"))   # design floor holds
            self.assertEqual(catalog.drift_intents({"whatever"}), [])
            self.assertEqual(catalog.intent_note("dom_contains"), "")
        _fresh_cache()


class ReportLabelTest(unittest.TestCase):
    _SPEC = TaskSpec(success_criteria=[
        "код не ломает существующие routes",     # generic → unverifiable
        "файл `a.txt` существует",                # file_exists → verifiable
    ])

    def test_flag_off_report_is_bit_identical(self):
        t = CriteriaTracker.from_spec(self._SPEC)
        for row in t.report():
            self.assertNotIn("unsupported", row)   # default payload unchanged

    def test_flag_on_labels_only_unverifiable(self):
        t = CriteriaTracker.from_spec(self._SPEC)
        t.catalog_assist = True
        rows = t.report()
        self.assertTrue(rows[0]["unsupported"])
        self.assertFalse(rows[1]["unsupported"])


class ClosureHintNotesTest(unittest.TestCase):
    _SPEC = TaskSpec(success_criteria=["нет горизонтального скролла на mobile"])

    def test_hints_identical_without_flag(self):
        t = CriteriaTracker.from_spec(self._SPEC)
        base = cc.missing_verifier_actions(t, url="http://127.0.0.1:3000")
        again = cc.missing_verifier_actions(t, url="http://127.0.0.1:3000", catalog_hints=False)
        self.assertEqual([a["call"] for a in base], [a["call"] for a in again])
        self.assertNotIn("каталог", base[0]["call"])

    def test_hints_carry_catalog_note_with_flag(self):
        t = CriteriaTracker.from_spec(self._SPEC)
        acts = cc.missing_verifier_actions(t, url="http://127.0.0.1:3000", catalog_hints=True)
        self.assertIn("[каталог: НЕ закрывает —", acts[0]["call"])   # viewport prose note


if __name__ == "__main__":
    unittest.main()
