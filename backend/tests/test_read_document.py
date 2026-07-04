"""read_file must extract text from on-disk pdf/docx/pptx/xlsx, not reject them.

Live catch (medical-docs run): read_file returned "binary file (not text)" for a
.pdf/.docx, so the model fumbled with run_bash+PyMuPDF/PowerShell. Now read_file
routes document types through the shared file_extract pipeline (pypdf/pdfplumber
+OCR fallback / python-docx / pptx / openpyxl).
"""
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

_EXTRACT = "app.application.file_extract.runtime.extract_file"


class ReadDocumentTest(unittest.TestCase):
    def test_real_docx_is_extracted_not_rejected(self):
        # end-to-end through python-docx (installed in the app venv)
        try:
            from docx import Document
        except Exception:
            self.skipTest("python-docx not installed")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "real.docx"
            doc = Document()
            doc.add_paragraph("СЕКРЕТНАЯ СТРОКА в документе 7391")
            doc.save(str(p))
            r = tool_read_file(Path(tmp), path="real.docx")
        self.assertNotIn("binary file", r["text"])
        self.assertIn("СЕКРЕТНАЯ СТРОКА", r["text"])
        self.assertIn("7391", r["text"])

    def test_pdf_is_routed_through_file_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "doc.pdf").write_bytes(b"%PDF-1.4 fake bytes")
            with mock.patch(_EXTRACT, return_value={"ok": True, "text": "Текст из PDF, строка A"}):
                r = tool_read_file(Path(tmp), path="doc.pdf")
        self.assertNotIn("binary file", r["text"])
        self.assertIn("Текст из PDF, строка A", r["text"])
        self.assertIn("file_extract", r["text"])  # header marks the source

    def test_scanned_pdf_empty_text_gives_helpful_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "scan.pdf").write_bytes(b"%PDF fake scan")
            with mock.patch(_EXTRACT, return_value={"ok": True, "text": "   \n  "}):
                r = tool_read_file(Path(tmp), path="scan.pdf")
        self.assertIn("не извлечён", r["text"])
        self.assertIn("OCR", r["text"])

    def test_plain_text_file_unchanged(self):
        # regression: normal text reads still work the old way
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("print('hello')\n", encoding="utf-8")
            r = tool_read_file(Path(tmp), path="a.py")
        self.assertIn("hello", r["text"])
        self.assertNotIn("file_extract", r["text"])


if __name__ == "__main__":
    unittest.main()
