"""http_api / browser must report HONEST ok + verifier evidence.

The VaultDesk live run (b51697e7) showed the core false-success: `http_api` and
`browser` were SSRF-blocked on localhost yet the tool result had no `ok` key, so
the loop defaulted ok→True and the model believed it had verified the page. These
pin: an errored/blocked runtime call is ok=False (no verifier verdict); a real
HTTP 2xx / browser render is a verifier verdict with evidence; and the scoped
loopback allowance lets the agent reach a dev server IT started.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._content import _format_runtime_result, tool_http_api  # noqa: E402
from app.application.code_agent.tools import _web  # noqa: E402


class FormatRuntimeResultTest(unittest.TestCase):
    def test_error_result_reports_ok_false(self):
        out = _format_runtime_result("X", {"ok": False, "error": "boom"})
        self.assertFalse(out["ok"])                # was silently missing → loop read True
        self.assertIn("boom", out["text"])

    def test_success_result_has_no_false_error(self):
        out = _format_runtime_result("X", {"ok": True, "value": 1})
        self.assertNotIn("ERROR", out["text"])


class HttpApiVerifierTest(unittest.TestCase):
    def _run(self, fake_result):
        with mock.patch("app.application.skills.runtime.http_request", return_value=fake_result), \
             mock.patch("app.application.code_agent.tools._run.active_server_ports", return_value={3000}):
            return tool_http_api(Path("."), url="http://localhost:3000")

    def test_blocked_is_ok_false_without_verifier(self):
        out = self._run({"ok": False, "error": "Заблокирован: localhost"})
        self.assertFalse(out["ok"])
        self.assertIsNone(out.get("verifier"))     # couldn't run → NOT a verdict
        self.assertIn("ERROR", out["text"])

    def test_2xx_is_a_passing_page_open_verdict(self):
        out = self._run({"ok": True, "status": 200, "url": "http://localhost:3000", "body": "<html>"})
        self.assertTrue(out["ok"])
        self.assertTrue(out["verifier"])
        self.assertIn("200", out["evidence"])

    def test_5xx_is_a_failing_verdict(self):
        out = self._run({"ok": True, "status": 500, "url": "http://localhost:3000", "body": "err"})
        self.assertFalse(out["ok"])                # a 500 is NOT "page opens"
        self.assertTrue(out["verifier"])           # but it IS a real verdict → can fail a criterion

    def test_loopback_ports_threaded_from_run_server(self):
        captured = {}

        def _fake_http(url, **kw):
            captured.update(kw)
            return {"ok": True, "status": 200, "url": url, "body": ""}

        with mock.patch("app.application.skills.runtime.http_request", side_effect=_fake_http), \
             mock.patch("app.application.code_agent.tools._run.active_server_ports", return_value={5173}):
            tool_http_api(Path("."), url="http://localhost:5173")
        self.assertEqual(captured.get("allow_loopback_ports"), {5173})


class BrowserHonestyTest(unittest.TestCase):
    def test_ssrf_blocked_render_is_ok_false_no_verifier(self):
        # A blocked URL returns before Playwright runs — assert ok=False, no verdict.
        with mock.patch("app.application.code_agent.tools._run.active_server_ports", return_value=set()):
            out = _web.tool_browser(url="http://localhost:3001")
        self.assertFalse(out["ok"])
        self.assertIsNone(out.get("verifier"))
        self.assertIn("SSRF blocked", out["text"])

    def test_empty_url_is_ok_false(self):
        out = _web.tool_browser(url="")
        self.assertFalse(out["ok"])
        self.assertIsNone(out.get("verifier"))


if __name__ == "__main__":
    unittest.main()
