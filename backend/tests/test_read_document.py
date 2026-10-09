"""Plain reads stay local; document and OCR work require mutable skills."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._files import tool_read_file  # noqa: E402



class ReadDocumentTest(unittest.TestCase):



    def test_plain_text_file_unchanged(self):
        # regression: normal text reads still work the old way
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("print('hello')\n", encoding="utf-8")
            r = tool_read_file(Path(tmp), path="a.py")
        self.assertIn("hello", r["text"])
        self.assertNotIn("file_extract", r["text"])

    def test_mangled_cyrillic_filename_suggests_exact_name_without_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Оценка диагноза Исаева.txt").write_text(
                "СОДЕРЖИМОЕ документа 999", encoding="utf-8")
            # Latin о,c look-alikes + a dropped final letter:
            r = tool_read_file(Path(tmp), path="Оценка диагнoза Иcаев.txt")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "file_not_found")
        self.assertIn("Оценка диагноза Исаева.txt", r["text"])
        self.assertNotIn("СОДЕРЖИМОЕ документа 999", r["text"])
        self.assertNotIn("touched_path", r)

    def test_missing_margin_file_never_reads_similar_verification_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "verify_repro.txt").write_text(
                "OTHER DOCUMENT VERIFICATION", encoding="utf-8")
            r = tool_read_file(Path(tmp), path="margin_repro.txt")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "file_not_found")
        self.assertIn("verify_repro.txt", r["text"])
        self.assertNotIn("OTHER DOCUMENT VERIFICATION", r["text"])
        self.assertNotIn("touched_path", r)



    def test_missing_file_with_no_match_lists_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "unrelated_report.txt").write_text("x", encoding="utf-8")
            r = tool_read_file(Path(tmp), path="totally_different_zzzzz.txt")
        self.assertIn("not a file or does not exist", r["text"])
        self.assertIn("unrelated_report.txt", r["text"])  # dir hint


if __name__ == "__main__":
    unittest.main()


def test_documents_and_images_require_skill_without_reading_binary_payload(tmp_path, monkeypatch):
    import pytest
    for extension in (".pdf", ".docx", ".doc", ".pptx", ".xls", ".xlsx", ".xlsm", ".png", ".jpg"):
        path = tmp_path / ("private" + extension)
        path.write_bytes(b"PRIVATE_BINARY_CAN_LOOK_ASCII")
        with monkeypatch.context() as context:
            context.setattr(Path, "read_bytes", lambda _: pytest.fail("builtin read document bytes"))
            result = tool_read_file(tmp_path, path=path.name)
        assert result["ok"] is False and result["error"] == "skill_required"
        assert ("document-read" if extension not in {".png", ".jpg"} else "ocr") in result["text"]
        assert "PRIVATE_BINARY" not in result["text"]


def test_runtime_bound_resource_requires_materialization_without_content_lookup(tmp_path, monkeypatch):
    import pytest
    from app.application.media import resource_store
    monkeypatch.setattr(resource_store, "read_bytes", lambda _: pytest.fail("resource contents read"))
    from app.application.agent_kernel.runtime_context import bind_runtime_context
    with bind_runtime_context({"resource_id": "a" * 32, "resource_name": "attachment.docx"}):
        result = tool_read_file(tmp_path, path="attachment.docx")
    assert result["ok"] is False and result["error"] == "resource_requires_materialize"
    assert result["resolved_from_resource"] is True
    assert result["resource_id"] == "a" * 32
    assert "resource_materialize" in result["text"] and "document-read" in result["text"]


def test_all_known_audio_video_extensions_refuse_even_ascii_bytes_before_read(tmp_path, monkeypatch):
    import pytest
    from app.core.file_types import AUDIO_EXTS, VIDEO_EXTS
    for extension in sorted(set(AUDIO_EXTS) | VIDEO_EXTS):
        source = tmp_path / ("private" + extension)
        source.write_bytes(b"PRIVATE_MEDIA_CAN_LOOK_ASCII")
        with monkeypatch.context() as context:
            context.setattr(Path, "read_bytes", lambda _: pytest.fail("media payload read as text"))
            result = tool_read_file(tmp_path, path=source.name)
        assert result["ok"] is False and result["error"] == "binary_file"
        assert "PRIVATE_MEDIA" not in result["text"]
