"""P1.6 — periodic task-recovery scheduler.

Startup recovery runs once; this daemon timer re-runs recover_stale_tasks on an
interval so a long-lived server self-heals stale tasks without a restart.
A stop_event lets the loop be shut down gracefully (used here so the test
daemons stop instead of lingering and spamming logs).
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.task_planner.service import start_task_recovery_scheduler  # noqa: E402


class TaskRecoverySchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._stop = threading.Event()
        self._thread = None

    def tearDown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def test_scheduler_invokes_recover_periodically(self) -> None:
        fired = threading.Event()
        calls: list[int] = []

        def fake_recover() -> None:
            calls.append(1)
            fired.set()

        self._thread = start_task_recovery_scheduler(
            interval_seconds=0.05, recover_fn=fake_recover, stop_event=self._stop
        )
        self.assertTrue(self._thread.daemon)
        self.assertEqual(self._thread.name, "task-recovery")
        self.assertTrue(fired.wait(3.0), "recover_fn was not invoked by the scheduler")
        self.assertGreaterEqual(len(calls), 1)

    def test_scheduler_survives_recover_errors(self) -> None:
        fired = threading.Event()

        def boom() -> None:
            fired.set()
            raise RuntimeError("recover failed")

        self._thread = start_task_recovery_scheduler(
            interval_seconds=0.05, recover_fn=boom, stop_event=self._stop
        )
        # The loop must swallow the error and keep the daemon alive.
        self.assertTrue(fired.wait(3.0))
        self.assertTrue(self._thread.is_alive())

    def test_stop_event_halts_the_loop(self) -> None:
        calls: list[int] = []
        self._thread = start_task_recovery_scheduler(
            interval_seconds=0.05, recover_fn=lambda: calls.append(1), stop_event=self._stop
        )
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.assertFalse(self._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
