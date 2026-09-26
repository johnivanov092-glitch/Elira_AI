"""Mixed PDFs retain native pages and OCR provenance in the real extractor."""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.file_extract.runtime import extract_file
from app.application.pdf import runtime as pdf_runtime
from app.infrastructure.llm import vision_ocr


def _pdf(*texts: str) -> bytes:
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _response(monkeypatch, pages, *, errors=None):
    result = {"text": "\n\n".join(p["text"] for p in pages), "pages": pages,
              "errors": errors or [], "confidence": 0.93}
    post = Mock(return_value=Mock(json=Mock(return_value=result)))
    monkeypatch.setattr(vision_ocr.requests, "post", post)
    return post


def _page(number, text):
    return {"page": number, "text": text, "width": 1224, "height": 1584,
            "dpi": 144, "coordinate_unit": "pixels", "confidence": 0.93,
            "engine": "paddleocr", "errors": [], "blocks": [
                {"text": text, "confidence": 0.93, "bbox": [10, 20, 300, 60],
                 "polygon": [[10, 20], [300, 20], [300, 60], [10, 60]]},
            ]}


def test_mixed_pdf_ocr_only_missing_page_and_preserves_order(monkeypatch):
    post = _response(monkeypatch, [_page(1, "РАСПОЗНАННЫЙ СКАН 7391")])
    first, last = "First native text. " * 12, "Last native text. " * 12
    result = extract_file("mixed.pdf", _pdf(first, "", last))
    assert "РАСПОЗНАННЫЙ СКАН 7391" in result["text"]
    assert result["text"].index("First native") < result["text"].index("СКАН")
    assert result["text"].index("СКАН") < result["text"].index("Last native")
    uploaded = post.call_args.kwargs["files"]["file"][1]
    assert len(PdfReader(io.BytesIO(uploaded)).pages) == 1
    metadata = result["document"]["pages"]
    assert [page["page"] for page in metadata] == [1, 2, 3]
    assert metadata[1]["blocks"][0]["bbox"] == [10, 20, 300, 60]
    assert metadata[1]["confidence"] == 0.93


def test_partial_ocr_failure_preserves_native_and_remaps_errors(monkeypatch):
    failed = _page(2, "")
    failed["errors"] = [{"page": 2, "code": "primary_failed", "message": "test failure"}]
    _response(monkeypatch, [_page(1, "РАСПОЗНАНО 123"), failed], errors=failed["errors"])
    fallback = Mock(return_value={})
    monkeypatch.setattr(pdf_runtime, "_try_ocr_pages", fallback)
    result = extract_file("partial.pdf", _pdf("Native content. " * 12, "", "HEADER"))
    assert "Native content" in result["text"] and "HEADER" in result["text"]
    assert "РАСПОЗНАНО 123" in result["text"]
    assert fallback.call_args.args[1] == [3]
    assert any(e["page"] == 3 and e["code"] == "primary_failed"
               for e in result["document"]["errors"])
    assert result["document"]["pages"][2]["errors"][0]["page"] == 3


def test_server_failure_uses_local_only_for_missing_pages(monkeypatch):
    monkeypatch.setattr(vision_ocr.requests, "post", Mock(side_effect=vision_ocr.requests.Timeout()))
    fallback = Mock(return_value={2: "ЛОКАЛЬНО 456"})
    monkeypatch.setattr(pdf_runtime, "_try_ocr_pages", fallback)
    result = extract_file("fallback.pdf", _pdf("Native text. " * 12, ""))
    assert fallback.call_args.args[1] == [2]
    assert "ЛОКАЛЬНО 456" in result["text"]
    assert result["document"]["pages"][1]["method"] == "ocr"


def test_local_fallback_keeps_page_numbers_and_closes_images(monkeypatch):
    import pdf2image
    import pytesseract

    images = [Mock(), Mock()]
    render = Mock(side_effect=[[images[0]], [images[1]]])
    recognize = Mock(side_effect=[RuntimeError("ru unavailable"), "Third page", "Seventh page"])
    monkeypatch.setattr(pdf2image, "convert_from_bytes", render)
    monkeypatch.setattr(pytesseract, "image_to_string", recognize)
    monkeypatch.setattr(pdf_runtime, "_ensure_tesseract", lambda _: True)
    assert pdf_runtime._try_ocr_pages(b"pdf", [3, 7], 100) == {3: "Third page", 7: "Seventh page"}
    assert [call.kwargs["first_page"] for call in render.call_args_list] == [3, 7]
    assert all(call.kwargs["timeout"] == 60 for call in render.call_args_list)
    assert all(call.kwargs["timeout"] == 30 for call in recognize.call_args_list)
    for image in images:
        image.close.assert_called_once()


def test_character_cap_stops_before_later_scan_and_avoids_ocr(monkeypatch):
    post = _response(monkeypatch, [])
    result = extract_file("bounded.pdf", _pdf("Native text. " * 100, ""), max_chars=160)
    assert len(result["text"]) <= 160
    assert result["document"]["truncated"] is True
    assert result["document"]["page_count"] == 2
    post.assert_not_called()


def test_ocr_result_keeps_structure_and_legacy_string_contract(monkeypatch):
    _response(monkeypatch, [_page(1, " Русский текст 21 ")],
              errors=[{"code": "recovered", "message": "fallback used"}])
    structured = vision_ocr.ocr_document_result("scan.png", b"image")
    assert structured["pages"][0]["blocks"][0]["polygon"][0] == [10, 20]
    assert structured["errors"][0]["code"] == "recovered"
    assert vision_ocr.ocr_document("scan.png", b"image") == "Русский текст 21"
    monkeypatch.setattr(vision_ocr.requests, "post", Mock(return_value=Mock(json=Mock(return_value=[]))))
    assert vision_ocr.ocr_document_result("scan.png", b"image") is None


def test_resource_extract_exposes_page_provenance(monkeypatch):
    from types import SimpleNamespace
    from app.application.media import processing

    _response(monkeypatch, [_page(1, "Скан 789")])
    data = _pdf("Native text. " * 12, "")
    monkeypatch.setattr(processing.resource_store, "read_bytes", lambda _: data)
    record = SimpleNamespace(original_name="mixed.pdf", kind="document", resource_id="r-test")
    result = processing._extract_text(record)
    assert result["ok"] is True
    assert "Скан 789" in result["text"]
    assert result["document"]["pages"][1]["page"] == 2
    assert result["document"]["pages"][1]["blocks"][0]["bbox"] == [10, 20, 300, 60]


def test_library_full_text_uses_same_pagewise_extraction(monkeypatch):
    from app.application.library.runtime import extract_full_text

    _response(monkeypatch, [_page(1, "Скан для поиска 456")])
    text = extract_full_text("mixed.pdf", _pdf("Native text. " * 12, ""))
    assert "Скан для поиска 456" in text
    assert "Страница 2" in text
