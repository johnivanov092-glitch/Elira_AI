"""Tests — SSRF guard (P4 Шаг 10)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.web.ssrf_guard import check_ssrf  # noqa: E402


class TestCheckSsrf(unittest.TestCase):

    # ── Blocked ───────────────────────────────────────────────────────────────

    def test_localhost_blocked(self):
        self.assertIsNotNone(check_ssrf("http://localhost/api"))
        self.assertIsNotNone(check_ssrf("http://localhost:8080/"))

    def test_loopback_ip_blocked(self):
        self.assertIsNotNone(check_ssrf("http://127.0.0.1/"))
        self.assertIsNotNone(check_ssrf("http://127.0.0.2/test"))

    def test_private_class_a_blocked(self):
        self.assertIsNotNone(check_ssrf("http://10.0.0.1/admin"))
        self.assertIsNotNone(check_ssrf("http://10.255.255.255/"))

    def test_private_class_b_blocked(self):
        self.assertIsNotNone(check_ssrf("http://172.16.0.1/"))
        self.assertIsNotNone(check_ssrf("http://172.31.255.255/"))

    def test_private_class_c_blocked(self):
        self.assertIsNotNone(check_ssrf("http://192.168.1.1/"))
        self.assertIsNotNone(check_ssrf("https://192.168.100.200:8443/"))

    def test_link_local_blocked(self):
        self.assertIsNotNone(check_ssrf("http://169.254.169.254/"))
        self.assertIsNotNone(check_ssrf("http://169.254.1.1/metadata"))

    def test_metadata_server_blocked(self):
        self.assertIsNotNone(check_ssrf("http://metadata.google.internal/"))

    def test_non_http_scheme_blocked(self):
        self.assertIsNotNone(check_ssrf("ftp://example.com/"))
        self.assertIsNotNone(check_ssrf("file:///etc/passwd"))
        self.assertIsNotNone(check_ssrf("ssh://192.168.1.1/"))

    def test_empty_url_blocked(self):
        self.assertIsNotNone(check_ssrf(""))
        self.assertIsNotNone(check_ssrf("   "))

    def test_hostname_resolving_to_private_blocked(self):
        # Simulate DNS resolution returning a private IP
        with mock.patch(
            "app.application.web.ssrf_guard._resolve_host",
            return_value=["10.0.0.1"],
        ):
            reason = check_ssrf("http://internal-service.company.local/")
        self.assertIsNotNone(reason)
        self.assertIn("10.0.0.1", reason)

    # ── Allowed ───────────────────────────────────────────────────────────────

    def test_public_http_allowed(self):
        with mock.patch("app.application.web.ssrf_guard._resolve_host",
                        return_value=["93.184.216.34"]):
            self.assertIsNone(check_ssrf("http://example.com/"))

    def test_public_https_allowed(self):
        with mock.patch("app.application.web.ssrf_guard._resolve_host",
                        return_value=["1.1.1.1"]):
            self.assertIsNone(check_ssrf("https://cloudflare.com/"))

    def test_public_ip_allowed(self):
        self.assertIsNone(check_ssrf("https://8.8.8.8/"))

    def test_dns_failure_does_not_block(self):
        # When DNS resolution fails (empty list), we allow — blocking is best-effort
        with mock.patch("app.application.web.ssrf_guard._resolve_host",
                        return_value=[]):
            self.assertIsNone(check_ssrf("http://unknown-host.example.org/"))

    # ── Integration: tool_web_fetch ──────────────────────────────────────────

    def test_tool_web_fetch_blocks_private_url(self):
        from app.application.code_agent.tools import tool_web_fetch
        result = tool_web_fetch(url="http://192.168.1.1/admin")
        self.assertIn("SSRF blocked", result["text"])

    def test_tool_web_fetch_blocks_localhost(self):
        from app.application.code_agent.tools import tool_web_fetch
        result = tool_web_fetch(url="http://localhost:8080/api/secret")
        self.assertIn("SSRF blocked", result["text"])

    # ── Integration: web_runtime.fetch_page_text ─────────────────────────────

    def test_web_runtime_fetch_blocks_private(self):
        from app.infrastructure.search.web_runtime import fetch_page_text
        result = fetch_page_text("http://10.0.0.1/internal")
        self.assertIn("SSRF blocked", result)


if __name__ == "__main__":
    unittest.main()
