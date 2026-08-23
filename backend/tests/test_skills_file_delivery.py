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
