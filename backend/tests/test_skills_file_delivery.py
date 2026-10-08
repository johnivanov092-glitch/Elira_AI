from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import skills_routes
from app.application.code_agent import document_validation


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
