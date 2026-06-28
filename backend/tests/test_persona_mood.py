"""Living Persona step B — mood (two-axis, auto-drift, decay)."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.persona import mood as m  # noqa: E402


class MoodLabelTest(unittest.TestCase):
    def test_baseline_is_even(self) -> None:
        self.assertEqual(m.mood_label(0.5, 0.55), "ровная")

    def test_high_energy_high_warmth(self) -> None:
        self.assertEqual(m.mood_label(0.8, 0.8), "тёплая и бодрая")

    def test_high_energy_low_warmth(self) -> None:
        self.assertEqual(m.mood_label(0.8, 0.4), "энергичная, собранная")

    def test_low_energy_high_warmth(self) -> None:
        self.assertEqual(m.mood_label(0.2, 0.8), "тихая и тёплая")

    def test_low_energy_low_warmth(self) -> None:
        self.assertEqual(m.mood_label(0.2, 0.4), "тихая, сосредоточенная")


class MoodOverlayTest(unittest.TestCase):
    def test_overlay_has_label_and_guard(self) -> None:
        line = m.mood_overlay_line({"energy": 0.8, "warmth": 0.8, "label": "тёплая и бодрая"})
        self.assertIn("тёплая и бодрая", line)
        # The honesty/identity guard must always ride along.
        self.assertIn("не меняя сути", line)


class SignalDeltasTest(unittest.TestCase):
    def test_warm_text_raises_both(self) -> None:
        de, dw = m._signal_deltas("спасибо, ты очень помогла, классно!")
        self.assertGreater(dw, 0)
        self.assertGreater(de, 0)

    def test_frustration_lowers_energy(self) -> None:
        de, dw = m._signal_deltas("опять баг, ничего не выходит, я устал")
        self.assertLess(de, 0)

    def test_neutral_no_change(self) -> None:
        de, dw = m._signal_deltas("расскажи про столицу Казахстана")
        self.assertEqual((de, dw), (0.0, 0.0))


class DecayTest(unittest.TestCase):
    def test_decays_toward_baseline(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(minutes=2 * m._DECAY_HALFLIFE_MIN)).isoformat()
        e, w = m._decayed(0.95, 0.95, old)
        # After two half-lives the distance to baseline shrinks to ~1/4.
        self.assertLess(e, 0.95)
        self.assertLess(w, 0.95)
        self.assertGreater(e, m.BASELINE["energy"])

    def test_warmth_never_below_floor(self) -> None:
        e, w = m._decayed(0.0, 0.0, datetime.now(timezone.utc).isoformat())
        self.assertGreaterEqual(w, m._WARMTH_FLOOR)


class MoodRoundTripTest(unittest.TestCase):
    """Relative assertions (pollution-proof: compared within one method)."""

    def test_frustration_does_not_raise_energy(self) -> None:
        before = m.get_mood()["energy"]
        m.nudge_mood(user_text="опять баг, не работает, я устал")
        after = m.get_mood()["energy"]
        self.assertLessEqual(after, before)

    def test_warm_exchange_raises_warmth(self) -> None:
        before = m.get_mood()["warmth"]
        m.nudge_mood(user_text="спасибо большое, ты молодец, обнимаю!")
        after = m.get_mood()["warmth"]
        self.assertGreaterEqual(after, before)

    def test_get_mood_shape(self) -> None:
        mood = m.get_mood()
        self.assertIn("energy", mood)
        self.assertIn("warmth", mood)
        self.assertIn("label", mood)
        self.assertGreaterEqual(mood["warmth"], m._WARMTH_FLOOR)


if __name__ == "__main__":
    unittest.main()
