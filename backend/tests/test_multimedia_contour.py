"""Multimedia runtime audit (Vision / OCR / STT / TTS)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import tempfile  # noqa: E402

from app.application.code_agent.tool_policy import BASE_TOOLS  # noqa: E402
from app.application.code_agent.tools import _vision  # noqa: E402


class RegistrationTest(unittest.TestCase):
    def test_vision_tools_do_not_bloat_the_stable_prompt_order(self):
        self.assertNotIn("read_image", BASE_TOOLS)
        self.assertNotIn("ocr_file", BASE_TOOLS)

    def test_vision_tools_registered_in_builtin_specs(self):
        from app.application.tool_registry.runtime import seed_builtin_tools
        from app.application.tool_registry.service import list_tools
        seed_builtin_tools()
        tools = list_tools()["tools"]
        names = {str(t.get("name") or t.get("tool_name") or "") for t in tools}
        self.assertIn("read_image", names)
        self.assertIn("ocr_file", names)

    def test_vision_tools_have_schemas_and_dispatch(self):
        from app.application.code_agent.tool_schemas import build_tool_schemas
        schema_names = {str((s.get("function") or {}).get("name")) for s in build_tool_schemas()}
        self.assertIn("read_image", schema_names)
        self.assertIn("ocr_file", schema_names)
        from app.application.code_agent.tools._dispatch import build_tool_dispatch
        dispatch = build_tool_dispatch(Path("."))
        self.assertIn("read_image", dispatch)
        self.assertIn("ocr_file", dispatch)

    def test_no_video_tools_exist(self):
        # Video is OUT OF SCOPE by decision — pin that none sneaks in silently.
        from app.application.code_agent.tool_schemas import build_tool_schemas
        schema_names = {str((s.get("function") or {}).get("name")) for s in build_tool_schemas()}
        for forbidden in ("read_video", "video_frames", "analyze_video", "transcribe_video"):
            self.assertNotIn(forbidden, schema_names)


class CapabilityDiscoveryTest(unittest.TestCase):
    def test_reports_configured_state_honestly(self):
        from app.application.code_agent.run_journal import discover_capabilities
        caps = discover_capabilities(model="m", tools=["read_file"])
        self.assertTrue(caps["vision"]["available"])
        self.assertTrue(caps["ocr"]["available"])
        self.assertEqual(caps["ocr"]["provider"], "server-ocr")
        self.assertNotIn("vision", caps["missing"])

    def test_vision_and_ocr_are_not_feature_gated(self):
        from app.application.code_agent.run_journal import discover_capabilities
        with patch("shutil.which", return_value=None), \
             patch("app.application.pdf.runtime._TESSERACT_CANDIDATES", []):
            caps = discover_capabilities(model="m", tools=["read_file"])
        self.assertTrue(caps["vision"]["available"])
        self.assertTrue(caps["ocr"]["available"])
        self.assertNotIn("vision", caps["missing"])
        self.assertNotIn("ocr", caps["missing"])


class ErrorPathTest(unittest.TestCase):
    """A service error must be ok=False + a clear ERROR text — the loop treats a
    missing `ok` as True, so an un-gated ERROR result read as success in events."""

    def test_missing_vision_file_is_ok_false(self):
        out = _vision.tool_read_image(Path("."), path="x.png")
        self.assertFalse(out["ok"])
        self.assertIn("ERROR", out["text"])

    def test_missing_ocr_file_is_ok_false(self):
        out = _vision.tool_ocr_file(Path("."), path="x.png")
        self.assertFalse(out["ok"])
        self.assertIn("ERROR", out["text"])

    def test_missing_file_is_ok_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_vision.tool_read_image(Path(tmp), path="no.png")["ok"])
            self.assertFalse(_vision.tool_ocr_file(Path(tmp), path="no.png")["ok"])

    def test_unreachable_service_is_ok_false_not_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "a.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n0000")
            with patch("app.infrastructure.llm.vision_ocr.describe_image", return_value=None):
                out = _vision.tool_read_image(Path(tmp), path="a.png")
            self.assertFalse(out["ok"])
            self.assertIn("ERROR", out["text"])
            with patch("app.infrastructure.llm.vision_ocr.ocr_document", return_value=None):
                out = _vision.tool_ocr_file(Path(tmp), path="a.png")
            self.assertFalse(out["ok"])
            self.assertIn("ERROR", out["text"])

    def test_stt_failure_surfaces_as_text_not_500(self):
        from app.application.file_extract.runtime import _transcribe_audio
        with patch("app.application.voice.runtime.transcribe", side_effect=RuntimeError("boom")):
            note = _transcribe_audio(b"xx", "a.wav")
        self.assertIn("не удалось расшифровать", note)   # attachment degrades, run survives


class Mp4AudioContainerTest(unittest.TestCase):
    """.mp4 (WhatsApp voice) is an AUDIO container: extract & transcribe its audio
    track via the existing STT — NOT video/frame analysis (test_no_video_tools_exist
    still pins that no video tools exist). Single canonical allowlist, no drift."""

    _MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\xff" * 32  # binary, not UTF-8

    def test_mp4_in_canonical_audio_exts(self):
        from app.application.file_extract.runtime import _AUDIO_EXTS
        self.assertIn(".mp4", _AUDIO_EXTS)

    def test_library_uses_the_same_canonical_allowlist(self):
        # No second independent copy: Library imports the SAME tuple object.
        from app.application.file_extract.runtime import _AUDIO_EXTS as FE_AUDIO
        from app.application.library.runtime import _AUDIO_EXTS as LIB_AUDIO
        self.assertIs(LIB_AUDIO, FE_AUDIO)
        self.assertIn(".mp4", LIB_AUDIO)

    def test_extract_file_mp4_calls_transcribe_with_original_filename(self):
        from app.application.file_extract.runtime import extract_file
        with patch("app.application.voice.runtime.transcribe", return_value="привет из mp4") as m:
            out = extract_file("voice.mp4", self._MP4)
        self.assertTrue(m.called)
        self.assertEqual(m.call_args.kwargs.get("filename"), "voice.mp4")  # original filename passed
        self.assertEqual(m.call_args.kwargs.get("timeout"), 3600)           # bounded long-audio budget
        self.assertEqual(out["text"], "привет из mp4")                     # transcribed, not UTF-8 garbage
        self.assertEqual(out["type"], ".mp4")

    def test_mp4_decode_failure_is_explicit_error_not_utf8_garbage(self):
        # If STT cannot decode the container → explicit attachment error, and the raw
        # MP4 bytes are NEVER decoded as UTF-8 text.
        from app.application.file_extract.runtime import extract_file
        with patch("app.application.voice.runtime.transcribe", side_effect=RuntimeError("bad container")):
            out = extract_file("voice.mp4", self._MP4)
        self.assertIn("не удалось расшифровать", out["text"])
        self.assertNotIn("ftyp", out["text"])  # container bytes not leaked as text

    def test_library_preview_mp4_routes_to_transcribe(self):
        from app.application.library.runtime import extract_preview
        with patch("app.application.voice.runtime.transcribe", return_value="из библиотеки") as m:
            preview = extract_preview("note.mp4", self._MP4)
        self.assertTrue(m.called)
        self.assertEqual(preview, "из библиотеки")

    def test_existing_audio_exts_do_not_regress(self):
        from app.application.file_extract.runtime import extract_file
        for fn in ("v.ogg", "v.m4a", "v.webm"):
            with patch("app.application.voice.runtime.transcribe", return_value="ok") as m:
                out = extract_file(fn, b"audio-bytes")
            self.assertTrue(m.called, fn)
            self.assertEqual(out["text"], "ok", fn)

if __name__ == "__main__":
    unittest.main()
