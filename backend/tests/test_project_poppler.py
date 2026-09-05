import os
from pathlib import Path

import pytest
from PIL import Image

from app.application.pdf import poppler, runtime
from app.application.code_agent import document_validation


@pytest.fixture
def bundled_poppler(tmp_path, monkeypatch):
    monkeypatch.setattr(poppler, "ROOT_DIR", tmp_path)
    binary_dir = tmp_path / ".runtime" / "poppler" / "Library" / "bin"
    binary_dir.mkdir(parents=True)
    for name in ("pdfinfo", "pdftoppm"):
        (binary_dir / (name + (".exe" if os.name == "nt" else ""))).write_bytes(b"fixture")
    return binary_dir


def test_project_poppler_takes_precedence_over_path(bundled_poppler, monkeypatch):
    monkeypatch.setenv("PATH", "unrelated-tools")
    assert poppler.poppler_options() == {"poppler_path": str(bundled_poppler)}


def test_other_installations_keep_system_package_support(tmp_path, monkeypatch):
    monkeypatch.setattr(poppler, "ROOT_DIR", tmp_path)
    assert poppler.poppler_options() == {}


def test_incomplete_project_install_does_not_silently_use_unrelated_path(bundled_poppler):
    (bundled_poppler / ("pdfinfo.exe" if os.name == "nt" else "pdfinfo")).unlink()
    with pytest.raises(RuntimeError, match="Incomplete project Poppler"):
        poppler.poppler_options()


@pytest.mark.parametrize("operation", ["preview", "ocr", "document_qa"])
def test_all_pdf_conversion_paths_use_project_poppler(bundled_poppler, tmp_path, monkeypatch, operation):
    seen = []
    def convert(*args, **kwargs):
        seen.append(kwargs["poppler_path"])
        return [Image.new("RGB", (32, 32), "white")]
    monkeypatch.setattr("pdf2image.convert_from_bytes", convert)
    monkeypatch.setattr("pdf2image.convert_from_path", convert)
    if operation == "preview":
        monkeypatch.setattr(runtime, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(runtime, "_count_pages", lambda data: 1)
        assert runtime.render_pdf_pages(b"fixture")["rendered"] == 1
    elif operation == "ocr":
        monkeypatch.setattr(runtime, "_ensure_tesseract", lambda module: True)
        monkeypatch.setattr("pytesseract.image_to_string", lambda *a, **k: "OCR fixture")
        assert "OCR fixture" in runtime._try_ocr(b"fixture", 100)
    else:
        monkeypatch.setattr("app.infrastructure.llm.vision_ocr.describe_image", lambda *a, **k: '{"layout_issue": false, "issues": []}')
        assert document_validation._inspect_pages(Path("fixture.pdf"), 1)[0] == "passed"
    assert seen == [str(bundled_poppler)]
