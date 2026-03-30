from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.identity_guard import guard_identity_response  # noqa: E402


class PersonaServiceTest(unittest.TestCase):
    def test_identity_guard_blocks_model_self_identification(self) -> None:
        guarded = guard_identity_response(
            "Как тебя зовут? кто ты?",
            "Меня зовут Gemma. Я большая языковая модель, разработанная компанией Google DeepMind.",
        )

        self.assertTrue(guarded["changed"])
        self.assertIn("Elira", guarded["text"])
        self.assertNotIn("Gemma", guarded["text"])
        self.assertNotIn("Google DeepMind", guarded["text"])
        self.assertNotIn("языковая модель", guarded["text"].lower())

    def test_bootstrap_promotion_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            script = textwrap.dedent(
                f"""
                import json
                import sys
                sys.path.insert(0, r"{BACKEND_ROOT}")

                from app.services.persona_service import (
                    get_model_calibration,
                    get_persona_status,
                    observe_dialogue,
                    rollback_persona,
                )

                before = get_persona_status()
                for dialog_id, session_id in [("d1", "s1"), ("d2", "s1"), ("d3", "s2")]:
                    observe_dialogue(
                        dialog_id=dialog_id,
                        session_id=session_id,
                        profile_name="Универсальный",
                        model_name="gemma3:4b",
                        user_input="Помоги мне и скажи прямо, если данных мало",
                        answer_text="Давай помогу. Следующие шаги: 1. Проверим данные. 2. Скажу прямо, если данных недостаточно. " + ("x" * 2300),
                        route="chat",
                        outcome_ok=True,
                    )

                after = get_persona_status()
                calibration = get_model_calibration("gemma3:4b", version_id=after["active_version"])
                rolled = rollback_persona(1)

                print(json.dumps({{
                    "before_version": before["active_version"],
                    "after_version": after["active_version"],
                    "quarantine": after["quarantine_candidates"],
                    "traits": after["latest_traits"],
                    "models": after["model_consistency"],
                    "calibration": calibration,
                    "rolled_version": rolled["active_version"],
                }}, ensure_ascii=False))
                """
            )

            env = os.environ.copy()
            env["ELIRA_DATA_DIR"] = data_dir

            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout.strip())

            self.assertEqual(payload["before_version"], 1)
            self.assertGreaterEqual(payload["after_version"], 2)
            self.assertTrue(payload["traits"])
            self.assertTrue(payload["models"])
            self.assertIn("calibration", payload["calibration"])
            self.assertEqual(payload["rolled_version"], 1)


if __name__ == "__main__":
    unittest.main()
