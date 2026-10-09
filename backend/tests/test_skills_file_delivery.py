from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest
from fastapi import HTTPException

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import skills_routes
from app.application.skill_services import documents as document_validation


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(skills_routes.router)
    return TestClient(app)


def test_pdf_download_and_inline_view_use_pdf_media_type(tmp_path: Path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    with patch.object(skills_routes, "OUTPUT_DIR", tmp_path):
        download = _client().get("/api/skills/download/report.pdf")
        preview = _client().get("/api/skills/view/report.pdf")

    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert "attachment" in download.headers.get("content-disposition", "").lower()
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "application/pdf"
    assert "attachment" not in preview.headers.get("content-disposition", "").lower()


def test_retired_generation_endpoints_return_404_and_shared_routes_remain():
    client = _client()
    for operation in ("word", "excel", "pdf"):
        response = client.post(f"/api/skills/generate/{operation}", json={"content": "Body"})
        assert response.status_code == 404
    paths = {route.path for route in skills_routes.router.routes}
    assert {"/api/skills/download/{filename}", "/api/skills/view/{filename}",
            "/api/skills/files", "/api/skills/sql/query", "/api/skills/http",
            "/api/skills/screenshot"} <= paths


def test_docx_preview_caches_rendered_pages_but_downloads_original(tmp_path, monkeypatch):
    source = tmp_path / "report.docx"
    source.write_bytes(b"original docx")
    renders = []

    def render(path, directory):
        renders.append(path.read_bytes())
        pdf = directory / "rendered.pdf"
        pdf.write_bytes(b"%PDF-1.4\n" + path.read_bytes())
        return pdf, "test_renderer"

    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(document_validation, "_render_to_pdf", render)
    client = _client()
    first = client.get("/api/skills/view/report.docx")
    second = client.get("/api/skills/view/report.docx")
    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == "application/pdf"
    assert first.content == second.content == b"%PDF-1.4\noriginal docx"
    assert "attachment" not in first.headers.get("content-disposition", "")
    assert renders == [b"original docx"]
    assert client.get("/api/skills/download/report.docx").content == b"original docx"
    source.write_bytes(b"updated docx")
    assert client.get("/api/skills/view/report.docx").content == b"%PDF-1.4\nupdated docx"
    assert renders == [b"original docx", b"updated docx"]


def test_unavailable_docx_renderer_does_not_break_download(tmp_path, monkeypatch):
    (tmp_path / "report.docx").write_bytes(b"original docx")
    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(document_validation, "_render_to_pdf", lambda *a: (None, "unavailable"))
    client = _client()
    assert client.get("/api/skills/view/report.docx").status_code == 503
    assert client.get("/api/skills/download/report.docx").content == b"original docx"


def test_docx_preview_rejects_source_changed_during_render(tmp_path, monkeypatch):
    source = tmp_path / "report.docx"
    source.write_bytes(b"original")

    def render(path, directory):
        source.write_bytes(b"changed")
        pdf = directory / "rendered.pdf"
        pdf.write_bytes(b"%PDF-1.4\noriginal")
        return pdf, "test_renderer"

    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(document_validation, "_render_to_pdf", render)
    assert _client().get("/api/skills/view/report.docx").status_code == 503
    assert not list((tmp_path / ".previews").glob("*.pdf"))


@pytest.mark.parametrize("route", [skills_routes.download_file, skills_routes.view_file])
def test_file_routes_reject_parent_paths_and_directories(tmp_path, monkeypatch, route):
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "directory").mkdir()
    (tmp_path / "private.txt").write_text("private", encoding="utf-8")
    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", generated)
    for name in ("../private.txt", "..\\private.txt", str(tmp_path / "private.txt"), "directory"):
        with pytest.raises(HTTPException) as error:
            route(name)
        assert error.value.status_code == 404


def test_listing_urls_roundtrip_special_characters(tmp_path, monkeypatch):
    name = "Отчёт #1 + 50%.pdf"
    (tmp_path / name).write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", tmp_path)
    client = _client()
    item = client.get("/api/skills/files").json()["files"][0]
    assert item["download_url"] == "/api/skills/download/" + quote(name, safe="")
    assert client.get(item["download_url"]).content == (tmp_path / name).read_bytes()


def test_page_preview_is_versioned_and_keeps_original(tmp_path, monkeypatch):
    from PIL import Image
    from pypdf import PdfWriter
    import re

    name = "Страницы #1.pdf"
    source = tmp_path / name
    pdf = PdfWriter()
    for _ in range(2):
        pdf.add_blank_page(width=612, height=792)
    pdf.write(source)
    original = source.read_bytes()
    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("app.application.pdf.poppler.poppler_options", lambda: {})
    renders = []
    def render(*args, **kwargs):
        renders.append(kwargs)
        return [Image.new("RGB", (32, 24), "white")]
    monkeypatch.setattr("pdf2image.convert_from_path", render)
    client = _client()
    url = "/api/skills/view/" + quote(name, safe="")
    response = client.get(url + "?pages=true")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "Страница 2 из 2" in response.text
    version = re.search(r"version=([a-f0-9]{64})", response.text)[1]
    page_url = url + f"?page=2&version={version}"
    page = client.get(page_url)
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"
    assert page.content.startswith(b"\x89PNG")
    assert client.get(page_url).content == page.content
    assert len(renders) == 1 and renders[0]["first_page"] == renders[0]["last_page"] == 2
    assert renders[0]["timeout"] == 30
    assert client.get(url + f"?page=3&version={version}").status_code == 404
    assert client.get(url + "?page=1&version=old").status_code == 409
    assert client.get("/api/skills/download/" + quote(name, safe="")).content == original
    source.write_bytes(original + b"\n% new version")
    assert client.get(page_url).status_code == 409


def test_page_renderer_failure_is_a_visible_error(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    from pdf2image.exceptions import PDFPopplerTimeoutError
    source = tmp_path / "report.pdf"
    pdf = PdfWriter()
    pdf.add_blank_page(width=612, height=792)
    pdf.write(source)
    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("app.application.pdf.poppler.poppler_options", lambda: {})
    def fail(*a, **k):
        raise PDFPopplerTimeoutError("slow")
    monkeypatch.setattr("pdf2image.convert_from_path", fail)
    digest = document_validation.document_sha256(source)
    response = _client().get(f"/api/skills/view/report.pdf?page=1&version={digest}")
    assert response.status_code == 503
    assert "скачать" in response.json()["detail"]
    assert not list((tmp_path / ".previews").glob("*.png"))


def test_file_routes_and_listing_reject_escaping_symlink(tmp_path, monkeypatch):
    generated = tmp_path / "generated"
    generated.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("private", encoding="utf-8")
    link = generated / "escape.txt"
    try:
        link.symlink_to(private)
    except OSError:
        pytest.skip("OS does not allow symlinks")
    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", generated)
    client = _client()
    assert client.get("/api/skills/download/escape.txt").status_code == 404
    assert client.get("/api/skills/view/escape.txt").status_code == 404
    assert client.get("/api/skills/files").json()["files"] == []


def test_older_mutable_skill_keeps_raw_docx_and_reports_page_unavailability(tmp_path, monkeypatch):
    source = tmp_path / "report.docx"
    source.write_bytes(b"original docx")
    preview = tmp_path / "cached.pdf"
    preview.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(skills_routes, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(document_validation, "document_preview", lambda *a: preview)
    monkeypatch.setattr(document_validation, "_page_count", lambda *a: 1)
    monkeypatch.delattr(document_validation, "document_page_preview")
    client = _client()
    assert client.get("/api/skills/view/report.docx").status_code == 200
    assert client.get("/api/skills/view/report.docx?page=1&version=cached").status_code == 503
    assert client.get("/api/skills/download/report.docx").content == b"original docx"
