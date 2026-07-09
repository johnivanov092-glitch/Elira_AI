"""Multimedia contour audit (Vision / OCR / STT / TTS) — offline focused tests.

Pins: (1) read_image / ocr_file are registered as DEFERRED (activatable) tools —
present in the registry and discoverable, NOT in the base set (compaction canary);
(2) capability discovery reports the CONFIGURED state honestly; (3) service errors
surface to the model as ok=False + a clear ERROR text — never a false success.
Video is deliberately out of scope: no video tools exist and none are added.
"""
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
    def test_vision_tools_are_deferred_not_base(self):
        self.assertNotIn("read_image", BASE_TOOLS)     # base prompt stays lean (canary)
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
        with patch.dict("os.environ", {"VISION_ENABLED": "true", "OCR_ENABLED": "true"}):
            caps = discover_capabilities(model="m", tools=["read_file"])
        self.assertTrue(caps["vision"]["available"])
        self.assertTrue(caps["ocr"]["available"])
        self.assertEqual(caps["ocr"]["provider"], "server-ocr")
        self.assertNotIn("vision", caps["missing"])

    def test_disabled_vision_lands_in_missing(self):
        from app.application.code_agent.run_journal import discover_capabilities
        with patch.dict("os.environ", {"VISION_ENABLED": "false", "OCR_ENABLED": "false"}), \
             patch("shutil.which", return_value=None), \
             patch("app.application.pdf.runtime._TESSERACT_CANDIDATES", []):
            caps = discover_capabilities(model="m", tools=["read_file"])
        self.assertFalse(caps["vision"]["available"])
        self.assertIn("vision", caps["missing"])
        self.assertIn("ocr", caps["missing"])


class ErrorPathTest(unittest.TestCase):
    """A service error must be ok=False + a clear ERROR text — the loop treats a
    missing `ok` as True, so an un-gated ERROR result read as success in events."""

    def test_disabled_vision_is_ok_false(self):
        with patch.dict("os.environ", {"VISION_ENABLED": "false"}):
            out = _vision.tool_read_image(Path("."), path="x.png")
        self.assertFalse(out["ok"])
        self.assertIn("ERROR", out["text"])

    def test_disabled_ocr_is_ok_false(self):
        with patch.dict("os.environ", {"OCR_ENABLED": "false"}):
            out = _vision.tool_ocr_file(Path("."), path="x.png")
        self.assertFalse(out["ok"])
        self.assertIn("ERROR", out["text"])

    def test_missing_file_is_ok_false(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict("os.environ", {"VISION_ENABLED": "true", "OCR_ENABLED": "true"}):
            self.assertFalse(_vision.tool_read_image(Path(tmp), path="no.png")["ok"])
            self.assertFalse(_vision.tool_ocr_file(Path(tmp), path="no.png")["ok"])

    def test_unreachable_service_is_ok_false_not_false_success(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict("os.environ", {"VISION_ENABLED": "true", "OCR_ENABLED": "true"}):
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


if __name__ == "__main__":
    unittest.main()
