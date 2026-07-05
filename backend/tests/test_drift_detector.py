from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.application.drift import probes as drift_probes
from app.application.drift import runtime as drift_runtime
from app.application.drift import store as drift_store


class DriftDetectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = drift_store.DB_PATH
        drift_store.DB_PATH = Path(self._tmp.name) / "drift_test.db"
        drift_store.init_db()

    def tearDown(self) -> None:
        drift_store.DB_PATH = self._old_db
        self._tmp.cleanup()

    def _reconcile_with(self, facts):
        with patch.object(drift_probes, "probe_live_facts", return_value=facts), patch(
            "app.application.event_bus.runtime.emit_event"
        ) as emit:
            result = drift_runtime.reconcile()
        return result, emit

    def test_first_observation_is_not_a_drift(self) -> None:
        result, emit = self._reconcile_with(
            {"active_model": "/models/x/Qwen-Q5.gguf", "server_context_window": "65536"}
        )
        self.assertTrue(result["reachable"])
        self.assertEqual(result["drifts"], [])
        emit.assert_not_called()
        self.assertEqual(
            drift_store.get_fact("active_model")["value"], "/models/x/Qwen-Q5.gguf"
        )

    def test_unchanged_value_no_drift(self) -> None:
        facts = {"active_model": "/models/x/Qwen-Q5.gguf", "server_context_window": "65536"}
        self._reconcile_with(facts)
        result, emit = self._reconcile_with(facts)
        self.assertEqual(result["drifts"], [])
        emit.assert_not_called()

    def test_changed_value_raises_drift(self) -> None:
        # doc/prior said Qwopus...
        self._reconcile_with(
            {"active_model": "/models/x/Qwopus-Q5.gguf", "server_context_window": "65536"}
        )
        # ...live is now Qwen — exactly the 2026-07-05 case.
        result, emit = self._reconcile_with(
            {"active_model": "/models/x/Qwen-Q5.gguf", "server_context_window": "65536"}
        )
        self.assertEqual(len(result["drifts"]), 1)
        drift = result["drifts"][0]
        self.assertEqual(drift["key"], "active_model")
        self.assertEqual(drift["previous"], "/models/x/Qwopus-Q5.gguf")
        self.assertEqual(drift["current"], "/models/x/Qwen-Q5.gguf")
        emit.assert_called_once()

    def test_context_window_drift(self) -> None:
        # the prior 64K/128K bug class.
        self._reconcile_with(
            {"active_model": "/models/x/Qwen-Q5.gguf", "server_context_window": "131072"}
        )
        result, _ = self._reconcile_with(
            {"active_model": "/models/x/Qwen-Q5.gguf", "server_context_window": "65536"}
        )
        keys = {d["key"] for d in result["drifts"]}
        self.assertIn("server_context_window", keys)

    def test_unreachable_does_not_fake_drift(self) -> None:
        facts = {"active_model": "/models/x/Qwen-Q5.gguf", "server_context_window": "65536"}
        self._reconcile_with(facts)
        result, emit = self._reconcile_with(
            {"active_model": None, "server_context_window": None}
        )
        self.assertFalse(result["reachable"])
        self.assertEqual(result["drifts"], [])
        emit.assert_not_called()
        # known value preserved, not clobbered to None
        self.assertEqual(
            drift_store.get_fact("active_model")["value"], "/models/x/Qwen-Q5.gguf"
        )


if __name__ == "__main__":
    unittest.main()
