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
from app.application.code_agent.loop_helpers import gate_completion_claims  # noqa: E402


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

    def test_passive_exists_present_does_not_fail_cleanup(self):
        # A PASSIVE ssh_exists that sees the file (e.g. a pre-cleanup check) must not
        # FAIL a "removed" criterion — it stays unconfirmed (lifecycle-safe).
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "временный файл `C:\\lab\\tmp.txt` удалён после cleanup",
        ]))
        t.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                 ok=True, evidence="C:\\lab\\tmp.txt: существует (файл)")
        self.assertEqual(t.items[0]["status"], "unconfirmed")

    def test_explicit_not_exists_still_present_fails_cleanup(self):
        # An EXPLICIT ssh_not_exists assertion that finds the file still there IS a
        # real cleanup failure.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "временный файл `C:\\lab\\tmp.txt` удалён после cleanup",
        ]))
        t.record(tool_name="ssh_not_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                 ok=False, evidence="C:\\lab\\tmp.txt: всё ещё существует (файл)")
        self.assertEqual(t.items[0]["status"], "failed")

    def test_not_exists_absent_confirms_cleanup(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "временный файл `C:\\lab\\tmp.txt` удалён после cleanup",
        ]))
        t.record(tool_name="ssh_not_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                 ok=True, evidence="C:\\lab\\tmp.txt: отсутствует — cleanup ок")
        self.assertEqual(t.items[0]["status"], "confirmed")

    def test_post_cleanup_absence_does_not_fail_setup_exists(self):
        # FIX #2: setup-exists confirmed during setup, then a post-cleanup absence
        # check must NOT flip it to failed.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "файл `C:\\lab\\tmp.txt` существует",           # setup criterion
        ]))
        t.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                 ok=True, evidence="существует")            # setup: confirmed
        self.assertEqual(t.items[0]["status"], "confirmed")
        t.record(tool_name="ssh_not_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                 ok=True, evidence="отсутствует")           # post-cleanup: absent
        self.assertEqual(t.items[0]["status"], "confirmed")  # NOT failed
        # and even an unconfirmed setup-exists is only neutral on a later absence
        t2 = CriteriaTracker.from_spec(TaskSpec(success_criteria=["файл `C:\\lab\\tmp.txt` существует"]))
        t2.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"},
                  ok=False, evidence="не найден")
        self.assertEqual(t2.items[0]["status"], "unconfirmed")  # neutral, not failed


class FileExistenceLifecycleTest(unittest.TestCase):
    """A whole setup→verify→cleanup lifecycle confirms without cross-phase false fails."""

    def test_ssh_read_success_confirms_file_exists(self):
        # FIX #3: a successful read proves the file exists (no separate ssh_exists needed).
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["файл `C:\\lab\\health.txt` существует"]))
        ch = t.record(tool_name="ssh_read", args={"host": "h", "path": "C:\\lab\\health.txt"},
                      ok=True, evidence="C:\\lab\\health.txt: прочитан (42 байт) — существует")
        self.assertTrue(ch)
        self.assertEqual(t.items[0]["status"], "confirmed")

    def test_exists_before_cleanup_is_positive_and_reports_confirming_evidence(self):
        # Live f478c61a: "директория … существует ДО cleanup" was misclassified as a
        # cleanup criterion and confirmed with evidence "не найден". The temporal
        # "cleanup" must not flip a POSITIVE existence claim → it's file_exists, and a
        # confirmed criterion reports the CONFIRMING evidence, not a stale precheck one.
        from app.application.code_agent.taskspec import _criterion_intent
        c = "verifier подтверждает, что директория `C:\\AgentLabGlobalCanary` существует ДО cleanup"
        self.assertEqual(_criterion_intent(c), "file_exists")
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[c]))
        # a pre-creation absence check is neutral (never confirms/fails with "не найден")
        t.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\AgentLabGlobalCanary"},
                 ok=False, evidence="C:\\AgentLabGlobalCanary: не найден")
        self.assertEqual(t.items[0]["status"], "unconfirmed")
        self.assertIsNone(t.items[0]["evidence"])
        # after creation, ssh_exists(present) confirms it with the CONFIRMING evidence
        t.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\AgentLabGlobalCanary"},
                 ok=True, evidence="C:\\AgentLabGlobalCanary: существует (директория)")
        self.assertEqual(t.items[0]["status"], "confirmed")
        self.assertIn("существует", t.items[0]["evidence"])
        self.assertNotIn("не найден", t.items[0]["evidence"])
        # a later post-cleanup absence must NOT flip it back or overwrite the evidence
        t.record(tool_name="ssh_not_exists", args={"host": "h", "path": "C:\\AgentLabGlobalCanary"},
                 ok=True, evidence="отсутствует — cleanup ок")
        self.assertEqual(t.items[0]["status"], "confirmed")
        self.assertIn("существует", t.items[0]["evidence"])

    def test_full_setup_verify_cleanup_reaches_confirmed(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "директория `C:\\lab` существует",
            "файл `C:\\lab\\health.txt` существует",
            "файл `C:\\lab\\health.txt` содержит строку `status=ok`",
            "временный файл `C:\\lab\\tmp.txt` удалён после cleanup",
        ]))
        t.record(tool_name="ssh_exists", args={"host": "h", "path": "C:\\lab"}, ok=True, evidence="dir")
        t.record(tool_name="ssh_read", args={"host": "h", "path": "C:\\lab\\health.txt"}, ok=True, evidence="read")
        t.record(tool_name="ssh_assert_contains",
                 args={"host": "h", "path": "C:\\lab\\health.txt", "pattern": "status=ok"}, ok=True, evidence="found")
        t.record(tool_name="ssh_not_exists", args={"host": "h", "path": "C:\\lab\\tmp.txt"}, ok=True, evidence="gone")
        self.assertEqual(t.completion_status(), "confirmed")


class MultilineContentSplitTest(unittest.TestCase):
    """FIX #4: 'file contains lines: A, B, C' → one criterion per line."""

    def test_colon_list_splits_into_per_line_criteria(self):
        spec = derive_task_spec(
            "Цель: наполнить лог.\nКритерии готовности:\n"
            "- файл `C:\\lab\\out.txt` содержит строки: `READY`, `OK`, `DONE`"
        )
        crits = spec.success_criteria
        self.assertEqual(len(crits), 3)
        for tok in ("READY", "OK", "DONE"):
            self.assertTrue(any(f"`{tok}`" in c for c in crits), tok)
        for c in crits:
            self.assertEqual(_criterion_intent(c), "content_contains")
        # each split keeps the file, and is confirmed by its own ssh_assert_contains
        t = CriteriaTracker.from_spec(spec)
        for tok in ("READY", "OK", "DONE"):
            t.record(tool_name="ssh_assert_contains",
                     args={"host": "h", "path": "C:\\lab\\out.txt", "pattern": tok}, ok=True, evidence="found")
        self.assertEqual(t.completion_status(), "confirmed")

    def test_single_path_plus_pattern_criterion_not_split(self):
        # `path` + `pattern` (two quotes, no colon-list) must stay ONE criterion.
        spec = derive_task_spec(
            "Цель: X.\nКритерии готовности:\n"
            "- файл `C:\\lab\\h.txt` содержит строку `status=ok`"
        )
        self.assertEqual(len(spec.success_criteria), 1)


class CompletionClaimGateTest(unittest.TestCase):
    """FIX #1: the model can't make a UNIVERSAL 'everything passed' claim while the
    verifier says otherwise — including phrasings with words between (the live miss
    "все frontend и SSH критерии подтверждены verifier'ом")."""

    UNIVERSAL_CLAIMS = [
        "все frontend и SSH критерии подтверждены verifier'ом",   # exact live phrase
        "Всё сделано! Все критерии выполнены. COMPLETED.",
        "all frontend and SSH criteria are confirmed",
        "Done — all criteria passed. Task completed.",
        "все verifier checks прошли",
        "все проверки пройдены",
        "нет unverified",
        "no failed or unverified",
        "задача полностью выполнена",
    ]
    FACTUAL_SURVIVORS = [
        "критерий build подтверждён verifier'ом",                 # singular fact
        "typecheck и build подтверждены",
        "все стили, включая .snapshot-grid, .snapshot-card",
        "Я прочитал health.txt и создал tmp.txt, запустил typecheck.",
        "snapshot.txt содержит project=frontend-global-live",
    ]

    def test_universal_claims_neutralized_when_not_confirmed(self):
        for s in self.UNIVERSAL_CLAIMS:
            for status in ("partial", "unverified", "failed"):
                out = gate_completion_claims(s, status)
                self.assertNotEqual(out, s, msg=f"[{status}] not scrubbed: {s!r}")

    def test_live_phrase_specifically(self):
        out = gate_completion_claims(
            "**COMPLETED** — все frontend и SSH критерии подтверждены verifier'ом.", "partial")
        self.assertNotIn("COMPLETED", out)
        self.assertNotIn("критерии подтверждены", out.lower())

    def test_factual_statements_survive(self):
        for s in self.FACTUAL_SURVIVORS:
            self.assertEqual(gate_completion_claims(s, "partial"), s, msg=f"over-scrubbed: {s!r}")

    def test_confirmed_leaves_text_intact(self):
        s = "Готово! Все критерии выполнены. COMPLETED."
        self.assertEqual(gate_completion_claims(s, "confirmed"), s)


class LiveGlobalCanaryRegressionTest(unittest.TestCase):
    """Exact live case (run d8cd3092): agent ran `ssh_read snapshot.txt` and claimed
    the 3 content lines verified — but ssh_read proves file_exists, NOT content. The
    runtime must stay partial, and the final text must not claim universal success."""

    def _spec(self):
        return TaskSpec(success_criteria=[
            "директория `C:\\AgentLabGlobalCanary` существует",
            "файл `C:\\AgentLabGlobalCanary\\snapshot.txt` существует",
            "файл `snapshot.txt` содержит строку `project=frontend-global-live`",
            "файл `snapshot.txt` содержит строку `status=ok`",
            "файл `snapshot.txt` содержит строку `dom=verified`",
        ])

    def test_ssh_read_alone_leaves_content_unverified_partial(self):
        t = CriteriaTracker.from_spec(self._spec())
        # a successful read → file_exists only; NO ssh_assert_contains was called.
        t.record(tool_name="ssh_read", args={"host": "home-srv01", "path": "C:\\AgentLabGlobalCanary\\snapshot.txt"},
                 ok=True, evidence="прочитан — существует")
        self.assertEqual(t.completion_status(), "partial")
        statuses = {it["text"]: it["status"] for it in t.items}
        # file exists confirmed; the 3 content lines stay unconfirmed
        self.assertEqual(statuses["файл `C:\\AgentLabGlobalCanary\\snapshot.txt` существует"], "confirmed")
        for line in ("project=frontend-global-live", "status=ok", "dom=verified"):
            self.assertTrue(any(line in txt and st == "unconfirmed"
                                for txt, st in statuses.items()), line)

    def test_mkdir_without_ssh_exists_leaves_dir_unverified(self):
        t = CriteriaTracker.from_spec(self._spec())
        # ssh_run mkdir is not a verifier → the "directory exists" criterion is not
        # confirmed until an ssh_exists/ssh_read verdict lands.
        self.assertEqual(t.items[0]["status"], "unconfirmed")

    def test_final_text_cannot_claim_all_confirmed_on_this_run(self):
        # With completion=partial, the model's universal claim is neutralised.
        out = gate_completion_claims(
            "Готово. Все frontend и SSH критерии подтверждены verifier'ом.", "partial")
        self.assertNotIn("критерии подтверждены", out.lower())
        self.assertNotIn("all criteria", out.lower())


if __name__ == "__main__":
    unittest.main()
