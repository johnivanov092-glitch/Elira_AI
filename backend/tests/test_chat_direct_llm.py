from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import chat as chat_routes  # noqa: E402


class DirectLlmChatRouteTest(unittest.TestCase):
    def test_send_direct_llm_bypasses_orchestrated_agent(self) -> None:
        payload = chat_routes.ChatRequest(
            model_name="gemma3:4b",
            profile_name="Универсальный",
            user_input="hello",
            history=[{"role": "user", "content": "hello"}],
            direct_llm=True,
        )

        with (
            patch.object(
                chat_routes,
                "run_chat",
                return_value={"ok": True, "answer": "direct answer", "warnings": [], "meta": {}},
            ) as run_chat_mock,
            patch.object(chat_routes, "run_agent") as run_agent_mock,
        ):
            response = chat_routes.chat_send(payload)

        data = json.loads(response.body.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["content"], "direct answer")
        self.assertEqual(data["meta"]["route"], "direct_llm")
        run_agent_mock.assert_not_called()
        run_chat_mock.assert_called_once()
        self.assertEqual(run_chat_mock.call_args.kwargs["history"], [])


if __name__ == "__main__":
    unittest.main()
