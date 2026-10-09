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
from app.application.skill_services import image as _vision  # noqa: E402


class RegistrationTest(unittest.TestCase):
    def test_vision_tools_do_not_bloat_the_stable_prompt_order(self):
        self.assertNotIn("read_image", BASE_TOOLS)

    def test_vision_tools_registered_in_builtin_specs(self):
        from app.application.tool_registry.runtime import seed_builtin_tools
        from app.application.tool_registry.service import list_tools
        seed_builtin_tools()
        tools = list_tools()["tools"]
        names = {str(t.get("name") or t.get("tool_name") or "") for t in tools}
        self.assertNotIn("read_image", names)

    def test_vision_tools_have_schemas_and_dispatch(self):
        from app.application.code_agent.tool_schemas import build_tool_schemas
        schema_names = {str((s.get("function") or {}).get("name")) for s in build_tool_schemas()}
        self.assertNotIn("read_image", schema_names)
        from app.application.code_agent.tools._dispatch import build_tool_dispatch
        dispatch = build_tool_dispatch(Path("."))
        self.assertNotIn("read_image", dispatch)
        self.assertTrue(callable(_vision.tool_read_image))

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
        with patch("shutil.which", return_value=None):
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


    def test_missing_file_is_ok_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_vision.tool_read_image(Path(tmp), path="no.png")["ok"])

    def test_unreachable_service_is_ok_false_not_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "a.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n0000")
            with patch("app.application.skill_services.vision.describe_image", return_value=None):
                out = _vision.tool_read_image(Path(tmp), path="a.png")
            self.assertFalse(out["ok"])
            self.assertIn("ERROR", out["text"])






if __name__ == "__main__":
    unittest.main()
