"""Tests for application/workflows/execution.py — workflow_duration_ms (pure)."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.workflows.execution import (  # noqa: E402
    workflow_duration_ms,
)


# ─────────────────────────────────────────────────────────────────────────────
# application/workflows/execution.py — workflow_duration_ms
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowDurationMsTest(unittest.TestCase):
    """Tests for ``workflow_duration_ms(run: dict) -> int``."""

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_int(self) -> None:
        self.assertIsInstance(workflow_duration_ms({"started_at": ""}), int)

    # ── invalid / missing started_at returns 0 ────────────────────────────────

    def test_empty_started_at_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({"started_at": ""}), 0)

    def test_none_started_at_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({"started_at": None}), 0)

    def test_missing_started_at_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({}), 0)

    def test_invalid_date_string_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({"started_at": "not-a-date"}), 0)

    # ── valid past timestamp returns non-negative int ─────────────────────────

    def test_past_timestamp_returns_nonnegative(self) -> None:
        result = workflow_duration_ms({"started_at": "2020-01-01T00:00:00+00:00"})
        self.assertGreaterEqual(result, 0)

    def test_past_timestamp_returns_positive(self) -> None:
        result = workflow_duration_ms({"started_at": "2020-01-01T00:00:00+00:00"})
        self.assertGreater(result, 0)

    def test_very_recent_timestamp_returns_nonneg(self) -> None:
        # 1 second in the past
        from datetime import datetime, timezone, timedelta
        ts = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        result = workflow_duration_ms({"started_at": ts})
        self.assertGreaterEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
