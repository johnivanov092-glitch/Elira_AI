"""Living Persona step D (slice 1) — voice/TTS client + route."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.voice import runtime as voice_runtime  # noqa: E402
from app.main import app  # noqa: E402


class VoiceRuntimeTest(unittest.TestCase):
    def test_tts_url_default(self) -> None:
        import os
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELIRA_TTS_URL", None)
            self.assertTrue(voice_runtime.tts_url().startswith("http"))

    def test_status_failsafe_on_unreachable(self) -> None:
        # A bogus host must degrade to ok=False, never raise.
        import os
        with patch.dict(os.environ, {"ELIRA_TTS_URL": "http://127.0.0.1:1"}, clear=False):
            st = voice_runtime.tts_status()
        self.assertFalse(st["ok"])
        self.assertEqual(st["voices"], [])

    def test_list_voices_failsafe(self) -> None:
        import os
        with patch.dict(os.environ, {"ELIRA_TTS_URL": "http://127.0.0.1:1"}, clear=False):
            self.assertEqual(voice_runtime.list_voices(), [])


class VoiceRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_voices_route(self) -> None:
        with patch.object(voice_runtime, "list_voices", return_value=["ru_RU-irina-medium"]):
            r = self.client.get("/api/voice/voices")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["voices"], ["ru_RU-irina-medium"])

    def test_tts_empty_text_400(self) -> None:
        r = self.client.post("/api/voice/tts", json={"text": "   "})
        self.assertEqual(r.status_code, 400)

    def test_tts_returns_wav(self) -> None:
        fake_wav = b"RIFF\x00\x00\x00\x00WAVE"
        with patch.object(voice_runtime, "synthesize", return_value=fake_wav):
            r = self.client.post("/api/voice/tts", json={"text": "привет", "voice": "ru_RU-irina-medium"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "audio/wav")
        self.assertTrue(r.content.startswith(b"RIFF"))

    def test_tts_upstream_failure_502(self) -> None:
        def _boom(*a, **k):
            raise RuntimeError("upstream down")
        with patch.object(voice_runtime, "synthesize", _boom):
            r = self.client.post("/api/voice/tts", json={"text": "привет"})
        self.assertEqual(r.status_code, 502)


if __name__ == "__main__":
    unittest.main()
