"""Living Persona step C — proactivity framework (master switch, gate, rate-limit)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.persona import proactive as p  # noqa: E402


def _on():
    """Master switch ON via env override (flag_enabled reads env first)."""
    return patch.dict(os.environ, {"ELIRA_PROACTIVE": "1"}, clear=False)


def _off():
    return patch.dict(os.environ, {"ELIRA_PROACTIVE": "0"}, clear=False)


class MasterSwitchTest(unittest.TestCase):
    def test_off_by_default_returns_nothing(self) -> None:
        with _off():
            out = p.consider_proactive({"edited": True, "verified": False})
        self.assertEqual(out["suggestions"], [])
        self.assertEqual(out["enable_asks"], [])


class EvaluateTest(unittest.TestCase):
    def test_noticed_issue_on_edited_unverified(self) -> None:
        self.assertIsNotNone(p._evaluate("noticed_issue", {"edited": True, "verified": False}))

    def test_next_step_on_edited_verified(self) -> None:
        self.assertIsNotNone(p._evaluate("next_step", {"edited": True, "verified": True}))

    def test_no_fire_on_readonly_turn(self) -> None:
        self.assertIsNone(p._evaluate("noticed_issue", {"edited": False, "verified": False}))
        self.assertIsNone(p._evaluate("next_step", {"edited": False, "verified": False}))


class GateAndRateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        # reset the three turn triggers to a clean unknown state
        for tid in ("noticed_issue", "next_step", "state_condition"):
            p.set_trigger_status(tid, "unknown")
            # clear last_fired so rate-limit doesn't suppress
            conn = p.persona_store.connect()
            try:
                conn.execute("UPDATE persona_triggers SET last_fired_at = 0 WHERE trigger_id = ?", (tid,))
                conn.commit()
            finally:
                conn.close()

    def test_unknown_trigger_asks_to_enable(self) -> None:
        with _on(), patch.object(p, "_create_enable_ask", return_value="appr-1"):
            out = p.consider_proactive({"edited": True, "verified": False})
        self.assertEqual(len(out["enable_asks"]), 1)
        self.assertEqual(out["enable_asks"][0]["trigger_id"], "noticed_issue")
        self.assertEqual(out["suggestions"], [])

    def test_denied_trigger_is_silent(self) -> None:
        p.set_trigger_status("noticed_issue", "denied")
        with _on(), patch.object(p, "_create_enable_ask", return_value="x"):
            out = p.consider_proactive({"edited": True, "verified": False})
        self.assertEqual(out["enable_asks"], [])
        self.assertEqual(out["suggestions"], [])

    def test_approved_trigger_suggests(self) -> None:
        p.set_trigger_status("next_step", "approved")
        with _on():
            out = p.consider_proactive({"edited": True, "verified": True})
        self.assertEqual(len(out["suggestions"]), 1)

    def test_rate_limited_second_call_silent(self) -> None:
        p.set_trigger_status("next_step", "approved")
        with _on():
            first = p.consider_proactive({"edited": True, "verified": True})
            second = p.consider_proactive({"edited": True, "verified": True})
        self.assertEqual(len(first["suggestions"]), 1)
        self.assertEqual(second["suggestions"], [])  # rate-limited


class DecideFromApprovalTest(unittest.TestCase):
    def test_approve_enables_trigger(self) -> None:
        p.set_trigger_status("state_condition", "unknown")
        p.decide_from_approval("proactive:state_condition", "approved")
        self.assertEqual(p.get_trigger("state_condition")["status"], "approved")

    def test_reject_denies_trigger(self) -> None:
        p.set_trigger_status("state_condition", "unknown")
        p.decide_from_approval("proactive:state_condition", "rejected")
        self.assertEqual(p.get_trigger("state_condition")["status"], "denied")

    def test_non_proactive_tool_ignored(self) -> None:
        # Must not raise / must not touch triggers for ordinary tool approvals.
        p.decide_from_approval("run_bash", "approved")


class ListTriggersTest(unittest.TestCase):
    def test_lists_all_four(self) -> None:
        data = p.list_triggers()
        ids = {t["trigger_id"] for t in data["triggers"]}
        self.assertEqual(ids, {"next_step", "noticed_issue", "state_condition", "scheduled"})
        self.assertIn("proactive_enabled", data)


class ScheduledQueueTest(unittest.TestCase):
    def _reset(self) -> None:
        p.set_trigger_status("scheduled", "unknown")
        conn = p.persona_store.connect()
        try:
            conn.execute("DELETE FROM persona_pending_suggestions")
            conn.execute("DELETE FROM persona_proactive_config")
            conn.commit()
        finally:
            conn.close()

    def setUp(self) -> None:
        p._ensure_aux_tables()
        self._reset()

    def test_queue_and_dismiss(self) -> None:
        qid = p.queue_suggestion("scheduled", "digest", "T", "B")
        self.assertEqual(len(p.list_pending()), 1)
        p.dismiss_suggestion(qid)
        self.assertEqual(len(p.list_pending()), 0)

    def test_mark_read_drops_from_unread(self) -> None:
        qid = p.queue_suggestion("scheduled", "digest", "T", "B")
        p.mark_read(qid)
        self.assertEqual(len(p.list_pending(only_unread=True)), 0)
        self.assertEqual(len(p.list_pending(only_unread=False)), 1)

    def test_respond_enable_ask_approves_trigger(self) -> None:
        qid = p.queue_suggestion("scheduled", "enable_ask", "T", "B")
        p.respond_suggestion(qid, "approve")
        self.assertEqual(p.get_trigger("scheduled")["status"], "approved")
        self.assertEqual(len(p.list_pending()), 0)  # dismissed

    def test_set_checkin_time_validates(self) -> None:
        self.assertTrue(p.set_checkin_time("07:30")["ok"])
        self.assertEqual(p.get_proactive_config()["checkin_time"], "07:30")
        self.assertFalse(p.set_checkin_time("25:00")["ok"])

    def test_digest_is_nonempty_text(self) -> None:
        self.assertTrue(p.build_checkin_digest().strip())


class ScheduledDaemonTest(unittest.TestCase):
    def _reset(self) -> None:
        p.set_trigger_status("scheduled", "unknown")
        conn = p.persona_store.connect()
        try:
            conn.execute("DELETE FROM persona_pending_suggestions")
            conn.execute("DELETE FROM persona_proactive_config")
            conn.commit()
        finally:
            conn.close()

    def setUp(self) -> None:
        p._ensure_aux_tables()
        self._reset()
        p.set_checkin_time("09:00")

    def test_master_off_does_nothing(self) -> None:
        import datetime
        with _off():
            self.assertIsNone(p.run_scheduled_checkin(now=datetime.datetime(2030, 1, 1, 10, 0)))

    def test_before_time_does_nothing(self) -> None:
        import datetime
        with _on():
            self.assertIsNone(p.run_scheduled_checkin(now=datetime.datetime(2030, 1, 1, 8, 0)))

    def test_unknown_status_queues_enable_ask(self) -> None:
        import datetime
        with _on():
            out = p.run_scheduled_checkin(now=datetime.datetime(2030, 1, 1, 10, 0))
        self.assertEqual(out["kind"], "enable_ask")
        pend = p.list_pending()
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["kind"], "enable_ask")

    def test_approved_queues_digest_once_per_day(self) -> None:
        import datetime
        p.set_trigger_status("scheduled", "approved")
        with _on():
            first = p.run_scheduled_checkin(now=datetime.datetime(2030, 1, 1, 10, 0))
            second = p.run_scheduled_checkin(now=datetime.datetime(2030, 1, 1, 11, 0))
        self.assertEqual(first["kind"], "digest")
        self.assertIsNone(second)  # already fired today

    def test_denied_status_silent(self) -> None:
        import datetime
        p.set_trigger_status("scheduled", "denied")
        with _on():
            out = p.run_scheduled_checkin(now=datetime.datetime(2030, 1, 1, 10, 0))
        self.assertIsNone(out)
        self.assertEqual(len(p.list_pending()), 0)


if __name__ == "__main__":
    unittest.main()
