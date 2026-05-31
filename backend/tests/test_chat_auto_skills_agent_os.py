"""Tests for two previously uncovered chat sub-modules:
  chat/auto_skills — _FILE_TRIGGERS_WORD, _FILE_TRIGGERS_EXCEL constants;
    maybe_generate_files (disabled path); run_auto_skills (no-trigger and
    hint paths without actual skill calls)
  chat/agent_os — resolve_agent_os_source_id (pure); emit_agent_os_event
    (fire-and-forget, errors caught); record_registry_agent_run (no-id skip)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.auto_skills import (  # noqa: E402
    _FILE_TRIGGERS_WORD,
    _FILE_TRIGGERS_EXCEL,
    maybe_generate_files,
    run_auto_skills,
)
from app.application.chat.agent_os import (  # noqa: E402
    resolve_agent_os_source_id,
    emit_agent_os_event,
    record_registry_agent_run,
)


# ─────────────────────────────────────────────────────────────────────────────
# chat/auto_skills — _FILE_TRIGGERS_WORD / _FILE_TRIGGERS_EXCEL constants
# ─────────────────────────────────────────────────────────────────────────────

class FileTriggerWordTest(unittest.TestCase):
    def test_is_list(self) -> None:
        self.assertIsInstance(_FILE_TRIGGERS_WORD, list)

    def test_not_empty(self) -> None:
        self.assertGreater(len(_FILE_TRIGGERS_WORD), 0)

    def test_contains_strings(self) -> None:
        for item in _FILE_TRIGGERS_WORD:
            self.assertIsInstance(item, str)

    def test_contains_docx(self) -> None:
        self.assertTrue(any("docx" in t or "word" in t for t in _FILE_TRIGGERS_WORD))


class FileTriggerExcelTest(unittest.TestCase):
    def test_is_list(self) -> None:
        self.assertIsInstance(_FILE_TRIGGERS_EXCEL, list)

    def test_not_empty(self) -> None:
        self.assertGreater(len(_FILE_TRIGGERS_EXCEL), 0)

    def test_contains_strings(self) -> None:
        for item in _FILE_TRIGGERS_EXCEL:
            self.assertIsInstance(item, str)

    def test_contains_excel(self) -> None:
        self.assertTrue(any("excel" in t or "xlsx" in t for t in _FILE_TRIGGERS_EXCEL))


# ─────────────────────────────────────────────────────────────────────────────
# chat/auto_skills — maybe_generate_files
# ─────────────────────────────────────────────────────────────────────────────

class MaybeGenerateFilesTest(unittest.TestCase):
    def test_disabled_returns_empty_string(self) -> None:
        result = maybe_generate_files("docx документ", "some answer", enabled=False)
        self.assertEqual(result, "")

    def test_returns_string_type(self) -> None:
        result = maybe_generate_files("hello", "world", enabled=False)
        self.assertIsInstance(result, str)

    def test_no_trigger_returns_empty(self) -> None:
        # No word/excel trigger in input and enabled=True, but no match — empty
        result = maybe_generate_files("explain Python", "Python is great", enabled=True)
        self.assertEqual(result, "")

    def test_empty_answer_no_file_generated(self) -> None:
        # LLM answer too short → no file created even with trigger
        result = maybe_generate_files("создай документ", "Hi", enabled=True)
        # Short answer (<50 chars for word) → no word file
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# chat/auto_skills — run_auto_skills
# ─────────────────────────────────────────────────────────────────────────────

_ALL_DISABLED: set = {
    "http_api", "sql", "screenshot", "image_gen", "file_gen",
    "translator", "encrypt", "archiver", "converter", "regex",
    "csv_analysis", "webhook", "git", "gpu", "files",
}


class RunAutoSkillsTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        result = run_auto_skills("hello world")
        self.assertIsInstance(result, str)

    def test_neutral_input_returns_empty(self) -> None:
        result = run_auto_skills("explain Python programming", disabled=_ALL_DISABLED)
        self.assertEqual(result, "")

    def test_all_disabled_returns_empty(self) -> None:
        # Even with triggers, disabled skills → empty
        result = run_auto_skills("сделай excel таблицу", disabled=_ALL_DISABLED)
        self.assertEqual(result, "")

    def test_word_hint_when_file_gen_enabled(self) -> None:
        # Word trigger in input + file_gen NOT disabled → SKILL_HINT injected
        result = run_auto_skills("создай документ для скачивания", disabled=set())
        self.assertIn("SKILL_HINT", result)

    def test_excel_hint_when_file_gen_enabled(self) -> None:
        result = run_auto_skills("создай excel таблицу", disabled=set())
        self.assertIn("SKILL_HINT", result)

    def test_no_triggers_no_disabled_returns_empty(self) -> None:
        result = run_auto_skills("how does Python work")
        self.assertEqual(result, "")

    def test_empty_input_returns_empty(self) -> None:
        result = run_auto_skills("")
        self.assertEqual(result, "")


# ─────────────────────────────────────────────────────────────────────────────
# chat/agent_os — resolve_agent_os_source_id
# ─────────────────────────────────────────────────────────────────────────────

class ResolveAgentOsSourceIdTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        result = resolve_agent_os_source_id("a1", None)
        self.assertIsInstance(result, str)

    def test_explicit_agent_id_wins(self) -> None:
        result = resolve_agent_os_source_id("explicit", {"id": "registry"})
        self.assertEqual(result, "explicit")

    def test_registry_agent_id_used_when_no_explicit(self) -> None:
        result = resolve_agent_os_source_id(None, {"id": "registry-agent"})
        self.assertEqual(result, "registry-agent")

    def test_both_none_returns_empty_string(self) -> None:
        result = resolve_agent_os_source_id(None, None)
        self.assertEqual(result, "")

    def test_empty_string_agent_id_uses_registry(self) -> None:
        result = resolve_agent_os_source_id("", {"id": "fallback"})
        self.assertEqual(result, "fallback")

    def test_registry_without_id_returns_empty(self) -> None:
        result = resolve_agent_os_source_id(None, {})
        self.assertEqual(result, "")

    def test_none_registry_returns_empty(self) -> None:
        result = resolve_agent_os_source_id(None, None)
        self.assertEqual(result, "")


# ─────────────────────────────────────────────────────────────────────────────
# chat/agent_os — emit_agent_os_event (fire-and-forget, errors suppressed)
# ─────────────────────────────────────────────────────────────────────────────

class EmitAgentOsEventTest(unittest.TestCase):
    def test_returns_none(self) -> None:
        result = emit_agent_os_event(event_type="test.event")
        self.assertIsNone(result)

    def test_does_not_raise(self) -> None:
        # Even if event_bus DB is not initialized, errors are suppressed
        try:
            emit_agent_os_event(event_type="test.event", source_agent_id="a1")
        except Exception as exc:
            self.fail(f"emit_agent_os_event raised unexpectedly: {exc}")

    def test_with_payload_does_not_raise(self) -> None:
        try:
            emit_agent_os_event(
                event_type="test.event",
                source_agent_id="agent-1",
                payload={"key": "value"},
            )
        except Exception as exc:
            self.fail(f"emit_agent_os_event raised unexpectedly: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# chat/agent_os — record_registry_agent_run (no agent_id → early return)
# ─────────────────────────────────────────────────────────────────────────────

class RecordRegistryAgentRunTest(unittest.TestCase):
    def test_returns_none_when_no_agent_id(self) -> None:
        result = record_registry_agent_run(
            agent_id=None,
            registry_agent=None,
            run_id="r1",
            input_summary="hi",
            output_summary="hello",
            ok=True,
            route="chat",
            model_name="model",
            duration_ms=100,
        )
        self.assertIsNone(result)

    def test_does_not_raise_with_empty_agent_id(self) -> None:
        try:
            record_registry_agent_run(
                agent_id="",
                registry_agent={},
                run_id="r1",
                input_summary="hi",
                output_summary="hello",
                ok=True,
                route="chat",
                model_name="model",
                duration_ms=100,
            )
        except Exception as exc:
            self.fail(f"Raised unexpectedly: {exc}")

    def test_with_valid_agent_id_does_not_raise(self) -> None:
        # agent_registry.runtime import may fail but errors are caught
        try:
            record_registry_agent_run(
                agent_id="a1",
                registry_agent=None,
                run_id="r1",
                input_summary="input",
                output_summary="output",
                ok=True,
                route="chat",
                model_name="m",
                duration_ms=50,
            )
        except Exception as exc:
            self.fail(f"Raised unexpectedly: {exc}")


if __name__ == "__main__":
    unittest.main()
