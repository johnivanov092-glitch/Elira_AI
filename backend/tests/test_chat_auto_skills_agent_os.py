"""Tests for live chat/agent_os helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.agent_os import (  # noqa: E402
    emit_agent_os_event,
    record_registry_agent_run,
    resolve_agent_os_source_id,
)


class ResolveAgentOsSourceIdTest(unittest.TestCase):
    def test_explicit_agent_id_wins(self) -> None:
        self.assertEqual(resolve_agent_os_source_id("explicit", {"id": "registry"}), "explicit")

    def test_registry_agent_id_used_when_no_explicit(self) -> None:
        self.assertEqual(resolve_agent_os_source_id(None, {"id": "registry-agent"}), "registry-agent")

    def test_missing_ids_return_empty_string(self) -> None:
        self.assertEqual(resolve_agent_os_source_id(None, None), "")
        self.assertEqual(resolve_agent_os_source_id(None, {}), "")


class EmitAgentOsEventTest(unittest.TestCase):
    def test_does_not_raise(self) -> None:
        self.assertIsNone(emit_agent_os_event(event_type="test.event"))


class RecordRegistryAgentRunTest(unittest.TestCase):
    def test_no_agent_id_is_noop(self) -> None:
        self.assertIsNone(record_registry_agent_run(
            agent_id="",
            registry_agent=None,
            run_id="run-1",
            input_summary="input",
            output_summary="output",
            ok=True,
            route="code_agent",
            model_name="local-model",
            duration_ms=1,
        ))


if __name__ == "__main__":
    unittest.main()
