"""Tests for application/chat_service and application/reflection_loop."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat_service.runtime import normalize_profile, run_chat, run_chat_stream  # noqa: E402
from app.application.reflection_loop.runtime import run_reflection_loop  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────────

def _mock_chunk(token: str):
    """Build a streaming chunk mock."""
    m = MagicMock()
    m.message.content = token
    return m


def _mock_response(text: str):
    """Build a single-response mock."""
    m = MagicMock()
    m.message.content = text
    return m


def _make_client(chat_return):
    client = MagicMock()
    client.chat.return_value = chat_return
    return client


# ── normalize_profile ──────────────────────────────────────────────────────────

class NormalizeProfileTest(unittest.TestCase):
    def test_empty_string_returns_default(self) -> None:
        result = normalize_profile("")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_default_keyword_returns_default(self) -> None:
        self.assertEqual(normalize_profile("default"), normalize_profile(""))

    def test_unknown_profile_falls_back_to_default(self) -> None:
        self.assertEqual(normalize_profile("nonexistent_xyz"), normalize_profile(""))

    def test_known_profile_is_preserved(self) -> None:
        from app.core.persona_defaults import PROFILE_MODE_OVERLAYS

        if not PROFILE_MODE_OVERLAYS:
            self.skipTest("No profiles defined")
        first_known = next(iter(PROFILE_MODE_OVERLAYS))
        self.assertEqual(normalize_profile(first_known), first_known)


# ── run_chat ───────────────────────────────────────────────────────────────────

class RunChatTest(unittest.TestCase):
    def _run(self, text: str = "pong", **kwargs):
        mock_client = _make_client(_mock_response(text))
        with patch("ollama.Client", return_value=mock_client):
            return run_chat(
                model_name="test-model",
                profile_name="Universal",
                user_input=kwargs.pop("user_input", "ping"),
                **kwargs,
            )

    def test_successful_chat(self) -> None:
        result = self._run(text="pong")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "pong")
        self.assertEqual(result["warnings"], [])
        self.assertIn("profile", result["meta"])

    def test_history_is_included(self) -> None:
        mock_client = MagicMock()
        mock_client.chat.return_value = _mock_response("ok")
        history = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "prev answer"},
        ]
        with patch("ollama.Client", return_value=mock_client):
            run_chat("test-model", "Universal", "new question", history=history)

        messages = mock_client.chat.call_args[1]["messages"]
        roles = [m["role"] for m in messages]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        # history + new user message
        self.assertGreaterEqual(len(messages), 4)

    def test_empty_content_history_skipped(self) -> None:
        mock_client = MagicMock()
        mock_client.chat.return_value = _mock_response("ok")
        history = [
            {"role": "user", "content": ""},   # blank — should be skipped
            {"role": "assistant", "content": "  "},  # whitespace — should be skipped
        ]
        with patch("ollama.Client", return_value=mock_client):
            run_chat("test-model", "Universal", "question", history=history)

        messages = mock_client.chat.call_args[1]["messages"]
        # system + user only (blank history skipped)
        self.assertEqual(len(messages), 2)

    def test_ollama_error_returns_not_ok(self) -> None:
        mock_client = MagicMock()
        mock_client.chat.side_effect = ConnectionError("ollama not running")
        with patch("ollama.Client", return_value=mock_client):
            result = run_chat("test-model", "Universal", "ping")

        self.assertFalse(result["ok"])
        self.assertEqual(result["answer"], "")
        self.assertTrue(len(result["warnings"]) > 0)
        self.assertIn("ollama not running", result["warnings"][0])

    def test_task_context_forwarded(self) -> None:
        mock_client = MagicMock()
        mock_client.chat.return_value = _mock_response("ok")
        with patch("ollama.Client", return_value=mock_client), \
             patch("app.application.persona_service.runtime.build_persona_prompt",
                   return_value="sys") as mock_persona:
            run_chat("test-model", "Universal", "q", task_context="context text")

        mock_persona.assert_called_once()
        _, kwargs = mock_persona.call_args
        self.assertEqual(kwargs.get("task_context") or mock_persona.call_args[0][2] if len(mock_persona.call_args[0]) > 2 else kwargs.get("task_context"), "context text")


# ── run_chat_stream ─────────────────────────────────────────────────────────────

class RunChatStreamTest(unittest.TestCase):
    def test_stream_yields_tokens(self) -> None:
        tokens = ["hel", "lo", " world"]
        mock_client = _make_client([_mock_chunk(t) for t in tokens])
        with patch("ollama.Client", return_value=mock_client):
            result = list(
                run_chat_stream("test-model", "Universal", "ping")
            )
        self.assertEqual(result, tokens)

    def test_stream_skips_empty_tokens(self) -> None:
        chunks = [_mock_chunk("a"), _mock_chunk(""), _mock_chunk("b")]
        mock_client = _make_client(chunks)
        with patch("ollama.Client", return_value=mock_client):
            result = list(run_chat_stream("test-model", "Universal", "q"))
        self.assertEqual(result, ["a", "b"])

    def test_stream_fallback_on_error(self) -> None:
        mock_client = MagicMock()
        mock_client.chat.side_effect = ConnectionError("stream broken")
        with patch("ollama.Client", return_value=mock_client), \
             patch("app.application.chat_service.runtime.run_chat",
                   return_value={"ok": True, "answer": "fallback text"}):
            result = list(run_chat_stream("test-model", "Universal", "q"))
        self.assertEqual(result, ["fallback text"])

    def test_stream_fallback_emits_error_token_when_run_chat_also_fails(self) -> None:
        mock_client = MagicMock()
        mock_client.chat.side_effect = ConnectionError("stream broken")
        with patch("ollama.Client", return_value=mock_client), \
             patch("app.application.chat_service.runtime.run_chat",
                   return_value={"ok": False, "answer": ""}):
            result = list(run_chat_stream("test-model", "Universal", "q"))
        self.assertEqual(len(result), 1)
        self.assertIn("Ошибка", result[0])


# ── run_reflection_loop ─────────────────────────────────────────────────────────

class RunReflectionLoopTest(unittest.TestCase):
    def _reflection(self, answer: str = "improved", ok: bool = True, **kwargs):
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value={"ok": ok, "answer": answer, "warnings": [], "meta": {}},
        ):
            return run_reflection_loop(
                model_name="test-model",
                profile_name="Universal",
                user_input=kwargs.pop("user_input", "fix this"),
                draft_text=kwargs.pop("draft_text", "draft"),
                review_text=kwargs.pop("review_text", "review notes"),
                **kwargs,
            )

    def test_reflection_ok(self) -> None:
        result = self._reflection(answer="better answer")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "better answer")
        self.assertIn("stage", result["meta"])

    def test_reflection_includes_context(self) -> None:
        """run_chat should receive a prompt that mentions the context."""
        captured_input = []
        with patch(
            "app.application.chat_service.runtime.run_chat",
            side_effect=lambda *a, **kw: captured_input.append(kw.get("user_input") or a[2]) or {"ok": True, "answer": "ok", "warnings": [], "meta": {}},
        ):
            run_reflection_loop(
                "test-model", "Universal",
                user_input="task",
                draft_text="draft",
                review_text="notes",
                context="extra context",
            )
        self.assertTrue(any("extra context" in s for s in captured_input))

    def test_reflection_failure_propagates(self) -> None:
        result = self._reflection(answer="", ok=False)
        self.assertFalse(result["ok"])

    def test_reflection_used_context_flag(self) -> None:
        result = self._reflection(context="ctx")
        self.assertTrue(result["meta"].get("used_context"))

    def test_reflection_no_context_flag(self) -> None:
        result = self._reflection()
        self.assertFalse(result["meta"].get("used_context"))


if __name__ == "__main__":
    unittest.main()
