"""Real streamed HTTP bodies obey page deadlines, byte caps and cleanup."""
import gzip
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from webskill import research
from webskill.infrastructure.search.web_runtime import fetch_page


@pytest.fixture
def http_server(monkeypatch):
    paths = []
    disconnected = threading.Event()
    body = ("Official page with readable text. " * 20).encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            paths.append(self.path)
            if self.path in {"/drip-status", "/drip-headers"}:
                try:
                    if self.path == "/drip-status":
                        for value in b"HTTP/1.1 200 Slowly Delivered Status\r\n":
                            self.wfile.write(bytes([value]))
                            self.wfile.flush()
                            time.sleep(0.05)
                    else:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                        self.wfile.flush()
                        for index in range(30):
                            self.wfile.write(f"X-Drip-{index}: value\r\n".encode())
                            self.wfile.flush()
                            time.sleep(0.05)
                    self.wfile.write(b"Content-Length: 4\r\nConnection: close\r\n\r\ndone")
                    self.wfile.flush()
                except OSError:
                    disconnected.set()
                self.close_connection = True
                return
            if self.path == "/header-redirect":
                time.sleep(0.35)
                self.send_response(302)
                self.send_header("Location", "/drip-headers")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/plain")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/slow-headers":
                time.sleep(1.15)
            compressed = self.path in {"/gzip", "/gzip-bomb"}
            payload = gzip.compress(b"x" * (9 * 1024 * 1024) if self.path == "/gzip-bomb" else body) if compressed else body
            if self.path == "/wire-oversize":
                payload = b"x" * (8 * 1024 * 1024 + 1)
            drip = self.path.startswith("/drip")
            chunked = self.path in {"/drip-chunked", "/drip-chunk-header"}
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            if self.path == "/unknown-encoding":
                self.send_header("Content-Encoding", "unknown")
            self.send_header("Connection", "close")
            self.send_header("Transfer-Encoding" if chunked else "Content-Length",
                             "chunked" if chunked else str(1000 if drip else len(payload)))
            self.end_headers()
            try:
                if drip:
                    if self.path == "/drip-chunk-header":
                        self.wfile.write(b"1;extension=")
                        self.wfile.flush()
                    for _ in range(100):
                        self.wfile.write(b"1\r\nx\r\n" if self.path == "/drip-chunked" else b"x")
                        self.wfile.flush()
                        time.sleep(0.05)
                    if chunked:
                        self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(payload)
            except OSError:
                disconnected.set()
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **kw: None)
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", paths=paths,
                              body=body, disconnected=disconnected)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize("path", ["/drip", "/drip-chunked", "/drip-chunk-header"])
def test_real_body_drip_is_interrupted_at_page_deadline_and_closed(http_server, path):
    token = research._PAGE_DEADLINE.set(time.time() + 2)
    started = time.monotonic()
    try:
        result = fetch_page(http_server.url + path)
    finally:
        research._PAGE_DEADLINE.reset(token)
    assert not result.ok and "timed out" in result.error
    assert 1.5 < time.monotonic() - started < 3
    assert http_server.disconnected.wait(timeout=1)


@pytest.mark.parametrize("path", ["/drip-status", "/drip-headers", "/header-redirect"])
def test_status_and_headers_share_page_deadline_across_redirects(http_server, path):
    token = research._PAGE_DEADLINE.set(time.time() + 0.5)
    started = time.monotonic()
    try:
        result = fetch_page(http_server.url + path)
    finally:
        research._PAGE_DEADLINE.reset(token)
    assert not result.ok and "timed out" in result.error
    assert 0.35 < time.monotonic() - started < 1
    assert http_server.disconnected.wait(timeout=1)
    if path == "/header-redirect":
        assert http_server.paths == ["/header-redirect", "/drip-headers"]


@pytest.mark.parametrize("path", ["/plain", "/gzip", "/redirect", "/slow-headers"])
def test_streamed_normal_gzip_redirect_and_slow_headers_remain_readable(http_server, path):
    token = research._PAGE_DEADLINE.set(time.time() + 4)
    try:
        result = fetch_page(http_server.url + path)
    finally:
        research._PAGE_DEADLINE.reset(token)
    assert result.ok and result.text.encode() == http_server.body
    if path == "/redirect":
        assert http_server.paths == ["/redirect", "/plain"]
        assert result.final_url.endswith("/plain")


@pytest.mark.parametrize("path,cap", [("/wire-oversize", "wire cap"), ("/gzip-bomb", "decoded cap")])
def test_wire_and_decompressed_body_are_capped(http_server, path, cap):
    result = fetch_page(http_server.url + path)
    assert not result.ok and cap in result.error and not result.text


def test_unsupported_content_encoding_is_not_claimed_as_read_text(http_server):
    result = fetch_page(http_server.url + "/unknown-encoding")
    assert not result.ok and "unsupported page Content-Encoding" in result.error
    assert not result.text


def test_missing_socket_fails_honestly_and_closes_response(monkeypatch):
    response = SimpleNamespace(status_code=200, url="https://example.org", headers={},
                               raw=SimpleNamespace(), close=Mock())
    monkeypatch.setattr("webskill.infrastructure.search.web_runtime._get_page_response", Mock(return_value=response))
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **kw: None)
    result = fetch_page(response.url)
    assert not result.ok and "enforceable timeout" in result.error
    response.close.assert_called_once_with()
