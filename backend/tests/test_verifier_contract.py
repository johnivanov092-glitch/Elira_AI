"""The GENERIC verifier contract (Ph7.9).

DONE is decided by intent + target + verifier EVIDENCE — never by the task's name or
the model's final text. There is NO `if "VaultDesk"` anywhere: the same intent set
(command_check / server_started / page_open / dom_contains / viewport_layout /
file_exists / file_not_exists / content_contains / content_not_contains) verifies a
landing page, a dashboard, a backend smoke test, and an SSH cleanup task alike.

VaultDesk and NovaPanel are two DIFFERENT fixtures proving the same logic — if a
regression ever hardcodes product words, the NovaPanel case fails.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.taskspec import (  # noqa: E402
    CriteriaTracker,
    TaskSpec,
    _criterion_intent,
    derive_task_spec,
)


class IntentClassificationTest(unittest.TestCase):
    """Intent comes from the TARGET CONTEXT (page/file/server/command), across domains."""

    CASES = [
        # command_check
        ("`npm run typecheck` проходит без ошибок", "command_check"),
        ("проект успешно собирается (build)", "command_check"),
        ("pytest tests/test_x.py проходит", "command_check"),
        ("smoke-тест бэкенда проходит", "command_check"),
        # server_started
        ("dev server запускается через `run_server`", "server_started"),
        ("`run_server` возвращает фактический `actual_url` / `actual_port`", "server_started"),
        ("сервис слушает порт 8080", "server_started"),
        # page_open
        ("`browser` открывает страницу на `actual_url` без SSRF-block", "page_open"),
        ("endpoint `/health` возвращает 200", "page_open"),
        # dom_contains — the live bug: "содержит" in a DOM context ≠ file content
        ("rendered DOM содержит текст `VaultDesk`", "dom_contains"),
        ("есть секция с преимуществами: `Inventory`, `Backups`", "dom_contains"),
        ("на первом экране видно название `NovaPanel`", "dom_contains"),
        # content vs dom disambiguation
        ("файл `config.json` содержит `debug`", "content_contains"),
        ("файл `app.log` не содержит `ERROR`", "content_not_contains"),
        # files
        ("файл `C:\\lab\\health.txt` существует", "file_exists"),
        ("временный файл `C:\\lab\\tmp.txt` удалён (cleanup)", "file_not_exists"),
        # viewport
        ("страница адаптивна для desktop и mobile", "viewport_layout"),
    ]

    def test_intents(self):
        for text, expected in self.CASES:
            self.assertEqual(_criterion_intent(text), expected, msg=text)

    def test_no_product_words_leak_into_logic(self):
        # The classifier must not special-case any product name: swapping VaultDesk→
        # NovaPanel→Foo must not change the intent.
        for name in ("VaultDesk", "NovaPanel", "Foo42"):
            self.assertEqual(_criterion_intent(f"rendered DOM содержит текст `{name}`"), "dom_contains")
            self.assertEqual(_criterion_intent(f"на экране видно `{name}`"), "dom_contains")


def _frontend_task(product: str, tokens: list[str]) -> str:
    lines = [
        f"Цель: landing для `{product}`.",
        "Критерии готовности:",
        "- `npm run typecheck` проходит без ошибок",
        "- `npm run build` проходит без ошибок",
        "- dev server запускается через `run_server`",
        "- `run_server` возвращает фактический `actual_url` / `actual_port`",
        "- `browser` открывает страницу на `actual_url` без SSRF-block",
    ]
    lines += [f"- rendered DOM содержит текст `{t}`" for t in tokens]
    return "\n".join(lines)


def _run_full_frontend(product: str, tokens: list[str], *, dom_text: str,
                       server_started: bool = True, browser_ok: bool = True) -> CriteriaTracker:
    t = CriteriaTracker.from_spec(derive_task_spec(_frontend_task(product, tokens)))
    t.record(tool_name="run_bash", args={"command": "npm run typecheck"}, ok=True, evidence="exit 0")
    t.record(tool_name="run_bash", args={"command": "npm run build"}, ok=True, evidence="exit 0")
    if server_started:
        t.record(tool_name="run_server", args={"action": "start", "command": "npm run dev"},
                 ok=True, evidence="dev server started: http://localhost:3000",
                 meta={"verifier": True, "actual_port": 3000, "actual_url": "http://localhost:3000"})
    t.record(tool_name="browser", args={"url": "http://localhost:3000"},
             ok=browser_ok, evidence=(dom_text if browser_ok else ""), meta={"verifier": True})
    return t


class TwoFixtureGenericTest(unittest.TestCase):
    """Same verifier logic, two different products — proves no hardcoding."""

    def test_vaultdesk_reaches_confirmed(self):
        toks = ["VaultDesk", "Inventory", "Backups", "Alerts", "Home Lab", "Pro", "Team", "Start local audit"]
        dom = "TITLE: VaultDesk — Admin. VaultDesk Inventory Backups Alerts Home Lab Pro Team Start local audit"
        t = _run_full_frontend("VaultDesk", toks, dom_text=dom)
        self.assertEqual(t.completion_status(), "confirmed")

    def test_novapanel_reaches_confirmed_same_logic(self):
        toks = ["NovaPanel", "Users", "Jobs", "Settings"]
        dom = "TITLE: NovaPanel. NovaPanel dashboard — Users Jobs Settings overview"
        t = _run_full_frontend("NovaPanel", toks, dom_text=dom)
        self.assertEqual(t.completion_status(), "confirmed")

    def test_novapanel_missing_token_stays_partial(self):
        toks = ["NovaPanel", "Users", "Jobs", "Settings"]
        dom = "TITLE: NovaPanel. NovaPanel dashboard — Users Jobs overview"  # no "Settings"
        t = _run_full_frontend("NovaPanel", toks, dom_text=dom)
        self.assertEqual(t.completion_status(), "partial")
        st = {it["text"]: it["status"] for it in t.items}
        self.assertEqual([s for txt, s in st.items() if "Settings" in txt], ["unconfirmed"])


class ServerVerifierTest(unittest.TestCase):
    def _spec(self):
        return TaskSpec(success_criteria=[
            "dev server запускается через `run_server`",
            "`run_server` возвращает фактический `actual_url`",
        ])

    def test_successful_start_confirms_server_criteria(self):
        t = CriteriaTracker.from_spec(self._spec())
        ch = t.record(tool_name="run_server", args={"action": "start"}, ok=True,
                      evidence="dev server started: http://localhost:5174",
                      meta={"verifier": True, "actual_port": 5174, "actual_url": "http://localhost:5174"})
        self.assertTrue(ch)
        self.assertEqual(t.completion_status(), "confirmed")

    def test_failed_start_does_not_confirm(self):
        # A start that died ("Port in use") is ok=False → it must not confirm anything.
        t = CriteriaTracker.from_spec(self._spec())
        t.record(tool_name="run_server", args={"action": "start"}, ok=False,
                 evidence="", meta={})
        self.assertEqual(t.completion_status(), "unverified")

    def test_list_of_running_server_confirms(self):
        t = CriteriaTracker.from_spec(self._spec())
        t.record(tool_name="run_server", args={"action": "list"}, ok=True,
                 evidence="dev server running via run_server: http://localhost:3000 (pid=18588)",
                 meta={"verifier": True, "actual_port": 3000, "actual_url": "http://localhost:3000"})
        self.assertEqual(t.completion_status(), "confirmed")


class NegativeEvidenceTest(unittest.TestCase):
    def test_blocked_browser_does_not_confirm_dom(self):
        # A blocked/failed browser (ok=False, empty evidence) cannot confirm text.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["rendered DOM содержит текст `VaultDesk`"]))
        t.record(tool_name="browser", args={"url": "http://localhost:5174"}, ok=False, evidence="", meta={})
        self.assertEqual(t.items[0]["status"], "unconfirmed")

    def test_bundle_grep_does_not_confirm_dom(self):
        # findstr on the built bundle finds the token but is NOT a rendered-DOM verdict.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["rendered DOM содержит текст `VaultDesk`"]))
        t.record(tool_name="run_bash",
                 args={"command": 'findstr /C:"VaultDesk" dist\\assets\\index.js'},
                 ok=True, evidence="VaultDesk found in bundle")
        self.assertEqual(t.items[0]["status"], "unconfirmed")

    def test_http_200_does_not_confirm_dom_text(self):
        # HTTP 200 proves the page opens, not that its DOM contains a token.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "`browser` открывает страницу на `actual_url`",     # page_open
            "rendered DOM содержит текст `VaultDesk`",           # dom_contains
        ]))
        t.record(tool_name="http_api", args={"url": "http://localhost:3000"}, ok=True, evidence="HTTP 200")
        self.assertEqual(t.items[0]["status"], "confirmed")     # page opened
        self.assertEqual(t.items[1]["status"], "unconfirmed")   # no DOM text evidence


class CleanupIntentTest(unittest.TestCase):
    def test_ssh_exists_absent_confirms_cleanup(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "временный файл `C:\\lab\\tmp.txt` удалён после cleanup",
        ]))
        # ssh_exists on a path that is GONE → ok=False → confirms file_not_exists.
        t.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                 ok=False, evidence="C:\\lab\\tmp.txt: не найден")
        self.assertEqual(t.items[0]["status"], "confirmed")

    def test_ssh_exists_present_fails_cleanup(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "временный файл `C:\\lab\\tmp.txt` удалён после cleanup",
        ]))
        t.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                 ok=True, evidence="C:\\lab\\tmp.txt: существует (файл)")
        self.assertEqual(t.items[0]["status"], "failed")        # still there → cleanup failed


if __name__ == "__main__":
    unittest.main()
