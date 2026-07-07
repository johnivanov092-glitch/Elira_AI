"""TaskSpec layer (runtime plan Phase 6): heuristic derivation + verifier gate.

DONE is decided by a verifier passing, not by the model asserting "готово". These
tests pin: a structured task yields goal/criteria/verifiers; a simple task yields
None (no spec, no injected tokens, canaries safe); and the loop nudges once to
confirm criteria with a verifier before it lets the run close.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS  # noqa: E402
from app.application.code_agent.taskspec import (  # noqa: E402
    CriteriaTracker,
    TaskSpec,
    derive_task_spec,
    taskspec_context,
)
from app.application.agent_kernel import deferred_tools  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402


_STRUCTURED = """Создай тестовый стенд agent-lab на Windows Server.

Цель:
Проверить, что агент умеет безопасно создавать, запускать, проверять и удалять сервис.

Ограничения:
- работать только в C:\\AgentLab
- не трогать системные файлы

Критерии готовности:
1. Сервис слушает порт 18080
2. test.ps1 проходит 8/8
3. agent-lab.ps1 не содержит Content-Length
"""


class DeriveTest(unittest.TestCase):
    def test_structured_task_yields_goal_criteria_verifiers(self) -> None:
        spec = derive_task_spec(_STRUCTURED)
        self.assertIsNotNone(spec)
        self.assertIn("агент", spec.goal.lower())
        self.assertEqual(len(spec.success_criteria), 3)
        self.assertTrue(any("18080" in c for c in spec.success_criteria))
        # a port and a test script became concrete verifiers…
        joined = " ".join(spec.verifiers)
        self.assertIn("18080", joined)
        self.assertIn("test.ps1", joined)
        # …but the script UNDER test is NOT mistaken for a verifier.
        self.assertNotIn("agent-lab.ps1", joined)
        self.assertEqual(len(spec.constraints), 2)

    def test_phrased_verification_header_yields_criteria(self) -> None:
        # A real-task header "После запуска проверить:" (not the bare "Критерии
        # готовности:") must still yield criteria — else a restored spec is empty.
        task = ("Цель: тестовый стенд.\n"
                "После запуска проверить:\n"
                "- что процесс работает\n"
                "- что порт 18080 слушается\n"
                "- что /health отвечает")
        spec = derive_task_spec(task)
        self.assertIsNotNone(spec)
        self.assertGreaterEqual(len(spec.success_criteria), 3)
        self.assertTrue(any("порт" in c or "18080" in c for c in spec.success_criteria))

    def test_podvoh_section_is_constraints_not_success_criteria(self) -> None:
        task = r"""Цель:
Через SSH на `home-srv01` создай безопасный тестовый стенд в `C:\AgentLabCanary2`.

Критерии готовности:
- директория `C:\AgentLabCanary2` существует
- файл `C:\AgentLabCanary2\health.txt` существует
- файл `C:\AgentLabCanary2\health.txt` содержит строку `status=ok`
- файл `C:\AgentLabCanary2\health.txt` НЕ содержит строку `status=fail`
- файл `C:\AgentLabCanary2\other.txt` существует
- файл `C:\AgentLabCanary2\other.txt` содержит строку `other=ok`

Подвох:
- `health.txt` и `other.txt` должны проверяться отдельно.
- `ssh_assert_not_contains` по `health.txt` НЕ должен подтверждать критерии для `other.txt`.
- `ssh_exists` директории НЕ должен подтверждать существование файлов.
- Нельзя писать в финале `COMPLETED`, если хотя бы один criterion не подтверждён verifier'ом.
"""
        spec = derive_task_spec(task)
        self.assertIsNotNone(spec)
        self.assertEqual(len(spec.success_criteria), 6)
        self.assertFalse(any("Подвох" in c or "ssh_exists директории" in c for c in spec.success_criteria))
        self.assertTrue(any("health.txt" in c and "other.txt" in c for c in spec.constraints))

    def test_is_continuation_message(self) -> None:
        from app.application.code_agent.taskspec import is_continuation_message
        for m in ("делай", "продолжай", "продолжи работу", "да", "ок", "поехали",
                  "Длинный анализ... я бы начал с Windows Service. делай"):
            self.assertTrue(is_continuation_message(m), m)
        for m in ("привет", "объясни как работает docker", "что такое рекурсия",
                  "напиши функцию сортировки", "давай сделаем большой калькулятор проекта",
                  "сделай другой файл test2.py", "сделать start.ps1", "переделай агента", ""):
            self.assertFalse(is_continuation_message(m), m)  # "сделай" != "делай" (word-level)

    def test_simple_task_yields_none(self) -> None:
        self.assertIsNone(derive_task_spec("почини баг в parser.py"))
        self.assertIsNone(derive_task_spec("привет, как дела?"))
        self.assertIsNone(derive_task_spec(""))
        self.assertIsNone(derive_task_spec("просканируй сеть"))  # canary-shaped

    def test_context_block_lists_criteria_and_verifiers(self) -> None:
        spec = derive_task_spec(_STRUCTURED)
        block = taskspec_context(spec)
        self.assertIn("Критерии готовности", block)
        self.assertIn("18080", block)
        self.assertIn("verifier", block.lower())

    def test_coding_verifiers_from_task_text(self) -> None:
        task = ("Цель: почини типизацию фронта.\n"
                "Критерии готовности:\n"
                "- npm run typecheck проходит без ошибок\n"
                "- pytest tests/test_x.py зелёный")
        spec = derive_task_spec(task)
        self.assertIsNotNone(spec)
        j = " ".join(spec.verifiers).lower()
        self.assertIn("typecheck", j)
        self.assertIn("pytest", j)

    def test_project_verifiers_enrich_but_never_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".elira").mkdir()
            (root / ".elira" / "verify").write_text("echo ok", encoding="utf-8")
            (root / "package.json").write_text(
                '{"scripts": {"typecheck": "tsc --noEmit", "build": "vite build"}}', encoding="utf-8")
            spec = derive_task_spec("Цель: X.\nКритерии готовности:\n- сервис работает", project_root=root)
            self.assertIsNotNone(spec)
            j = " ".join(spec.verifiers)
            self.assertIn(".elira/verify", j)
            self.assertIn("typecheck", j)
            # a SIMPLE task in the same project must still be None — project doesn't trigger
            self.assertIsNone(derive_task_spec("почини баг", project_root=root))


# ── loop-level: the verifier gate ───────────────────────────────

_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]


@contextlib.contextmanager
def _loop_env():
    with patch.object(agent_loop, "_resolve_code_route", return_value=("test-model", 32768, None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


class _SeqChat:
    def __init__(self, responses, fallback):
        self._r, self._fb, self._i = responses, fallback, 0

    def __call__(self, **kw):
        r = self._r[self._i] if self._i < len(self._r) else self._fb
        self._i += 1
        return r


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


class VerifierGateTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("ts-gate", "ts-confirmed"):
            deferred_tools.clear_run(rid)

    def test_unconfirmed_criteria_finalize_without_extra_llm_turn(self):
        # Model edits a file then tries to close WITHOUT any verifier. Runtime must
        # not burn another LLM turn for a reminder; it finalizes and marks the
        # criteria as unconfirmed deterministically.
        chat = _SeqChat([_call("write_file", path="a.ps1", content="x"), _final("сделал")], _final("готово"))
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(status="ok", output={"text": "ok", "ok": True, "touched_path": "a.ps1"})):
            evs = list(agent_loop.stream_code_agent(
                user_message=_STRUCTURED, project_root=tmp, run_id="ts-gate",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")
        self.assertTrue(done.get("ok"))                      # runtime health = OK …
        self.assertEqual(done.get("completion_status"), "unverified")  # … but task NOT verified
        self.assertTrue(done.get("partial"))
        self.assertFalse(done.get("criteria_confirmed"))
        self.assertEqual(len([e for e in evs if e.get("type") == "step_started"]), 2)  # no extra turn
        # The chat text carries only a short pointer to the readiness panel — the
        # full per-criterion breakdown lives in the structured `criteria` (done event),
        # not duplicated into the message.
        final = [e for e in evs if e.get("type") == "final_response"][-1]
        self.assertIn("Готовность задачи: unverified", final["text"])
        self.assertIn("детали в панели проверки", final["text"])
        self.assertNotIn("НЕ ПРОЙДЕН", final["text"])          # no bullet dump in chat
        # criteria still fully available in the done event for the panel / audit
        self.assertEqual(len(done.get("criteria") or []), 3)

    def test_passing_verifier_confirms_its_matching_criterion(self):
        # A passing ssh_assert_not_contains(Content-Length) confirms ONLY the
        # matching criterion (per-criterion) → completion_status = partial, one
        # criterion confirmed with evidence, the other two still unconfirmed.
        chat = _SeqChat([
            _call("write_file", path="agent-lab.ps1", content="x"),
            _call("ssh_assert_not_contains", host="home-srv01", path="C:\\agent-lab.ps1", pattern="Content-Length"),
            _final("готово, проверено"),
        ], _final())

        def _exec(request, **kw):
            tool = getattr(request, "tool_name", "")
            if "assert" in str(tool):
                return SimpleNamespace(status="ok", output={
                    "text": "OK", "ok": True, "verifier": True,
                    "evidence": "«Content-Length» НЕ НАЙДЕНО в C:\\agent-lab.ps1", "touched_host": "home-srv01"})
            return SimpleNamespace(status="ok", output={"text": "ok", "ok": True, "touched_path": "agent-lab.ps1"})

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            evs = list(agent_loop.stream_code_agent(
                user_message=_STRUCTURED, project_root=tmp, run_id="ts-confirmed",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")
        self.assertEqual(done.get("completion_status"), "partial")   # 1 of 3 proven
        self.assertFalse(done.get("criteria_confirmed"))             # NOT all confirmed
        confirmed = [c for c in done["criteria"] if c["status"] == "confirmed"]
        self.assertEqual(len(confirmed), 1)
        self.assertIn("Content-Length", confirmed[0]["text"])
        self.assertIsNotNone(confirmed[0]["evidence"])


# ── per-criterion tracker (Ph7.4/7.5) ───────────────────────────


class CriteriaTrackerTest(unittest.TestCase):
    def _spec(self):
        return TaskSpec(success_criteria=[
            "Сервис слушает порт 18080",
            "agent-lab.ps1 не содержит Content-Length",
            "pytest tests/test_x.py проходит",
        ])

    # ── canary-shaped criteria (the live over-match bug) ────────────
    _CANARY = "C:\\AgentLabCanary"
    _HEALTH = "C:\\AgentLabCanary\\health.txt"

    def _canary_tracker(self):
        from app.application.code_agent.taskspec import CriteriaTracker
        return CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "папка C:\\AgentLabCanary существует",     # exists (directory)
            "health.txt существует",                    # exists (file)
            "health.txt содержит status=ok",            # contains
            "health.txt не содержит status=fail",       # not_contains
        ]))

    def _status(self, t, needle):
        return next(it["status"] for it in t.items if needle in it["text"])

    def test_status_transitions_and_completion(self):
        t = CriteriaTracker.from_spec(self._spec())
        self.assertEqual(t.completion_status(), "unverified")
        t.record(tool_name="ssh_port_check", args={"host": "h", "port": 18080},
                 ok=True, evidence="18080 LISTENING")
        self.assertEqual(t.completion_status(), "partial")  # 1/3
        t.record(tool_name="ssh_assert_not_contains",
                 args={"host": "h", "path": "C:\\AgentLab\\agent-lab.ps1", "pattern": "Content-Length"},
                 ok=True, evidence="")
        self.assertEqual(t.completion_status(), "partial")  # 2/3
        t.record(tool_name="run_bash", args={"command": "pytest tests/test_x.py"},
                 ok=False, evidence="")
        self.assertEqual(t.completion_status(), "failed")   # a verifier ran red

    def test_all_confirmed(self):
        t = CriteriaTracker.from_spec(self._spec())
        t.record(tool_name="ssh_port_check", args={"host": "h", "port": 18080}, ok=True, evidence="")
        t.record(tool_name="ssh_assert_not_contains",
                 args={"host": "h", "path": "C:\\AgentLab\\agent-lab.ps1", "pattern": "Content-Length"},
                 ok=True, evidence="")
        t.record(tool_name="run_bash", args={"command": "pytest tests/test_x.py"}, ok=True, evidence="")
        self.assertEqual(t.completion_status(), "confirmed")

    def test_port_mismatch_never_confirms(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["порт 18080 слушает"]))
        t.record(tool_name="ssh_port_check", args={"host": "h", "port": 9999}, ok=True, evidence="")
        self.assertEqual(t.items[0]["status"], "unconfirmed")  # 9999 != 18080

    # ── FIX-9: intent+target matching (the live over-match) ─────────

    def test_not_contains_confirms_only_matching_not_contains(self):
        # ssh_assert_not_contains(health.txt, status=fail) confirms ONLY the
        # not_contains criterion — not exists, not contains (the live bug).
        t = self._canary_tracker()
        t.record(tool_name="ssh_assert_not_contains",
                 args={"host": "h", "path": self._HEALTH, "pattern": "status=fail"},
                 ok=True, evidence="«status=fail» НЕ НАЙДЕНО")
        self.assertEqual(self._status(t, "не содержит status=fail"), "confirmed")

    def test_not_contains_does_not_confirm_exists(self):
        t = self._canary_tracker()
        t.record(tool_name="ssh_assert_not_contains",
                 args={"host": "h", "path": self._HEALTH, "pattern": "status=fail"},
                 ok=True, evidence="")
        self.assertEqual(self._status(t, "health.txt существует"), "unconfirmed")
        self.assertEqual(self._status(t, "AgentLabCanary существует"), "unconfirmed")

    def test_not_contains_does_not_confirm_contains(self):
        t = self._canary_tracker()
        t.record(tool_name="ssh_assert_not_contains",
                 args={"host": "h", "path": self._HEALTH, "pattern": "status=fail"},
                 ok=True, evidence="")
        self.assertEqual(self._status(t, "содержит status=ok"), "unconfirmed")

    def test_exists_dir_confirms_directory_exists(self):
        # ssh_exists on the directory confirms the directory criterion, NOT the
        # file-exists criterion (different salient target).
        t = self._canary_tracker()
        t.record(tool_name="ssh_exists", args={"host": "h", "path": self._CANARY},
                 ok=True, evidence="существует (директория)")
        self.assertEqual(self._status(t, "AgentLabCanary существует"), "confirmed")
        self.assertEqual(self._status(t, "health.txt существует"), "unconfirmed")

    def test_exists_file_confirms_file_exists(self):
        t = self._canary_tracker()
        t.record(tool_name="ssh_exists", args={"host": "h", "path": self._HEALTH},
                 ok=True, evidence="существует (файл)")
        self.assertEqual(self._status(t, "health.txt существует"), "confirmed")
        self.assertEqual(self._status(t, "AgentLabCanary существует"), "unconfirmed")

    def test_live_canary_markdown_paths_confirm_all_real_criteria(self):
        # Regression from live run 1cabd332...: Markdown backticks were captured
        # into the path token (`health.txt`), so verifier paths never matched.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "директория `C:\\AgentLabCanary` существует",
            "файл `C:\\AgentLabCanary\\health.txt` существует",
            "файл `C:\\AgentLabCanary\\health.txt` содержит строку `status=ok`",
            "verifier подтверждает, что `C:\\AgentLabCanary\\health.txt` НЕ содержит строку `status=fail`",
        ]))
        t.record(tool_name="ssh_exists", args={"host": "h", "path": self._CANARY},
                 ok=True, evidence="существует (директория)")
        t.record(tool_name="ssh_exists", args={"host": "h", "path": self._HEALTH},
                 ok=True, evidence="существует (файл)")
        t.record(tool_name="ssh_assert_contains",
                 args={"host": "h", "path": self._HEALTH, "pattern": "status=ok"},
                 ok=True, evidence="«status=ok» НАЙДЕНО")
        t.record(tool_name="ssh_assert_not_contains",
                 args={"host": "h", "path": self._HEALTH, "pattern": "status=fail"},
                 ok=True, evidence="«status=fail» НЕ НАЙДЕНО")
        # A later cleanup check must not undo already-confirmed creation criteria.
        t.record(tool_name="ssh_exists", args={"host": "h", "path": self._CANARY},
                 ok=False, evidence="не найден")
        self.assertEqual(t.completion_status(), "confirmed")

    def test_report_requirement_excluded_from_criteria(self):
        # A report-only section (and inline "cleanup описана в отчёте") must NOT
        # become a verifier criterion — only the real port criterion survives.
        task = ("Цель: тестовый стенд.\n"
                "Критерии готовности:\n"
                "- порт 18080 слушает\n"
                "- команда cleanup описана в финальном отчёте\n"
                "Финальный отчёт:\n"
                "- какие команды очистки (cleanup) были выполнены\n"
                "- какие файлы созданы")
        spec = derive_task_spec(task)
        self.assertIsNotNone(spec)
        joined = " ".join(spec.success_criteria).lower()
        self.assertIn("18080", joined)
        self.assertNotIn("cleanup", joined)
        self.assertNotIn("какие", joined)
        self.assertNotIn("созданы", joined)
        self.assertEqual(len(spec.success_criteria), 1)

    def test_no_criteria_is_none(self):
        self.assertEqual(CriteriaTracker.from_spec(None).completion_status(), "none")


# ── coding/frontend verification (VaultDesk live run b51697e7) ───
#
# The run made 51 tool calls, really ran typecheck/build/http/browser, yet every
# criterion stayed unconfirmed because coding/frontend evidence wasn't mapped to
# CriteriaTracker. These pin: real checks confirm their OWN criteria; a bundle grep
# never confirms visibility; text is confirmed only by rendered DOM; and a run
# without DOM evidence stays honestly partial (not COMPLETED).


class VaultDeskVerificationTest(unittest.TestCase):
    _VAULT = (
        "Цель:\n"
        "Создай landing page для `VaultDesk`.\n\n"
        "Критерии готовности:\n"
        "- первая страница открывается без ошибок\n"
        "- на первом экране явно видно название `VaultDesk`\n"
        "- есть секция с 3 преимуществами: `Inventory`, `Backups`, `Alerts`\n"
        "- есть CTA-кнопка `Start local audit`\n"
        "- проект проходит доступные проверки сборки/typecheck\n\n"
        "Подвох:\n"
        "- не делай маркетинговую пустышку без реальной структуры\n"
        "- если проверки не запускаются, не пиши `готово`\n"
    )
    _DOM = "TITLE: VaultDesk\nvaultdesk — local dashboard. inventory backups alerts. start local audit."

    def _st(self, t, needle):
        return next(it["status"] for it in t.items if needle.lower() in it["text"].lower())

    def _vault(self):
        return CriteriaTracker.from_spec(derive_task_spec(self._VAULT))

    def test_podvokh_not_in_success_criteria(self):
        spec = derive_task_spec(self._VAULT)
        joined = " ".join(spec.success_criteria).lower()
        self.assertNotIn("пустышк", joined)          # Подвох item, not a criterion
        self.assertNotIn("не пиши", joined)
        self.assertTrue(any("пустышк" in c.lower() for c in spec.constraints))
        self.assertEqual(len(spec.success_criteria), 5)

    def test_typecheck_and_build_confirm_only_their_criteria(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "npm run typecheck проходит без ошибок",
            "проект успешно собирается (build)",
            "pytest tests/test_x.py проходит",
        ]))
        t.record(tool_name="run_bash", args={"command": "npm run typecheck"}, ok=True, evidence="exit 0")
        self.assertEqual(self._st(t, "typecheck"), "confirmed")
        self.assertEqual(self._st(t, "собирается"), "unconfirmed")   # build not run yet
        self.assertEqual(self._st(t, "pytest"), "unconfirmed")       # test not run
        t.record(tool_name="run_bash", args={"command": "npm run build"}, ok=True, evidence="exit 0")
        self.assertEqual(self._st(t, "собирается"), "confirmed")
        self.assertEqual(self._st(t, "pytest"), "unconfirmed")       # still only its own verdict confirms

    def test_http_200_confirms_page_open_only(self):
        t = self._vault()
        t.record(tool_name="http_api", args={"url": "http://localhost:3000"}, ok=True, evidence="HTTP 200")
        self.assertEqual(self._st(t, "открывается"), "confirmed")
        self.assertEqual(self._st(t, "VaultDesk"), "unconfirmed")    # page-open ≠ text visible
        self.assertEqual(self._st(t, "typecheck"), "unconfirmed")

    def test_browser_dom_confirms_only_matching_text(self):
        t = self._vault()
        t.record(tool_name="browser", args={"url": "http://localhost:3000"}, ok=True, evidence=self._DOM)
        self.assertEqual(self._st(t, "VaultDesk"), "confirmed")
        self.assertEqual(self._st(t, "преимуществами"), "confirmed")   # inventory/backups/alerts all present
        self.assertEqual(self._st(t, "Start local audit"), "confirmed")
        self.assertEqual(self._st(t, "открывается"), "confirmed")       # a render proves the page loaded
        self.assertEqual(self._st(t, "typecheck"), "unconfirmed")       # browser is not a build check

    def test_browser_missing_token_does_not_confirm(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["на экране видно `Nonexistent`"]))
        t.record(tool_name="browser", args={"url": "http://x"}, ok=True, evidence=self._DOM)
        self.assertEqual(t.items[0]["status"], "unconfirmed")          # token not in rendered DOM

    def test_wrong_server_dom_does_not_confirm_vaultdesk_text(self):
        # Live d1511484: the requested port was taken by Elira's own dev server, so a
        # render 'worked' but returned Elira's DOM. Token matching keeps the VaultDesk
        # text criteria unconfirmed even against a live-but-wrong server.
        t = self._vault()
        elira_dom = "TITLE: Elira AI\nЭлира Новый чат ДИАЛОГИ Настройки"
        t.record(tool_name="browser", args={"url": "http://localhost:5173/"}, ok=True, evidence=elira_dom)
        self.assertEqual(self._st(t, "VaultDesk"), "unconfirmed")
        self.assertEqual(self._st(t, "Start local audit"), "unconfirmed")

    def test_bundle_findstr_does_not_confirm_visibility(self):
        # findstr finds the tokens in the built bundle — that is NOT "visible on the
        # first screen". A bundle grep must confirm neither text nor page-open.
        t = self._vault()
        t.record(tool_name="run_bash",
                 args={"command": 'findstr /C:"VaultDesk" /C:"Start local audit" dist\\assets\\index.js'},
                 ok=True, evidence="VaultDesk ... Start local audit ... Inventory Backups Alerts")
        self.assertEqual(self._st(t, "VaultDesk"), "unconfirmed")
        self.assertEqual(self._st(t, "Start local audit"), "unconfirmed")
        self.assertEqual(self._st(t, "открывается"), "unconfirmed")

    def test_full_run_with_real_evidence_reaches_confirmed(self):
        spec = TaskSpec(success_criteria=[
            "первая страница открывается без ошибок",
            "на первом экране видно `VaultDesk`",
            "проект проходит доступные проверки сборки/typecheck",
        ])
        t = CriteriaTracker.from_spec(spec)
        t.record(tool_name="run_bash", args={"command": "npm run typecheck"}, ok=True, evidence="exit 0")
        t.record(tool_name="http_api", args={"url": "http://localhost:3000"}, ok=True, evidence="HTTP 200")
        t.record(tool_name="browser", args={"url": "http://localhost:3000"}, ok=True, evidence=self._DOM)
        self.assertEqual(t.completion_status(), "confirmed")

    def test_run_without_dom_evidence_stays_partial_not_completed(self):
        # Same spec, but the text criterion never gets DOM evidence (browser blocked
        # / not run). It must stay unconfirmed → partial, NOT confirmed/"COMPLETED".
        spec = TaskSpec(success_criteria=[
            "первая страница открывается без ошибок",
            "на первом экране видно `VaultDesk`",
            "проект проходит доступные проверки сборки/typecheck",
        ])
        t = CriteriaTracker.from_spec(spec)
        t.record(tool_name="run_bash", args={"command": "npm run typecheck"}, ok=True, evidence="exit 0")
        t.record(tool_name="http_api", args={"url": "http://localhost:3000"}, ok=True, evidence="HTTP 200")
        self.assertEqual(t.completion_status(), "partial")
        self.assertEqual(self._st(t, "VaultDesk"), "unconfirmed")

    def test_full_vaultdesk_spec_stays_partial_when_criteria_are_unverifiable(self):
        # The REAL 9-criterion spec includes hero/adaptive/no-break criteria that no
        # deterministic verifier can prove — so even a fully-checked run is honestly
        # `partial`, never a false `confirmed`.
        from app.application.code_agent.taskspec import _criterion_intent
        real = derive_task_spec(
            "Цель: landing.\nКритерии готовности:\n"
            "- первая страница открывается без ошибок\n"
            "- есть hero-секция с коротким описанием\n"
            "- страница адаптивна для desktop и mobile\n"
            "- проект проходит доступные проверки сборки/typecheck"
        )
        # hero (no quoted token) and adaptive (viewport_layout, no viewport evidence)
        # have no verifier verdict → they never confirm
        self.assertEqual(_criterion_intent("страница адаптивна для desktop и mobile"), "viewport_layout")
        t = CriteriaTracker.from_spec(real)
        t.record(tool_name="run_bash", args={"command": "npm run build"}, ok=True, evidence="exit 0")
        t.record(tool_name="http_api", args={"url": "http://localhost:3000"}, ok=True, evidence="HTTP 200")
        t.record(tool_name="browser", args={"url": "http://localhost:3000"}, ok=True, evidence="hero desc")
        self.assertEqual(t.completion_status(), "partial")


class CodingLoopTest(unittest.TestCase):
    def tearDown(self):
        deferred_tools.clear_run("coding-loop")

    def test_edit_fail_edit_pass_confirms_via_green_test(self):
        task = ("Цель: почини сломанный тест.\n"
                "Критерии готовности:\n- pytest tests/test_x.py проходит")
        chat = _SeqChat([
            _call("write_file", path="x.py", content="a"),
            _call("run_bash", command="pytest tests/test_x.py"),   # fail
            _call("write_file", path="x.py", content="b"),
            _call("run_bash", command="pytest tests/test_x.py"),   # pass
            _final("починил"),
        ], _final())
        state = {"pytest": 0}

        def _exec(request, **kw):
            tool = getattr(request, "tool_name", "")
            args = getattr(request, "args", {})
            if tool == "run_bash":
                state["pytest"] += 1
                ec = 0 if state["pytest"] >= 2 else 1
                return SimpleNamespace(status="ok", output={"text": f"exit {ec}", "ok": ec == 0, "exit_code": ec})
            return SimpleNamespace(status="ok", output={"text": "ok", "ok": True, "touched_path": args.get("path", "x.py")})

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            evs = list(agent_loop.stream_code_agent(
                user_message=task, project_root=tmp, run_id="coding-loop",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")          # runtime fine
        self.assertEqual(done.get("completion_status"), "confirmed")  # green test proved it
        self.assertTrue(done.get("criteria_confirmed"))


# ── completion contract (FIX-1/2/3/8) ───────────────────────────


def _exec_ok(**out):
    def _f(request, **kw):
        return SimpleNamespace(status="ok", output=out or {"text": "ok", "ok": True, "touched_path": "a.ps1"})
    return _f


class CompletionContractTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("cc-done", "cc-cont", "cc-part", "cc-conf", "cc-fix9",
                    "cc-nr0", "cc-nr1", "cc-nr2"):
            deferred_tools.clear_run(rid)

    def _stream(self, user_message, chat, rid, history=None, exec_side=None):
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          side_effect=exec_side or _exec_ok(text="ok", ok=True, touched_path="a.ps1")):
            return list(agent_loop.stream_code_agent(
                user_message=user_message, project_root=tmp, run_id=rid,
                conversation_history=history or [], auto_remember=False,
                permission_mode="bypass", max_steps=20, chat_fn=chat))

    def test_done_carries_full_task_state(self):
        chat = _SeqChat([_call("write_file", path="a.ps1", content="x"), _final("сделал")], _final())
        done = [e for e in self._stream(_STRUCTURED, chat, "cc-done") if e.get("type") == "done"][-1]
        for k in ("completion_status", "criteria", "criteria_confirmed", "partial", "task_spec", "task_spec_source"):
            self.assertIn(k, done, f"done missing {k}")
        self.assertTrue(done["ok"])                          # runtime health
        self.assertEqual(done["completion_status"], "unverified")  # task NOT solved
        self.assertTrue(done["partial"])
        self.assertFalse(done["criteria_confirmed"])
        self.assertEqual(done["task_spec_source"], "current_message")

    def test_sync_run_code_agent_carries_task_state(self):
        chat = _SeqChat([_call("write_file", path="a.ps1", content="x"), _final("сделал")], _final())
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec_ok(text="ok", ok=True, touched_path="a.ps1")):
            res = agent_loop.run_code_agent(user_message=_STRUCTURED, project_root=tmp,
                                            run_id="cc-sync", auto_remember=False, max_steps=20, chat_fn=chat)
        self.assertEqual(res["completion_status"], "unverified")
        self.assertTrue(res["partial"])
        self.assertTrue(res["criteria"])
        self.assertIsNotNone(res["task_spec"])

    def test_new_question_after_task_does_not_restore_taskspec(self):
        # FIX-8 restore is gated on a continuation signal — a NEW question after a
        # structured task must NOT drag the old task's criteria back.
        history = [{"role": "user", "content": _STRUCTURED}, {"role": "assistant", "content": "сделал"}]
        # incl. the "new sub-task" edge case: "сделай другой файл test2.py" must NOT
        # be read as a continuation of the old structured task.
        for i, msg in enumerate(("привет", "объясни как работает docker",
                                 "сделай другой файл test2.py")):
            chat = _SeqChat([_final("ответ")], _final())
            done = [e for e in self._stream(msg, chat, f"cc-nr{i}", history=history) if e.get("type") == "done"][-1]
            self.assertEqual(done["task_spec_source"], "none", f"falsely restored on {msg!r}")
            self.assertIsNone(done["task_spec"])
            self.assertEqual(done["completion_status"], "none")

    def test_continuation_restores_taskspec_from_history(self):
        history = [{"role": "user", "content": _STRUCTURED}, {"role": "assistant", "content": "частично"}]
        chat = _SeqChat([_call("write_file", path="a.ps1", content="x"), _final("продолжил")], _final())
        done = [e for e in self._stream("делай", chat, "cc-cont", history=history) if e.get("type") == "done"][-1]
        self.assertEqual(done["task_spec_source"], "conversation_history")  # restored, gate NOT off
        self.assertIsNotNone(done["task_spec"])
        self.assertEqual(done["completion_status"], "unverified")           # no verifier ran
        self.assertTrue(done["partial"])

    def test_continuation_partial_via_verifiers(self):
        history = [{"role": "user", "content": _STRUCTURED}]
        chat = _SeqChat([
            _call("write_file", path="a.ps1", content="x"),
            _call("ssh_port_check", host="home-srv01", port=18080),
            _call("ssh_assert_not_contains", host="home-srv01", path="C:\\agent-lab.ps1", pattern="Content-Length"),
            _final("готово"),
        ], _final())

        def _exec(request, **kw):
            t = getattr(request, "tool_name", "")
            if t == "ssh_port_check":
                return SimpleNamespace(status="ok", output={"text": "LISTENING", "ok": True, "verifier": True,
                                                            "evidence": "порт 18080 LISTENING pid=5", "touched_host": "home-srv01"})
            if "assert" in t:
                return SimpleNamespace(status="ok", output={"text": "OK", "ok": True, "verifier": True,
                                                            "evidence": "«Content-Length» НЕ НАЙДЕНО", "touched_host": "home-srv01"})
            return SimpleNamespace(status="ok", output={"text": "ok", "ok": True, "touched_path": "a.ps1"})

        done = [e for e in self._stream("делай", chat, "cc-part", history=history, exec_side=_exec) if e.get("type") == "done"][-1]
        # port + Content-Length confirmed, test.ps1 NOT → partial (2 of 3), NOT solved
        self.assertEqual(done["completion_status"], "partial")
        self.assertGreaterEqual(sum(1 for c in done["criteria"] if c["status"] == "confirmed"), 2)

    def test_fix9_writing_test_script_does_not_confirm_its_criterion(self):
        # FIX-9 slice: writing test.ps1 (a file edit) is NOT running it — the
        # "test.ps1 проходит" criterion stays unconfirmed until a real verifier.
        task = "Цель: стенд.\nКритерии готовности:\n- test.ps1 проходит\n- порт 18080 слушает"
        chat = _SeqChat([
            _call("ssh_write", host="h", path="test.ps1", content="..."),
            _call("ssh_port_check", host="h", port=18080),
            _final("готово"),
        ], _final())

        def _exec(request, **kw):
            t = getattr(request, "tool_name", "")
            if t == "ssh_port_check":
                return SimpleNamespace(status="ok", output={"text": "LISTENING", "ok": True, "verifier": True,
                                                            "evidence": "порт 18080 LISTENING", "touched_host": "h"})
            return SimpleNamespace(status="ok", output={"text": "Wrote", "ok": True, "touched_path": "test.ps1", "touched_host": "h"})

        done = [e for e in self._stream(task, chat, "cc-fix9", exec_side=_exec) if e.get("type") == "done"][-1]
        crit = {c["text"]: c["status"] for c in done["criteria"]}
        self.assertEqual(done["completion_status"], "partial")
        self.assertEqual([s for t, s in crit.items() if "test.ps1" in t], ["unconfirmed"])


class JournalCompletionTest(unittest.TestCase):
    def _status(self, **done_extra):
        from app.application.code_agent.run_journal import RunJournal
        j = RunJournal(f"jtest-{done_extra.get('completion_status', 'x')}")
        j.append_event({"type": "done", "ok": True, "stop_reason": "answer", "steps": 1, **done_extra})
        return j.state.get("status")

    def test_journal_completed_only_on_confirmed(self):
        self.assertEqual(self._status(completion_status="confirmed", criteria=[]), "completed")
        self.assertEqual(self._status(completion_status="none", criteria=[]), "completed")  # no-spec answer
        self.assertNotEqual(self._status(completion_status="failed", criteria=[]), "completed")
        self.assertNotEqual(self._status(completion_status="unverified", criteria=[]), "completed")
        self.assertNotEqual(self._status(completion_status="partial", criteria=[]), "completed")


if __name__ == "__main__":
    unittest.main()
