"""A success criterion flipping is the strongest progress signal — it re-arms the
whole run so early shell noise can't stop a run that is now provably advancing.

Live VaultDesk d1511484: 33 tool calls, typecheck + build CONFIRMED, yet the
router stopped it as `no_progress` with `local_shell@` / `service_start@` exhausted
— the early dir/where/npm noise had burned local_shell@, and run_server logs/stop
(status/teardown) had burned service_start@, and a confirmed criterion never
re-armed either. These pin the two fixes: a criterion flip re-arms the run, and a
run_server status call is not a throttled attempt.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.progress import ProgressEvaluator  # noqa: E402


class CriterionProgressReArmTest(unittest.TestCase):
    def test_criterion_flip_rearms_all_exhausted_families(self) -> None:
        ev = ProgressEvaluator()
        # early no-progress shell noise (dir / where node) exhausts local_shell@
        ev.evaluate(name="run_bash", args={"command": "dir"}, tool_meta={"ok": True, "exit_code": 0}, fact=None)
        ev.evaluate(name="run_bash", args={"command": "where node"}, tool_meta={"ok": True, "exit_code": 0}, fact=None)
        self.assertIn("local_shell@", ev.exhausted_summary())
        # a criterion flips (a green typecheck confirmed its criterion) → full re-arm
        v = ev.evaluate(name="run_bash", args={"command": "npm run typecheck"},
                        tool_meta={"ok": True, "exit_code": 0}, fact=None, criterion_progress=True)
        self.assertEqual(v.status, "progress")
        self.assertEqual(ev.exhausted_summary(), [])
        self.assertEqual(ev.consecutive_no_progress, 0)

    def test_run_server_status_calls_do_not_exhaust_service_start(self) -> None:
        ev = ProgressEvaluator()
        v0 = ev.evaluate(name="run_server", args={"action": "start", "port": 5173},
                         tool_meta={"ok": True, "server_started": True}, fact=None)
        self.assertEqual(v0.status, "progress")         # a real start IS progress
        for a in ("logs", "stop", "list", "logs"):      # status / teardown — neutral
            v = ev.evaluate(name="run_server", args={"action": a}, tool_meta={"ok": True}, fact=None)
            self.assertEqual(v.status, "no_progress")
            self.assertFalse(v.should_stop)
        self.assertNotIn("service_start@", ev.exhausted_summary())
        self.assertEqual(ev.consecutive_no_progress, 0)  # status calls don't burn the streak

    def test_small_frontend_run_does_not_stop_after_typecheck_build_confirmed(self) -> None:
        ev = ProgressEvaluator()
        # scaffolding noise exhausts local_shell@
        ev.evaluate(name="run_bash", args={"command": "dir"}, tool_meta={"ok": True, "exit_code": 0}, fact=None)
        ev.evaluate(name="run_bash", args={"command": "where npm"}, tool_meta={"ok": True, "exit_code": 0}, fact=None)
        ev.evaluate(name="run_server", args={"action": "start", "port": 5173}, tool_meta={"ok": True}, fact=None)
        # typecheck + build confirm their criteria → re-arm
        ev.evaluate(name="run_bash", args={"command": "npm run typecheck"},
                    tool_meta={"ok": True, "exit_code": 0}, fact=None, criterion_progress=True)
        ev.evaluate(name="run_bash", args={"command": "npm run build"},
                    tool_meta={"ok": True, "exit_code": 0}, fact=None, criterion_progress=True)
        # a blocked browser on a wrong guessed port (neutral no_progress) must NOT stop the run
        v = ev.evaluate(name="browser", args={"url": "http://localhost:5174/"}, tool_meta={"ok": False}, fact=None)
        self.assertFalse(v.should_stop)
        self.assertEqual(ev.exhausted_summary(), [])


if __name__ == "__main__":
    unittest.main()
