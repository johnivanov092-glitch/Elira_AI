"""Fail-closed page QA and observable provider failures at the actual skill seam."""
from unittest.mock import Mock
import json

import pytest
import requests
from PIL import Image

from app.application.skill_services import documents as qa
from app.application.skill_services import vision


@pytest.mark.parametrize("text", [
    '[' * 1000 + '0' + ']' * 1000,
    '[]', 'null', '{"layout_issue":false,"issues":["text overlaps"]}',
    '{"layout_issue":false,"issues":[null]}',
    '{"layout_issue":true,"issues":[{"text":"bad"}]}',
    '{"layout_issue":"false","issues":[]}',
    '{"layout_issue":true,"layout_issue":false,"issues":[]}',
    '{"layout_issue":false,"issues":[]} {"layout_issue":true,"issues":["bad"]}',
    'The page is broken. {"layout_issue":false,"issues":[]}',
    '{"layout_issue":false,"issues":[],"unexpected":true}',
])
def test_invalid_or_contradictory_layout_is_never_accepted(text):
    assert qa._parse_layout_response(text) is None


@pytest.mark.parametrize("text", [
    '{"layout_issue":false,"issues":[]}',
    '```json\n{"layout_issue":false,"issues":[]}\n```',
])
def test_single_valid_layout_json_is_accepted(text):
    assert qa._parse_layout_response(text) == (False, [])


def reply(content='{"layout_issue":false,"issues":[]}', finish="stop"):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": finish}]}).encode()
    response._content_consumed = True
    return response


@pytest.mark.parametrize("failure,code", [
    (requests.Timeout("slow"), "vision_timeout"),
    (requests.ConnectionError("offline"), "vision_connection_error"),
    (requests.HTTPError(response=Mock(status_code=503)), "vision_http_error"),
    (ValueError("invalid JSON"), "vision_provider_response_invalid"),
    (requests.exceptions.JSONDecodeError("invalid JSON", "", 0), "vision_provider_response_invalid"),
])
def test_provider_failures_remain_distinguishable(monkeypatch, failure, code):
    post = Mock(side_effect=failure)
    monkeypatch.setattr(vision.requests, "post", post)
    result = vision.describe_image_result("page.png", b"png", timeout_seconds=15)
    assert result.error == code and result.text is None
    assert result.elapsed_ms >= 0
    assert post.call_count == 1  # No hidden retry multiplying the page budget.
    assert vision.describe_image("page.png", b"png") is None


def test_structured_request_and_legacy_text_share_one_client(monkeypatch):
    post = Mock(return_value=reply())
    monkeypatch.setattr(vision.requests, "post", post)
    result = vision.describe_image_result("page.png", b"png", response_schema=qa._LAYOUT_SCHEMA)
    assert result.error is None and result.finish_reason == "stop"
    assert post.call_args.kwargs["json"]["response_format"]["json_schema"]["schema"] == qa._LAYOUT_SCHEMA
    assert post.call_args.kwargs["json"]["temperature"] == 0
    assert vision.describe_image("page.png", b"png") == result.text
    assert "response_format" not in post.call_args.kwargs["json"]


@pytest.mark.parametrize("content,finish,code", [
    ('{"layout_issue":false,"issues":[]}', "length", "vision_response_truncated"),
    ("", "stop", "vision_empty_response"),
    (None, "stop", "vision_empty_response"),
])
def test_incomplete_provider_answer_is_not_success(monkeypatch, content, finish, code):
    monkeypatch.setattr(vision.requests, "post", Mock(return_value=reply(content, finish)))
    assert vision.describe_image_result("page.png", b"png").error == code


def test_page_failure_keeps_transport_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr("pdf2image.convert_from_path", lambda *a, **k: [Image.new("RGB", (16, 16))])
    monkeypatch.setattr("app.application.pdf.poppler.poppler_options", lambda: {})
    monkeypatch.setattr(vision.requests, "post", Mock(side_effect=requests.Timeout("slow")))
    status, issues = qa._inspect_pages(tmp_path / "fixture.pdf", 1)
    assert status == "unverified"
    assert [issue["code"] for issue in issues] == ["vision_timeout"]
    assert "1" in issues[0]["message"]


def test_every_page_must_pass_and_a_layout_defect_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("pdf2image.convert_from_path", lambda *a, **k: [Image.new("RGB", (16, 16)) for _ in range(2)])
    monkeypatch.setattr("app.application.pdf.poppler.poppler_options", lambda: {})
    def inspect(filename, *a, **kw):
        content = '{"layout_issue":true,"issues":["Clipped table"]}' if filename == "page-2.png" else '{"layout_issue":false,"issues":[]}'
        return vision.VisionResult(text=content, finish_reason="stop", elapsed_ms=1)
    monkeypatch.setattr(vision, "describe_image_result", inspect)
    status, issues = qa._inspect_pages(tmp_path / "fixture.pdf", 2)
    assert status == "failed" and issues[0]["code"] == "layout_issue"
    assert "2" in issues[0]["message"] and "Clipped table" in issues[0]["message"]


def test_body_read_timeout_preserves_http_status(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import time

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"choices":')
            self.wfile.flush()
            time.sleep(0.15)
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("VISION_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    try:
        result = vision.describe_image_result("page.png", b"png", timeout_seconds=0.05)
        assert result.error == "vision_timeout"
        assert result.status_code == 200 and result.text is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_libreoffice_installed_outside_path_is_found(tmp_path, monkeypatch):
    from types import SimpleNamespace
    if qa.os.name != "nt":
        pytest.skip("Windows installation discovery")
    installation = tmp_path / "programs" / "LibreOffice" / "program" / "soffice.exe"
    installation.parent.mkdir(parents=True)
    installation.write_bytes(b"test")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "programs"))
    monkeypatch.setattr(qa.shutil, "which", lambda name: None)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        (tmp_path / "input.pdf").write_bytes(b"%PDF-1.4")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qa.subprocess, "run", run)
    assert qa._render_docx_with_libreoffice(tmp_path / "input.docx", tmp_path) == tmp_path / "input.pdf"
    assert commands[0][0] == str(installation)


def test_continuous_slow_body_cannot_extend_request_budget(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import time

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = reply().content
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for offset in range(0, len(body), 2):
                    self.wfile.write(body[offset:offset + 2])
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("VISION_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    try:
        result = vision.describe_image_result("page.png", b"png", timeout_seconds=0.15)
        assert result.error == "vision_timeout" and result.status_code == 200
        assert result.text is None and result.elapsed_ms < 600
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("pages", [1, 2])
def test_vision_budget_stops_before_uninspected_pages(tmp_path, monkeypatch, pages):
    clock = [0.0]
    monkeypatch.setattr(qa.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("pdf2image.convert_from_path", lambda *a, **k: [Image.new("RGB", (16, 16)) for _ in range(pages)])
    monkeypatch.setattr("app.application.pdf.poppler.poppler_options", lambda: {})
    calls = []
    def inspect(filename, *a, **kw):
        calls.append((filename, kw["timeout_seconds"]))
        clock[0] = 6.0
        return vision.VisionResult(text='{"layout_issue":false,"issues":[]}', finish_reason="stop")
    monkeypatch.setattr(vision, "describe_image_result", inspect)
    status, issues = qa._inspect_pages(tmp_path / "fixture.pdf", pages, deadline=5.0)
    assert calls == [("page-1.png", 5.0)]
    assert status == "unverified" and issues[0]["code"] == "qa_budget_exhausted"
    assert str(pages) in issues[-1]["message"]
