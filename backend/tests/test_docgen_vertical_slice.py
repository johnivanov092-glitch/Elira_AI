"""E2E for the DOCX vertical slice.

Covers the full chain the user asked for, with NO mocks on the DOCX path:
  agent tool-call (file_gen) -> real .docx on disk -> UI download contract
  (structured download_url/download_name the runtime streams) -> HTTP 200 from the
  download route -> a valid OOXML zip that opens as a Word document.

Plus the two guards this slice adds:
  * Russian tool_search discoverability of file_gen.
  * verify-before-link: an ok-but-missing file must not surface a download link.
  * anti-confabulation: a claimed .docx/.xlsx with no successful file_gen is flagged.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.tool_registry.runtime import seed_builtin_tools, search_tool_specs
from app.application.code_agent.tools._dispatch import build_tool_dispatch
from app.application.code_agent.loop_helpers import _unbacked_docgen_claim


ONE_LINE = "Одна строка документа."


def _make_docx(project_root: Path) -> dict:
    """Drive file_gen through the SAME dispatch the code-agent uses."""
    dispatch = build_tool_dispatch(project_root)
    return dispatch["file_gen"](
        format="word", title="E2E", content=ONE_LINE, filename="e2e_slice"
    )


# ── 1. Russian discoverability (tool_search) ─────────────────────────────────
@pytest.mark.parametrize(
    "query", ["документ", "ворд", "docx", "создать файл", "эксель", "таблица", "word"]
)
def test_file_gen_found_by_russian_query(query: str):
    seed_builtin_tools()
    names = {m["name"] for m in search_tool_specs(query, limit=20)}
    assert "file_gen" in names, f"file_gen not discoverable for query {query!r}"


# ── 2. Agent tool-call -> real .docx -> UI download contract ─────────────────
def test_file_gen_produces_download_contract(tmp_path: Path):
    res = _make_docx(tmp_path)
    # These are the exact structured fields the runtime copies onto the streamed
    # tool_call event; the frontend's deriveArtifacts reads them to build the
    # download artifact — no dependence on the model echoing a URL in prose.
    assert res.get("download_url", "").startswith("/api/skills/download/")
    assert res.get("download_name", "").endswith(".docx")
    assert res.get("touched_path", "").endswith(".docx")
    assert "ERROR" not in res.get("text", "")


# ── 3. Delivery: download route returns 200 + a valid OOXML zip ──────────────
def test_download_route_serves_valid_ooxml(tmp_path: Path):
    res = _make_docx(tmp_path)

    app = FastAPI()
    from app.api.routes.skills_routes import router

    app.include_router(router)
    client = TestClient(app)

    r = client.get(res["download_url"])
    assert r.status_code == 200
    body = r.content
    assert body[:4] == b"PK\x03\x04", "not an OOXML zip (missing PK magic)"
    assert zipfile.is_zipfile(io.BytesIO(body)), "download is not a valid zip"

    from docx import Document

    doc = Document(io.BytesIO(body))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert ONE_LINE in text, "generated .docx does not contain the requested line"

    # Missing file → 404 (route does not fabricate a body).
    assert client.get("/api/skills/download/does-not-exist.docx").status_code == 404


# ── 4. verify-before-link: ok-but-missing file → ERROR, no download link ─────
def test_missing_file_yields_error_and_no_link(tmp_path: Path, monkeypatch):
    import app.application.skills as skills

    def _fake_generate_word(title, content, filename=""):
        # Reports success but points at a path that was never written.
        return {
            "ok": True,
            "path": str(tmp_path / "ghost.docx"),
            "filename": "ghost.docx",
            "size": 123,
            "download_url": "/api/skills/download/ghost.docx",
        }

    monkeypatch.setattr(skills, "generate_word", _fake_generate_word)
    dispatch = build_tool_dispatch(tmp_path)
    res = dispatch["file_gen"](format="word", content="x", filename="ghost")

    assert "ERROR" in res.get("text", "")
    assert res.get("ok") is False                      # P1: error exits carry ok=False
    assert res.get("error") == "missing_output"        # …and a stable error code
    assert "download_url" not in res
    assert "touched_path" not in res


# ── 5. anti-confabulation guard (trusts only verified file_gen, exact match) ──
def test_unbacked_docgen_claim_guard():
    # No successful file_gen this run → a claimed doc is flagged.
    assert _unbacked_docgen_claim("Готово, вот отчёт.docx", []) == ["отчёт.docx"]
    assert _unbacked_docgen_claim("Файл data.xlsx готов", []) == ["data.xlsx"]
    # write_file produced report.docx (NOT via file_gen) → not in generated_docs →
    # flagged. (write_file paths are deliberately never passed to the guard: a plain
    # write_file can emit a UTF-8 file named report.docx that is not a Word document.)
    assert _unbacked_docgen_claim("report.docx готов", []) == ["report.docx"]
    # file_gen produced actual.docx but the answer claims report.docx → exact-name
    # mismatch → flagged (a different real doc does not back the claimed one).
    assert _unbacked_docgen_claim("Вот report.docx", ["actual.docx"]) == ["report.docx"]
    # Exact successful file_gen report.docx → not flagged (basename match; path ok).
    assert _unbacked_docgen_claim("report.docx готов", ["report.docx"]) == []
    assert _unbacked_docgen_claim("report.docx готов", ["generated/report.docx"]) == []
    # No document claim at all → nothing to flag.
    assert _unbacked_docgen_claim("Обычный ответ без файлов", []) == []


# ── 6. P1: error exits reach the kernel as status=error (not ok-by-default) ───
def _real_file_gen_dispatch(project_root: Path):
    """A dispatch_fn (name, args) -> dict backed by the REAL tool_file_gen."""
    disp = build_tool_dispatch(project_root)

    def _dispatch(name, args):
        return disp[name](**args)

    return _dispatch


def _exec_file_gen_via_kernel(args: dict, dispatch_fn):
    """Drive a real ToolExecutionRequest through the unified executor. The tool is
    given an auto-permission spec so the status-derivation path (executor line
    `status = "ok" if raw.get("ok", True) else "error"`) is exercised directly — the
    approval gate itself is covered by test_approval_flow."""
    from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool

    spec = {
        "permission": "auto",
        "max_output_chars": 50000,
        "policy_classified": True,
        "enabled": True,
    }
    with mock.patch("app.application.tool_registry.runtime.get_tool", return_value=spec), \
         mock.patch("app.application.agent_registry.sandbox.preflight_or_raise", return_value={"ok": True}):
        return execute_tool(
            ToolExecutionRequest(
                run_id="run-docgen",
                agent_id="test-agent",
                project_scope_id="scope:test",
                tool_name="file_gen",
                args=args,
                source="test",
            ),
            dispatch_fn=dispatch_fn,
        )


def test_kernel_status_error_on_unsupported_format(tmp_path: Path):
    # A genuinely unsupported format still maps to a kernel error. (pdf is now a
    # VALID format — its error path is covered by the generator-failure test below.)
    res = _exec_file_gen_via_kernel({"format": "ppt", "content": "x"}, _real_file_gen_dispatch(tmp_path))
    assert res.status == "error"
    assert res.output.get("ok") is False
    assert res.output.get("error") == "unsupported_format"
    assert res.error == "unsupported_format"


def test_kernel_status_error_on_generator_failure(tmp_path: Path, monkeypatch):
    import app.application.skills as skills

    monkeypatch.setattr(skills, "generate_word", lambda *a, **k: {"ok": False, "error": "boom"})
    res = _exec_file_gen_via_kernel({"format": "word", "content": "x"}, _real_file_gen_dispatch(tmp_path))
    assert res.status == "error"
    assert res.output.get("ok") is False
    assert res.output.get("error") == "generation_failed"


def test_kernel_status_error_on_missing_output(tmp_path: Path, monkeypatch):
    import app.application.skills as skills

    monkeypatch.setattr(
        skills,
        "generate_word",
        lambda *a, **k: {
            "ok": True,
            "path": str(tmp_path / "ghost.docx"),  # never written
            "filename": "ghost.docx",
            "size": 1,
            "download_url": "/api/skills/download/ghost.docx",
        },
    )
    res = _exec_file_gen_via_kernel({"format": "word", "content": "x"}, _real_file_gen_dispatch(tmp_path))
    assert res.status == "error"
    assert res.output.get("ok") is False
    assert res.output.get("error") == "missing_output"
    assert "download_url" not in res.output


def test_kernel_status_ok_on_success(tmp_path: Path):
    res = _exec_file_gen_via_kernel(
        {"format": "word", "content": ONE_LINE, "filename": "kernel_ok"},
        _real_file_gen_dispatch(tmp_path),
    )
    assert res.status == "ok"
    assert res.output.get("ok") is True
    assert res.output.get("download_url", "").startswith("/api/skills/download/")


# ── 7. PDF vertical slice (real Playwright/Chromium → %PDF, NO mocks) ─────────
PDF_LINE = "Одна строка PDF-документа."


def test_file_gen_pdf_real_pdf_download_and_pages(tmp_path: Path):
    # Real generation (no mock on the main path): tool-call → %PDF on disk →
    # download route 200 → pypdf opens it and sees at least one page.
    disp = build_tool_dispatch(tmp_path)
    res = disp["file_gen"](format="pdf", title="E2E PDF", content=PDF_LINE, filename="e2e_pdf")
    assert res.get("ok") is True
    assert res.get("download_url", "").startswith("/api/skills/download/")
    assert res.get("download_name", "").endswith(".pdf")

    app = FastAPI()
    from app.api.routes.skills_routes import router

    app.include_router(router)
    client = TestClient(app)

    r = client.get(res["download_url"])
    assert r.status_code == 200
    body = r.content
    assert body[:5] == b"%PDF-", "not a real PDF (missing %PDF- header)"
    assert len(body) > 0

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    assert len(reader.pages) >= 1


@pytest.mark.parametrize("query", ["сделай PDF", "пдф", "экспорт в PDF", "документ PDF"])
def test_file_gen_found_by_russian_pdf_query(query: str):
    seed_builtin_tools()
    names = {m["name"] for m in search_tool_specs(query, limit=20)}
    assert "file_gen" in names, f"file_gen not discoverable for PDF query {query!r}"


def test_kernel_status_error_on_pdf_generator_failure(tmp_path: Path, monkeypatch):
    import app.application.skills as skills

    # Error path only (the MAIN pdf path stays unmocked in the test above).
    monkeypatch.setattr(skills, "generate_pdf", lambda *a, **k: {"ok": False, "error": "chromium boom"})
    res = _exec_file_gen_via_kernel({"format": "pdf", "content": "x"}, _real_file_gen_dispatch(tmp_path))
    assert res.status == "error"
    assert res.output.get("ok") is False
    assert res.output.get("error") == "generation_failed"


def test_unbacked_pdf_claim_guard():
    # No verified file_gen this run → a claimed .pdf is flagged (a plain write_file
    # producing report.pdf is NOT proof, so it never reaches generated_docs).
    assert _unbacked_docgen_claim("report.pdf готов", []) == ["report.pdf"]
    # Exact successful file_gen → not flagged.
    assert _unbacked_docgen_claim("report.pdf готов", ["report.pdf"]) == []
    # A different produced pdf does not back the claimed one (exact-name).
    assert _unbacked_docgen_claim("Вот report.pdf", ["actual.pdf"]) == ["report.pdf"]


def test_pdf_filename_path_traversal_normalized(tmp_path: Path):
    from app.application.skills import generate_pdf
    from app.core.config import GENERATED_DIR

    res = generate_pdf("t", "body", "../../etc/passwd")
    assert res.get("ok") is True
    fn = res["filename"]
    assert fn == "passwd.pdf"
    assert "/" not in fn and "\\" not in fn and ".." not in fn
    # The file really lands INSIDE GENERATED_DIR — traversal did not escape it.
    assert Path(res["path"]).resolve().parent == GENERATED_DIR.resolve()
    # Same hardening through the tool branch.
    disp = build_tool_dispatch(tmp_path)
    tr = disp["file_gen"](format="pdf", content="x", filename=r"a\b\c")
    assert tr.get("download_name") == "c.pdf"
