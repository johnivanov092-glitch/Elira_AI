"""http_api / browser report honest ok and verifier evidence."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._content import _format_runtime_result  # noqa: E402
from webskill.application.code_agent.tools import _web  # noqa: E402


class FormatRuntimeResultTest(unittest.TestCase):
    def test_error_result_reports_ok_false(self):
        out = _format_runtime_result("X", {"ok": False, "error": "boom"})
        self.assertFalse(out["ok"])                # was silently missing → loop read True
        self.assertIn("boom", out["text"])

    def test_success_result_has_no_false_error(self):
        out = _format_runtime_result("X", {"ok": True, "value": 1})
        self.assertNotIn("ERROR", out["text"])


class HttpSkillHonestyTest(unittest.TestCase):
    def _run(self, status=200, failure=None):
        from types import SimpleNamespace
        from webskill.http import http_request
        response = SimpleNamespace(status_code=status, headers={}, text='response body',
            url='http://localhost:3000', elapsed=SimpleNamespace(total_seconds=lambda: 0.01))
        with mock.patch('webskill.http.http_lib.get', return_value=response, side_effect=failure):
            return http_request('http://localhost:3000')

    def test_transport_failure_is_explicit_without_verifier(self):
        out = self._run(failure=RuntimeError('offline'))
        self.assertFalse(out['ok'])
        self.assertIsNone(out.get('verifier'))
        self.assertIn('offline', out['error'])

    def test_2xx_status_is_preserved_without_claiming_browser_verdict(self):
        out = self._run(200)
        self.assertTrue(out['ok'])
        self.assertEqual(out['status'], 200)
        self.assertIsNone(out.get('verifier'))

    def test_5xx_is_visible_and_not_a_passing_page_verdict(self):
        out = self._run(500)
        self.assertEqual(out['status'], 500)
        self.assertEqual(out['body'], 'response body')
        self.assertIsNone(out.get('verifier'))

    def test_loopback_request_uses_skill_http_without_core_server_registry(self):
        from webskill.http import http_request
        with mock.patch('webskill.http.http_lib.get', side_effect=RuntimeError('fixture')) as get:
            http_request('http://localhost:5173')
        self.assertEqual(get.call_args.kwargs['url'], 'http://localhost:5173')


class BrowserHonestyTest(unittest.TestCase):
    def test_empty_url_is_ok_false(self):
        out = _web.tool_browser(url="")
        self.assertFalse(out["ok"])
        self.assertIsNone(out.get("verifier"))


if __name__ == "__main__":
    unittest.main()
