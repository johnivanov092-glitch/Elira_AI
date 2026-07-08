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
    _criterion_intent,
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


_MULTI_SECTION_TASK = (
    "Цель:\nПроверить стенд frontend + SSH.\n\n"
    "Важно:\n- не переписывай Ops Snapshot без необходимости\n\n"
    "Критерии готовности frontend:\n"
    "- `npm run typecheck` проходит без ошибок\n"
    "- rendered DOM содержит текст `Ops Snapshot`\n\n"
    "Критерии готовности SSH/home-srv01:\n"
    "- verifier подтверждает, что директория `C:\\AgentLabGlobalCanary` существует\n"
    "- verifier подтверждает, что `snapshot.txt` содержит строку `status=ok`\n\n"
    "Подвох:\n"
    "- content criteria требуют отдельных verifier checks: `ssh_assert_contains`\n"
    "- `ssh_assert_not_contains` по `snapshot.txt` НЕ подтверждает contains criteria\n"
    "- нельзя писать `COMPLETED`, если хоть один criterion не подтверждён\n\n"
    "Tool economy:\n- не спамить todo_update на каждом шаге\n\n"
    "Ограничения:\n- не трогать backend/API\n"
)


class DeriveTest(unittest.TestCase):
    def test_multi_section_criteria_headers_no_podvokh_leak(self) -> None:
        # Live d9ee69b9 bug: phrased "Критерии готовности frontend/SSH:" headers were
        # unrecognised, and a Подвох bullet "…verifier checks: `ssh_assert_contains`"
        # (colon + last word "checks") flipped the section and dumped anti-rules in as
        # success criteria. Both must be fixed.
        spec = derive_task_spec(_MULTI_SECTION_TASK)
        crit = " ".join(spec.success_criteria).lower()
        # both explicit success sections contributed their real criteria
        self.assertIn("typecheck", crit)
        self.assertIn("ops snapshot", crit)
        self.assertIn("директория", crit)
        self.assertIn("status=ok", crit)
        # NO Подвох anti-rule leaked as a criterion (the exact live symptom)
        for c in spec.success_criteria:
            self.assertNotIn("не подтверждает", c.lower())
            self.assertNotIn("нельзя писать", c.lower())
            self.assertNotEqual(c.strip("` "), "ssh_assert_contains")
        # Подвох / Tool economy / Ограничения all landed in constraints
        cons = " ".join(spec.constraints).lower()
        self.assertIn("не подтверждает", cons)      # Подвох rule
        self.assertIn("todo_update", cons)          # Tool economy
        self.assertIn("backend", cons)              # Ограничения

    def test_closure_actions_never_target_instruction_lines(self) -> None:
        from app.application.code_agent import criterion_closure as cc
        spec = derive_task_spec(_MULTI_SECTION_TASK)
        t = CriteriaTracker.from_spec(spec)
        for why in (a["why"] for a in cc.missing_verifier_actions(t)):
            self.assertNotIn("не подтверждает", why.lower())     # not a Подвох line
            self.assertNotEqual(why.strip("` "), "ssh_assert_contains")

    def test_scope_rules_route_to_constraints_not_criteria(self) -> None:
        # Live f478c61a: a scope rule sitting in a criteria section ("изменения внесены
        # именно в текущий проект, без создания нового Vite/React проекта") hung as an
        # unverifiable criterion. Scope/safety rules belong in constraints.
        task = (
            "Цель: verify-only прогон.\n"
            "Критерии готовности frontend:\n"
            "- изменения внесены именно в текущий проект, без создания нового Vite/React проекта\n"
            "- `npm run typecheck` проходит без ошибок\n"
            "- не трогать backend/API\n"
            "- rendered DOM содержит текст `Ops Snapshot`\n"
        )
        spec = derive_task_spec(task)
        crit = " ".join(spec.success_criteria).lower()
        cons = " ".join(spec.constraints).lower()
        self.assertIn("typecheck", crit)                    # verifiable outcome-fact
        self.assertIn("ops snapshot", crit)
        self.assertNotIn("изменения внесены", crit)         # scope rule NOT a criterion
        self.assertNotIn("не трогать backend", crit)
        self.assertIn("изменения внесены", cons)            # → constraints
        self.assertIn("backend", cons)
        # only the two real outcome criteria remain
        self.assertEqual(len(spec.success_criteria), 2)

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


def _playwright_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


_PLAYWRIGHT = _playwright_ready()


@unittest.skipUnless(_PLAYWRIGHT, "playwright/chromium not installed")
class BrowserInteractionLiveTest(unittest.TestCase):
    """Happy path for the browser interaction verifier — real Chromium fill+click on a
    local calculator page (file://, no server). Proves _resolve_locator (by label / by
    text) + _apply_action drive the DOM, so an interaction criterion verifies for real."""

    _HTML = (
        "<!doctype html><html><body><h1>Subnet Helper</h1>"
        "<label for=c>CIDR</label><input id=c value='192.168.1.0/24'>"
        "<button id=b onclick=\"document.getElementById('o').innerText="
        "document.getElementById('c').value==='192.168.1.0/24'?"
        "'Network: 192.168.1.0 Mask: 255.255.255.0 Hosts: 254':'Invalid CIDR'\">Calculate</button>"
        "<div id=o></div></body></html>"
    )

    def _page_url(self):
        d = Path(tempfile.mkdtemp())
        f = d / "index.html"
        f.write_text(self._HTML, encoding="utf-8")
        return f.as_uri()

    def test_fill_and_click_reveal_result_in_dom(self):
        from app.application.code_agent.tools._web import _browser_render
        url = self._page_url()
        _t, _u, dom0, _a, _v = _browser_render(url, None, 4000, None)
        self.assertNotIn("Network: 192.168.1.0", dom0)          # result hidden until interaction
        _t, _u, dom1, _a, _v = _browser_render(
            url, None, 4000, [{"fill": "CIDR", "value": "192.168.1.0/24"}, {"click": "Calculate"}])
        self.assertIn("Network: 192.168.1.0", dom1)             # fill-by-label + click-by-text worked

    def test_bad_input_interaction_shows_error_only(self):
        from app.application.code_agent.tools._web import _browser_render
        url = self._page_url()
        _t, _u, dom, _a, _v = _browser_render(
            url, None, 4000, [{"fill": "CIDR", "value": "bad-input"}, {"click": "Calculate"}])
        self.assertIn("Invalid CIDR", dom)
        self.assertNotIn("Network: 192.168.1.0", dom)

    _FORM = (
        "<!doctype html><html><body><h1>Backup Form Checker</h1>"
        "<label for=j>Job name</label><input id=j>"
        "<label for=s>Schedule</label><select id=s><option>Weekly</option><option>Daily</option></select>"
        "<label><input type=checkbox id=e> Encryption</label>"
        "<button onclick=\"var j=document.getElementById('j').value,s=document.getElementById('s').value,"
        "e=document.getElementById('e').checked;document.getElementById('o').innerText="
        "j===''?'Job name required':(j==='nas-backup'&&s==='Daily'&&e?'Backup job valid':'Invalid');\">Validate</button>"
        "<div id=o></div></body></html>"
    )

    def _form_url(self):
        d = Path(tempfile.mkdtemp())
        f = d / "form.html"
        f.write_text(self._FORM, encoding="utf-8")
        return f.as_uri()

    def test_form_fill_select_check_click_drive_the_dom(self):
        from app.application.code_agent.tools._web import _browser_render
        url = self._form_url()
        _t, _u, empty, _a, _v = _browser_render(url, None, 4000, [{"click": "Validate"}])
        self.assertIn("Job name required", empty)                       # empty field → required
        _t, _u, ok, _a, _v = _browser_render(url, None, 4000, [
            {"fill": "Job name", "value": "nas-backup"}, {"select": "Schedule", "value": "Daily"},
            {"check": "Encryption"}, {"click": "Validate"}])
        self.assertIn("Backup job valid", ok)                           # fill+select+check+click
        self.assertNotIn("Invalid", ok)
        _t, _u, noenc, _a, _v = _browser_render(url, None, 4000, [
            {"fill": "Job name", "value": "nas-backup"}, {"select": "Schedule", "value": "Daily"},
            {"click": "Validate"}])
        self.assertNotIn("Backup job valid", noenc)                     # the checkbox genuinely mattered

    # Batch D: a fixed 1000px block overflows a 375px phone but fits a 1280px desktop —
    # the browser MEASURES it (real layout signal), and no viewport request → no signal.
    _WIDE = ("<!doctype html><body style='margin:0'>"
             "<div style='width:1000px;height:40px'>wide block</div></body>")

    def _wide_url(self):
        d = Path(tempfile.mkdtemp())
        f = d / "wide.html"
        f.write_text(self._WIDE, encoding="utf-8")
        return f.as_uri()

    def test_viewport_measures_horizontal_overflow(self):
        from app.application.code_agent.tools._web import _browser_render
        url = self._wide_url()
        _t, _u, _b, _a, vp_m = _browser_render(url, None, 2000, None, {"width": 375, "height": 812})
        self.assertTrue(vp_m["checked"])
        self.assertFalse(vp_m["no_hoverflow"])                          # 1000px overflows a 375px phone
        _t, _u, _b, _a, vp_d = _browser_render(url, None, 2000, None, {"width": 1280, "height": 800})
        self.assertTrue(vp_d["no_hoverflow"])                           # …but fits a 1280px desktop
        _t, _u, _b, _a, vp_none = _browser_render(url, None, 2000, None, None)
        self.assertIsNone(vp_none)                                      # no viewport requested → no signal


class ViewportCoerceTest(unittest.TestCase):
    """`_coerce_viewport` normalises the tool arg → size dict or None (no network)."""

    def test_presets_and_explicit_and_junk(self):
        from app.application.code_agent.tools._web import _coerce_viewport
        self.assertEqual(_coerce_viewport("mobile"), {"width": 375, "height": 812})
        self.assertEqual(_coerce_viewport("DESKTOP"), {"width": 1280, "height": 800})
        self.assertEqual(_coerce_viewport({"width": 400, "height": 700}), {"width": 400, "height": 700})
        self.assertIsNone(_coerce_viewport("phablet"))                  # unknown preset
        self.assertIsNone(_coerce_viewport({"width": 10, "height": 10}))  # too small → rejected
        self.assertIsNone(_coerce_viewport(None))


class ViewportLayoutVerifierTest(unittest.TestCase):
    """Batch D: the layout verifier (non-live). A viewport_layout criterion confirms ONLY on
    a real browser measurement — no horizontal overflow at the tested width — and FAILS on
    overflow. A plain render (no viewport measured) leaves it honestly unconfirmed, and a
    desktop measurement can't confirm a 'mobile' claim (width-bucket attribution)."""

    def _tracker(self, crit):
        return CriteriaTracker.from_spec(TaskSpec(success_criteria=[crit]))

    def _record_vp(self, t, vp, ok=True):
        t.record(tool_name="browser", args={"url": "http://x"}, ok=ok,
                 evidence="TITLE: X\nсодержимое", meta={"verifier": True, "viewport": vp})

    def test_classification_and_width_bucket(self):
        from app.application.code_agent.taskspec import _criterion_intent, _viewport_target
        self.assertEqual(_criterion_intent("нет горизонтального скролла на mobile"), "viewport_layout")
        self.assertEqual(_viewport_target("адаптив на mobile"), "narrow")
        self.assertEqual(_viewport_target("desktop раскладка"), "wide")
        self.assertIsNone(_viewport_target("страница без переполнения"))   # ambiguous → any width

    def test_width_bucket_ignores_stray_css_px(self):
        # Review #1/#3/#5: a CSS spacing/font px is NOT a viewport width and must NOT override
        # an explicit device keyword (this mis-bucketed a desktop criterion into a mobile render).
        from app.application.code_agent.taskspec import _viewport_target
        self.assertEqual(_viewport_target("десктоп раскладка: карточки 280px, без скролла"), "wide")
        self.assertEqual(_viewport_target("desktop 1280px без overflow, отступ 16px"), "wide")
        self.assertIsNone(_viewport_target("нет скролла ни на mobile, ни на desktop"))  # both → any, no hijack

    def test_negative_layout_phrasing_still_viewport(self):
        # Review #4: "не должно быть overflow" is an absence-of-overflow assertion — no_hoverflow
        # verifies it, so it must NOT fall through to generic and become unverifiable.
        from app.application.code_agent.taskspec import _criterion_intent
        self.assertEqual(_criterion_intent("на mobile не должно быть горизонтального скролла"), "viewport_layout")
        self.assertEqual(_criterion_intent("горизонтальная прокрутка отсутствует на mobile"), "viewport_layout")
        # a negative FILE criterion is still content_not_contains (not hijacked by a viewport word)
        self.assertEqual(_criterion_intent("файл app.css не содержит `mobile-first`"), "content_not_contains")

    def test_wide_criterion_needs_wide_measurement(self):
        # Mirror attribution (review #1/#5): a mobile-width measurement must NOT confirm a
        # DESKTOP layout claim; the correct desktop measurement does.
        t = self._tracker("десктопная раскладка без горизонтального скролла")   # wide
        self._record_vp(t, {"checked": True, "width": 375, "no_hoverflow": True})
        self.assertEqual(t.items[0]["status"], "unconfirmed")                   # 375 can't confirm desktop
        self._record_vp(t, {"checked": True, "width": 1280, "no_hoverflow": True})
        self.assertEqual(t.items[0]["status"], "confirmed")

    def test_no_overflow_confirms(self):
        t = self._tracker("нет горизонтального скролла на mobile")
        self._record_vp(t, {"checked": True, "width": 375, "no_hoverflow": True})
        self.assertEqual(t.items[0]["status"], "confirmed")

    def test_overflow_fails(self):
        t = self._tracker("нет горизонтального скролла на mobile")
        self._record_vp(t, {"checked": True, "width": 375, "no_hoverflow": False})
        self.assertEqual(t.items[0]["status"], "failed")                # overflow = a real red

    def test_plain_render_without_viewport_stays_unconfirmed(self):
        t = self._tracker("нет горизонтального скролла на mobile")
        self._record_vp(t, None)
        self.assertEqual(t.items[0]["status"], "unconfirmed")           # never measured → honest partial

    def test_desktop_measure_cannot_confirm_mobile_claim(self):
        t = self._tracker("нет горизонтального скролла на mobile")
        self._record_vp(t, {"checked": True, "width": 1280, "no_hoverflow": True})
        self.assertEqual(t.items[0]["status"], "unconfirmed")           # width-bucket mismatch (attribution)

    def test_ambiguous_width_confirmed_by_any_measure(self):
        t = self._tracker("страница без горизонтального переполнения")   # no bucket → any width ok
        self._record_vp(t, {"checked": True, "width": 1280, "no_hoverflow": True})
        self.assertEqual(t.items[0]["status"], "confirmed")


class ToolSearchEconomyTest(unittest.TestCase):
    def tearDown(self):
        deferred_tools.clear_run("ts-econ")

    def test_browser_tool_search_redirected_once_browser_is_active(self):
        # Once `browser` is activated for the run, a repeat browser/playwright tool_search
        # is a wasted round trip (Subnet spent 4). The loop redirects it instead of
        # re-searching, and steers the model to browser(actions=…).
        chat = _SeqChat([
            _call("tool_search", query="browser"),
            _call("tool_search", query="playwright browser evaluate"),
            _final("готово"),
        ], _final())
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = list(agent_loop.stream_code_agent(
                user_message="открой локальную страницу в браузере и проверь DOM",
                project_root=tmp, run_id="ts-econ",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        ts = [e for e in evs if e.get("type") == "tool_call" and e.get("tool") == "tool_search"]
        self.assertTrue(
            any("уже активен" in str(e.get("result", "")) for e in ts),
            f"expected a browser re-search redirect; got {[e.get('result') for e in ts]}",
        )


class ServerRedirectTest(unittest.TestCase):
    _SPEC = (
        "Цель:\nСделай мини-фронт.\n\n"
        "Критерии готовности:\n"
        "- dev server запускается через `run_server`\n"
        "- browser interaction: после ввода `x` и нажатия `Go` rendered DOM содержит `Result`\n"
    )

    def tearDown(self):
        deferred_tools.clear_run("ts-redir")

    def test_repeat_run_server_redirected_to_browser_when_server_up(self):
        # server starts once (actual_url known); a SECOND run_server(start) while a
        # browser-interaction criterion is still open must be redirected to browser,
        # not executed — the live 10/13 looped run_server into a loop_guard stop.
        chat = _SeqChat([
            _call("run_server", action="start", command="npm run dev", port=5173),
            _call("run_server", action="start", command="npm run dev", port=5173),
            _final("готово"),
        ], _final())

        def _exec(request, **kw):
            return SimpleNamespace(status="ok", output={
                "text": "up", "ok": True, "actual_url": "http://localhost:5173",
                "server_started": True, "actual_port": 5173})

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            evs = list(agent_loop.stream_code_agent(
                user_message=self._SPEC, project_root=tmp, run_id="ts-redir",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        rs = [e for e in evs if e.get("type") == "tool_call" and e.get("tool") == "run_server"]
        self.assertTrue(any("НЕ перезапускай" in str(e.get("result", "")) for e in rs),
                        f"expected a run_server→browser redirect; got {[str(e.get('result',''))[:60] for e in rs]}")


class CliOutputVerifierTest(unittest.TestCase):
    """Batch A: a command's stdout/stderr closes command_output criteria — one run closes
    several; grep/read_file do not; the negative case needs text + non-zero exit."""

    _LOGSUM = """Цель:
Создай новый маленький CLI-проект `log-summarizer` в текущей рабочей директории.

Критерии готовности:
- создана папка `log-summarizer`
- `log-summarizer/index.js` существует
- `log-summarizer/sample.log` существует
- `node index.js sample.log` выводит `INFO: 2`
- `node index.js sample.log` выводит `WARN: 1`
- `node index.js sample.log` выводит `ERROR: 2`
- `node index.js sample.log` выводит `TOTAL: 5`
- `node index.js missing.log` выводит `File not found` и завершается с ошибкой

Подвох:
- grep/read_file по исходникам НЕ подтверждает output criteria
"""

    _CSV = """Цель:
Создай CLI-проект `csv-inventory-checker`.

Критерии готовности:
- создана папка `csv-inventory-checker`
- запуск с исходным CSV выводит `OK: 1`
- запуск с исходным CSV выводит `WARN: 1`
- запуск с исходным CSV выводит `DOWN: 1`
- запуск с исходным CSV выводит `SUBNET: 192.168.88.0/24`
"""

    def _st(self, t, needle):
        return next((it["status"] for it in t.items if needle in it["text"]), "?")

    def test_one_run_closes_all_output_criteria_for_a_command(self):
        t = CriteriaTracker.from_spec(derive_task_spec(self._LOGSUM))
        # every 'node index.js sample.log выводит X' is command_output
        cmd_out = [it for it in t.items if it["intent"] == "command_output"]
        self.assertEqual(len(cmd_out), 5)   # 4 positive + 1 negative
        out = "STDOUT:\nINFO: 2\nWARN: 1\nERROR: 2\nTOTAL: 5"
        t.record(tool_name="run_bash", args={"command": "cd log-summarizer && node index.js sample.log"},
                 ok=True, evidence=out, meta={"exit_code": 0})
        for tok in ("INFO: 2", "WARN: 1", "ERROR: 2", "TOTAL: 5"):
            self.assertEqual(self._st(t, tok), "confirmed", tok)
        self.assertEqual(self._st(t, "File not found"), "unconfirmed")  # negative not run yet

    def test_negative_case_needs_text_and_nonzero_exit(self):
        spec = derive_task_spec(self._LOGSUM)
        # exit 0 with the text must NOT confirm the fails-with-output criterion
        t0 = CriteriaTracker.from_spec(spec)
        t0.record(tool_name="run_bash", args={"command": "node index.js missing.log"},
                  ok=True, evidence="STDERR:\nFile not found", meta={"exit_code": 0})
        self.assertEqual(self._st(t0, "File not found"), "unconfirmed")
        # text + non-zero exit confirms it
        t1 = CriteriaTracker.from_spec(spec)
        t1.record(tool_name="run_bash", args={"command": "node index.js missing.log"},
                  ok=False, evidence="STDERR:\nFile not found", meta={"exit_code": 1})
        self.assertEqual(self._st(t1, "File not found"), "confirmed")

    def test_grep_does_not_close_command_output(self):
        t = CriteriaTracker.from_spec(derive_task_spec(self._LOGSUM))
        t.record(tool_name="run_bash", args={"command": "grep -r 'INFO: 2' ."},
                 ok=True, evidence="index.js: INFO: 2", meta={"exit_code": 0})
        self.assertEqual(self._st(t, "INFO: 2"), "unconfirmed")

    def _rec(self, crit, cmd, out, ec):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[crit]))
        t.record(tool_name="run_bash", args={"command": cmd}, ok=(ec == 0), evidence=out, meta={"exit_code": ec})
        return t.items[0]["status"]

    def test_prose_command_output_is_not_auto_verifiable(self):
        # Final design (whack-a-mole convergence): a PROSE run description with no named
        # runnable command ('запуск с CSV выводит `OK: 1`') is NOT command_output — it
        # classifies generic (honest), so no echo/cat/build/eval can false-confirm it.
        for c in derive_task_spec(self._CSV).success_criteria:
            self.assertNotEqual(_criterion_intent(c), "command_output", c)
        # naming the command makes it verifiable
        self.assertEqual(_criterion_intent("`node check.js inventory.csv` выводит `OK: 1`"), "command_output")

    def test_named_output_confirmed_by_equivalent_invocations(self):
        # interpreter/path/env/wrapper differences are the SAME invocation → confirm.
        crit = "`node index.js sample.log` выводит `INFO: 2`"
        for cmd in ("node index.js sample.log", "cd app && node index.js sample.log",
                    "node ./index.js sample.log", "NODE_ENV=prod node index.js sample.log",
                    'bash -c "node index.js sample.log"', "node --enable-source-maps index.js sample.log"):
            self.assertEqual(self._rec(crit, cmd, "STDOUT:\nINFO: 2", 0), "confirmed", cmd)
        self.assertEqual(self._rec("`python app.py` выводит `Ready`", "python3 app.py", "STDOUT:\nReady", 0), "confirmed")

    def test_named_output_rejects_dumps_wrong_arg_and_red(self):
        # POSITIVE-evidence: only a run that EXECUTES the named script confirms. A run that
        # merely prints/reads the token — echo/cat/sed/awk, an inline-eval EVEN WITH the
        # script files appended (node -e "…" index.js sample.log — node never runs them),
        # or an unknown text tool (jq/cut) — does not; nor does a wrong arg or a red run.
        crit = "`node index.js sample.log` выводит `INFO: 2`"
        for cmd in ("echo INFO: 2", "cat index.js", "sed '' index.js", "awk '1' index.js",
                    'bash -c "echo INFO: 2"', "node -e \"console.log('INFO: 2')\"",
                    "node -e \"console.log('INFO: 2')\" index.js sample.log",  # eval + files appended
                    "jq . index.js sample.log", "cut -f1 index.js"):            # unknown text tools
            self.assertEqual(self._rec(crit, cmd, "STDOUT:\nINFO: 2", 0), "unconfirmed", cmd)
        self.assertEqual(self._rec(crit, "node index.js missing.log", "STDOUT:\nINFO: 2", 0), "unconfirmed")  # wrong arg
        self.assertEqual(self._rec(crit, "node index.js sample.log", "STDOUT:\nINFO: 2", 1), "unconfirmed")   # red exit

    def test_composite_run_does_not_confirm_token_from_another_command(self):
        # round-5 (novel class): a chained/piped/substituted run mixes other commands'
        # output, so a token can't be attributed to the named program. `node index.js &&
        # echo OK` (echo fabricates OK) must NOT confirm; a plain / leading-`cd &&` run does.
        crit = "`node index.js` выводит `OK`"
        for cmd in ("node index.js && echo OK", "node index.js ; echo OK",
                    "node index.js || echo OK", "node index.js | grep OK",
                    "echo $(node index.js) OK", "node index.js|tee out.log"):
            self.assertEqual(self._rec(crit, cmd, "STDOUT:\nstarting\nOK", 0), "unconfirmed", cmd)
        self.assertEqual(self._rec(crit, "node index.js", "STDOUT:\nOK", 0), "confirmed")
        self.assertEqual(self._rec(crit, "cd app && node index.js", "STDOUT:\nOK", 0), "confirmed")  # leading cd ok

    def test_positive_output_requires_exit_zero(self):
        crit = "`npm test` выводит `passing`"
        self.assertEqual(self._rec(crit, "npm test", "STDOUT:\n12 passing\n3 failing", 1), "unconfirmed")
        self.assertEqual(self._rec(crit, "npm test", "STDOUT:\n15 passing", 0), "confirmed")

    def test_output_mention_without_expected_token_is_command_check(self):
        self.assertEqual(_criterion_intent("`npm run build` produces no errors in the output"), "command_check")

    def test_expected_token_before_a_later_verb_is_still_extracted(self):
        # verb-anchor: the value after the FIRST output verb wins even when a later verb
        # follows ('выводит `2` и печатает …') — else the criterion mis-classifies generic.
        crit = "`node app.js` выводит `2` и печатает перевод строки"
        self.assertEqual(_criterion_intent(crit), "command_output")
        self.assertEqual(self._rec(crit, "node app.js", "STDOUT:\n2", 0), "confirmed")

    def test_trailing_location_cue_still_classifies_and_confirms(self):
        from app.application.code_agent.taskspec import _criterion_intent
        crit = "`node index.js sample.log` выводит `INFO: 2` в stdout"
        self.assertEqual(_criterion_intent(crit), "command_output")
        self.assertEqual(self._rec(crit, "node index.js sample.log", "STDOUT:\nINFO: 2", 0), "confirmed")

    def test_closure_groups_output_criteria_by_command(self):
        from app.application.code_agent import criterion_closure as cc
        acts = cc.missing_verifier_actions(CriteriaTracker.from_spec(derive_task_spec(self._LOGSUM)))
        run_bash_acts = [a for a in acts if a["tool"] == "run_bash"]
        # ONE run_bash per distinct command (sample.log group + missing.log), not one per token
        self.assertEqual(len(run_bash_acts), 2)
        sample = next(a for a in run_bash_acts if "sample.log" in a["call"])
        for tok in ("INFO: 2", "WARN: 1", "ERROR: 2", "TOTAL: 5"):
            self.assertIn(tok, sample["call"])


class VerifierGateTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("ts-gate", "ts-confirmed", "ts-scrub"):
            deferred_tools.clear_run(rid)

    def test_model_success_marks_scrubbed_when_not_confirmed(self):
        # Batch C: on a partial/unverified run the model may still write a ✅ table/row.
        # The finalizer neutralises those glyphs (→ ▫) so no green check implies "done"
        # beside the runtime status block — the readiness panel owns the verdict.
        marky = _final("Итог:\n| Проверка | Статус |\n| typecheck | ✅ |\n✔ Все действия выполнены")
        chat = _SeqChat([_call("write_file", path="a.ps1", content="x"), _final("сделал")], marky)
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(status="ok", output={"text": "ok", "ok": True, "touched_path": "a.ps1"})):
            evs = list(agent_loop.stream_code_agent(
                user_message=_STRUCTURED, project_root=tmp, run_id="ts-scrub",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertNotEqual(done.get("completion_status"), "confirmed")   # precondition: partial
        final = [e for e in evs if e.get("type") == "final_response"][-1]["text"]
        for g in ("✅", "✔"):
            self.assertNotIn(g, final, g)                                  # model marks neutralised
        self.assertIn("▫", final)                                          # replaced, not deleted
        self.assertIn("typecheck", final)                                  # surrounding text kept
        self.assertIn("Готовность задачи — по verifier", final)            # runtime block present

    def test_open_criteria_get_one_bounded_closure_turn_then_partial(self):
        # Model edits a file then tries to close WITHOUT any verifier. The Criterion
        # Closure Gate (Ph7.12) spends exactly ONE bounded turn asking for the missing
        # verifier calls; the model still doesn't verify, so the run finalizes
        # honest-partial with a RUNTIME-owned status block (not the model's word).
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
        self.assertTrue(done.get("ok"))                                # runtime health OK …
        self.assertEqual(done.get("completion_status"), "unverified")  # … task NOT verified
        self.assertTrue(done.get("partial"))
        self.assertFalse(done.get("criteria_confirmed"))
        # exactly ONE bounded closure turn (edit → closure nudge → final) — not a loop
        self.assertEqual(len([e for e in evs if e.get("type") == "step_started"]), 3)
        final = [e for e in evs if e.get("type") == "final_response"][-1]
        self.assertIn("Готовность задачи — по verifier", final["text"])   # runtime report
        self.assertIn("Не подтверждено verifier", final["text"])
        self.assertNotIn("Все критерии подтверждены", final["text"])      # never claims all-passed
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


class _RecordingChat(_SeqChat):
    """_SeqChat that also captures the message list of every LLM call, so a test can
    assert on the closure nudge / auto-verifier report the runtime injected."""

    def __init__(self, responses, fallback):
        super().__init__(responses, fallback)
        self.seen_messages: list[list[dict]] = []

    def __call__(self, **kw):
        self.seen_messages.append(list(kw.get("messages") or []))
        return super().__call__(**kw)


class AutoVerifierClosureTest(unittest.TestCase):
    """Runtime-owned auto-verifier pass: when missing_verifier_actions() knows the exact
    safe calls (path_exists / named run_bash / browser / ssh_assert*), the RUNTIME
    executes them itself at the closure gate — the model only sees the result. Green →
    criteria confirmed with no extra model turn; red → one short report turn; bounded
    (once per run, ≤5 calls); interactions / cleanup / inferred commands stay
    model-directed."""

    _LOCAL_TASK = (
        "Создай CLI-утилиту log-summarizer в подпапке log-summarizer.\n\n"
        "Цель:\nУтилита считает уровни логов.\n\n"
        "Критерии готовности:\n"
        "1. файл `log-summarizer/index.js` существует\n"
        "2. `node index.js sample.log` выводит `TOTAL: 5`\n"
    )

    def tearDown(self):
        for rid in ("ts-auto-green", "ts-auto-red", "ts-auto-budget", "ts-auto-skip",
                    "ts-auto-blocked", "ts-auto-cleanup"):
            deferred_tools.clear_run(rid)

    @staticmethod
    def _exec_for(outputs: dict, calls: list):
        """kernel-exec mock: routes by tool_name, logs (tool, args) into `calls`."""
        def _exec(request, **kw):
            tool = str(getattr(request, "tool_name", ""))
            calls.append((tool, dict(getattr(request, "args", {}) or {})))
            out = outputs.get(tool, {"text": "ok", "ok": True})
            return SimpleNamespace(status="ok", output=dict(out))
        return _exec

    def test_green_pass_confirms_without_extra_model_turn(self):
        # Model writes the file and finalizes WITHOUT verifying. The runtime executes
        # path_exists + the named run_bash itself → both criteria confirm → the run
        # finalizes CONFIRMED with zero closure turns (2 model steps total).
        calls: list = []
        chat = _RecordingChat(
            [_call("write_file", path="log-summarizer/index.js", content="x"), _final("готово")],
            _final("готово"))
        outputs = {
            "write_file": {"text": "ok", "ok": True, "touched_path": "log-summarizer/index.js"},
            "path_exists": {"text": "log-summarizer/index.js: существует (файл)", "ok": True,
                            "verifier": True, "evidence": "log-summarizer/index.js: существует (файл)"},
            # REAL tool shape: run_bash returns text+exit_code with NO `ok` key
            "run_bash": {"text": "$ node index.js sample.log\nexit=0\nSTDOUT:\nINFO: 2\nTOTAL: 5",
                         "exit_code": 0},
        }
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=self._exec_for(outputs, calls)):
            evs = list(agent_loop.stream_code_agent(
                user_message=self._LOCAL_TASK, project_root=tmp, run_id="ts-auto-green",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done.get("completion_status"), "confirmed")
        self.assertTrue(done.get("criteria_confirmed"))
        # no closure nudge turn: write → final, the pass isn't a model step
        self.assertEqual(len([e for e in evs if e.get("type") == "step_started"]), 2)
        auto = [e for e in evs if e.get("type") == "tool_call" and e.get("auto_verifier")]
        self.assertEqual({e["tool"] for e in auto}, {"path_exists", "run_bash"})
        # cwd resolution: the named command ran with the deterministic cd prefix
        run_cmds = [a.get("command") for t, a in calls if t == "run_bash"]
        self.assertEqual(run_cmds, ["cd log-summarizer && node index.js sample.log"])

    def test_red_pass_gives_short_report_then_honest_partial(self):
        # The auto run goes RED → the model gets ONE short report turn with the
        # evidence; it doesn't fix anything → the run ends honestly unverified
        # (command_output red is neutral, never a false hard-fail). The mock uses
        # the REAL run_bash shape — text+exit_code, NO `ok` key — the review found
        # the red path only worked against an impossible {"ok": False} mock (F7).
        calls: list = []
        chat = _RecordingChat(
            [_call("write_file", path="index.js", content="x"), _final("сделал")],
            _final("готово"))
        outputs = {
            "write_file": {"text": "ok", "ok": True, "touched_path": "index.js"},
            "path_exists": {"text": "index.js: существует (файл)", "ok": True,
                            "verifier": True, "evidence": "index.js: существует (файл)"},
            "run_bash": {"text": "$ node index.js sample.log\nexit=1\nSTDERR:\nError: boom",
                         "exit_code": 1},
        }
        task = ("Цель:\nCLI.\n\nКритерии готовности:\n"
                "1. `node index.js sample.log` выводит `TOTAL: 5`\n")
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=self._exec_for(outputs, calls)):
            evs = list(agent_loop.stream_code_agent(
                user_message=task, project_root=tmp, run_id="ts-auto-red",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done.get("completion_status"), "unverified")   # red run = not proven, not failed
        self.assertEqual(len([e for e in evs if e.get("type") == "step_started"]), 3)  # one report turn
        # the report turn carries the runtime's evidence, not a bare nudge
        nudges = [m["content"] for ms in chat.seen_messages for m in ms
                  if m.get("role") == "user" and "Runtime выполнил" in str(m.get("content"))]
        self.assertTrue(nudges)
        self.assertIn("Error: boom", nudges[-1])
        self.assertIn("НЕ прошла", nudges[-1])
        # the auto pass ran ONCE: exactly one run_bash across the whole run
        self.assertEqual(len([1 for t, _ in calls if t == "run_bash"]), 1)

    def test_budget_caps_auto_calls_at_five(self):
        calls: list = []
        crits = "\n".join(f"{i}. файл `f{i}.txt` существует" for i in range(1, 8))
        task = f"Цель:\nФайлы.\n\nКритерии готовности:\n{crits}\n"
        chat = _RecordingChat([_final("готово")], _final("готово"))
        outputs = {"path_exists": {"text": "НЕ найден", "ok": False, "verifier": True,
                                   "evidence": "НЕ найден"}}
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=self._exec_for(outputs, calls)):
            list(agent_loop.stream_code_agent(
                user_message=task, project_root=tmp, run_id="ts-auto-budget",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        self.assertEqual(len([1 for t, _ in calls if t == "path_exists"]), 5)  # 7 criteria, cap 5

    def test_blocked_call_is_not_recorded_as_verdict(self):
        # Review F2: a kernel-BLOCKED run (rate limit / scope) never RAN — it must not
        # fail the criterion. The pass reports it and leaves the call to the model.
        calls: list = []
        task = "Цель:\nПроверки.\n\nКритерии готовности:\n1. `npm test` проходит\n"
        chat = _RecordingChat([_final("готово")], _final("готово"))

        def _exec(request, **kw):
            calls.append(str(getattr(request, "tool_name", "")))
            return SimpleNamespace(status="blocked",
                                   output={"text": "rate limited", "ok": False})

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            evs = list(agent_loop.stream_code_agent(
                user_message=task, project_root=tmp, run_id="ts-auto-blocked",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        self.assertIn("run_bash", calls)                                   # the pass did try
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done.get("completion_status"), "unverified")      # NOT failed
        self.assertTrue(all(c["status"] != "failed" for c in done["criteria"]))

    def test_cleanup_confirm_via_absent_probe_reported_green(self):
        # Review F8: path_exists ok=False (file absent) CONFIRMS a local cleanup
        # criterion — the report must list it GREEN («НЕ повторяй»), not tell the
        # model the check failed (which would invite re-creating the deleted file).
        task = ("Цель:\nCleanup.\n\nКритерии готовности:\n"
                "1. временный файл `tmp-data.txt` удалён\n"
                "2. `node index.js sample.log` выводит `TOTAL: 5`\n")
        chat = _RecordingChat(
            [_call("write_file", path="index.js", content="x"), _final("сделал")],
            _final("готово"))
        outputs = {
            "write_file": {"text": "ok", "ok": True, "touched_path": "index.js"},
            "path_exists": {"text": "tmp-data.txt: НЕ найден", "ok": False,
                            "verifier": True, "evidence": "tmp-data.txt: НЕ найден"},
            "run_bash": {"text": "$ node index.js sample.log\nexit=1\nSTDERR:\nboom",
                         "exit_code": 1},
        }
        calls: list = []
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=self._exec_for(outputs, calls)):
            evs = list(agent_loop.stream_code_agent(
                user_message=task, project_root=tmp, run_id="ts-auto-cleanup",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        cleanup = next(c for c in done["criteria"] if "tmp-data" in c["text"])
        self.assertEqual(cleanup["status"], "confirmed")
        nudges = [m["content"] for ms in chat.seen_messages for m in ms
                  if m.get("role") == "user" and "Runtime" in str(m.get("content"))]
        self.assertTrue(nudges)
        note = nudges[-1]
        self.assertIn("НЕ повторяй", note)                    # cleanup probe listed green…
        self.assertIn("path_exists", note.split("НЕ повторяй")[1].split("\n")[0])
        self.assertNotIn("path_exists", "".join(line for line in note.splitlines()
                                                if "НЕ прошла" in line))   # …never as red

    def test_interaction_and_remote_cleanup_stay_model_directed(self):
        # No auto spec for a browser interaction (selectors unknown) or a remote cleanup
        # (delete-then-verify is the model's flow) → the pass executes NOTHING and the
        # nudge still lists both calls, exactly like before the auto layer.
        calls: list = []
        task = ("Цель:\nФорма.\n\nКритерии готовности:\n"
                "1. browser interaction: заполни `CIDR` значением `10.0.0.0/24`, нажми "
                "`Calculate` — на странице показывает `Network: 10.0.0.0`\n"
                "2. файл C:\\Lab\\agent.ps1 удалён после проверки\n")
        chat = _RecordingChat([_final("готово")], _final("готово"))
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=self._exec_for({}, calls)):
            evs = list(agent_loop.stream_code_agent(
                user_message=task, project_root=tmp, run_id="ts-auto-skip",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        self.assertEqual([e for e in evs if e.get("auto_verifier")], [])   # nothing auto-ran
        self.assertEqual(calls, [])                                        # kernel untouched
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertNotEqual(done.get("completion_status"), "confirmed")


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

    def test_path_exists_confirms_local_file_criteria(self):
        # local FS verifier: "создана папка X" / "проект внутри X" close via path_exists,
        # not ssh (remote) — the bare dir name `subnet-helper` is now a real file target.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "создана новая папка `subnet-helper`",
            "проект находится именно внутри `subnet-helper`",
        ]))
        self.assertEqual(t.items[0]["intent"], "file_exists")
        self.assertEqual(t.items[1]["intent"], "file_exists")
        t.record(tool_name="path_exists", args={"path": "subnet-helper"}, ok=True, evidence="ok (каталог)")
        self.assertEqual(t.items[0]["status"], "confirmed")
        self.assertEqual(t.items[1]["status"], "confirmed")

    def test_path_exists_missing_does_not_fail_file_exists(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["создана новая папка `subnet-helper`"]))
        t.record(tool_name="path_exists", args={"path": "subnet-helper"}, ok=False, evidence="НЕ найден")
        self.assertEqual(t.items[0]["status"], "unconfirmed")   # absence is neutral, never a hard fail

    def test_interaction_criterion_targets_only_the_result_token(self):
        from app.application.code_agent.taskspec import _dom_targets
        c = ("browser interaction: после ввода `192.168.1.0/24` и нажатия `Calculate` "
             "rendered DOM содержит `Network: 192.168.1.0`")
        # the input value and the button are the ACTION — only the result is required
        self.assertEqual(_dom_targets(c), {"network: 192.168.1.0"})

    def test_browser_post_interaction_dom_confirms_interaction_only(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "browser interaction: после ввода `192.168.1.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.1.0`",
            "browser interaction: после ввода `bad-input` и нажатия `Calculate` rendered DOM содержит `Invalid CIDR`",
        ]))
        dom = ("[после действий: fill CIDR=192.168.1.0/24; click Calculate]\n"
               "TITLE: Subnet Helper\nNetwork: 192.168.1.0\nMask: 255.255.255.0\nHosts: 254")
        t.record(tool_name="browser", args={"url": "http://localhost:5173"}, ok=True, evidence=dom, meta={"interacted": True})
        self.assertEqual(t.items[0]["status"], "confirmed")       # result present in post-action DOM
        self.assertEqual(t.items[1]["status"], "unconfirmed")     # bad-input path not exercised → honest

    def test_whitespace_separated_dom_confirms_interaction(self):
        # Live 7/13: the app renders label and value in separate elements, so inner_text
        # is "Network:\n192.168.88.0" while the token is "Network: 192.168.88.0". One
        # valid browser call must confirm all three interaction criteria.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.88.0`",
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Mask: 255.255.255.0`",
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Hosts: 254`",
        ]))
        dom = "TITLE: Subnet Helper\nNetwork:\n192.168.88.0\nMask:\n255.255.255.0\nHosts:\n254"
        t.record(tool_name="browser", args={"url": "http://localhost:5173"}, ok=True, evidence=dom, meta={"interacted": True})
        self.assertTrue(all(it["status"] == "confirmed" for it in t.items))

    def test_colonless_label_dom_confirms_interaction(self):
        # Live 10/13: the app renders the label WITHOUT a colon → DOM "Network 192.168.88.0"
        # while the token is "Network: 192.168.88.0". Punct-insensitive match must confirm.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.88.0`",
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Mask: 255.255.255.0`",
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Hosts: 254`",
        ]))
        dom = "TITLE: Subnet Helper\nNetwork\n192.168.88.0\nMask\n255.255.255.0\nHosts\n254"
        t.record(tool_name="browser", args={"url": "http://localhost:5173"}, ok=True, evidence=dom, meta={"interacted": True})
        self.assertTrue(all(it["status"] == "confirmed" for it in t.items))

    def test_dom_match_still_rejects_wrong_value(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["на экране `Network: 10.0.0.0`"]))
        t.record(tool_name="browser", args={"url": "x"}, ok=True, evidence="Network\n192.168.88.0")
        self.assertEqual(t.items[0]["status"], "unconfirmed")   # different value → no false match

    def _dom_confirms(self, target, dom):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[f"на экране `{target}`"]))
        t.record(tool_name="browser", args={"url": "x"}, ok=True, evidence=dom)
        return t.items[0]["status"] == "confirmed"

    def test_ip_value_does_not_prefix_match_a_longer_value(self):
        # Ph6 review: boundary-anchored match — a value must not prefix-match a longer one,
        # else the verifier certifies a WRONG rendered value (.1 inside .10, .25 in .255).
        self.assertFalse(self._dom_confirms("Network: 192.168.88.1", "Network\n192.168.88.10"))
        self.assertFalse(self._dom_confirms("192.168.88.25", "Broadcast\n192.168.88.255"))
        self.assertTrue(self._dom_confirms("Last: 192.168.88.254", "Last\n192.168.88.254"))  # exact ok

    def test_interaction_spec_ignores_field_name_and_result_tokens(self):
        from app.application.code_agent.taskspec import interaction_spec
        # field name quoted before the value → fill is the VALUE, not the field name
        s1 = interaction_spec("В поле `CIDR` введите `192.168.88.0/24`, нажать `Calculate`, содержит `Network`")
        self.assertEqual(s1["fill"], "192.168.88.0/24")
        self.assertEqual(s1["click"], "Calculate")
        # value in prose, only the button quoted → fill empty, NOT the button
        s2 = interaction_spec("Введите CIDR и нажмите `Calculate` — содержит `Network`")
        self.assertEqual(s2["fill"], "")
        self.assertEqual(s2["click"], "Calculate")
        # button in prose, only the result quoted → click empty, NOT the result token
        s3 = interaction_spec("нажмите кнопку расчёта, DOM содержит `Network`")
        self.assertEqual(s3["click"], "")

    def test_conditional_typecheck_skipped_not_unverified_when_absent(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "`npm run build` проходит без ошибок",
            "если в проекте есть `npm run typecheck`, он проходит без ошибок",
        ]))
        cond = t.items[1]
        self.assertTrue(cond["conditional"])
        t.record(tool_name="run_bash", args={"command": "npm run build"}, ok=True, evidence="exit 0")
        # model runs typecheck but the script is absent → npm exits non-zero
        t.record(tool_name="run_bash", args={"command": "npm run typecheck"}, ok=False, evidence="Missing script: typecheck")
        self.assertEqual(cond["status"], "unconfirmed")          # conditional never hard-fails
        t.finalize_conditionals()
        self.assertEqual(cond["status"], "skipped")              # n/a, not unverified
        self.assertEqual(t.completion_status(), "confirmed")     # the mandatory build is done

    def test_mandatory_existence_with_leading_esli_is_not_conditional(self):
        # Review of 6a47eaa: a mandatory existence/availability criterion casually written
        # with a leading "если существует…"/"если доступ…" must NOT be demoted to optional
        # (else a real miss is silently skipped → false "confirmed").
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "проверь, если существует файл `health.txt`",
        ]))
        self.assertFalse(t.items[0]["conditional"])              # not optional
        self.assertEqual(t.items[0]["intent"], "file_exists")    # a real, blocking criterion
        t.finalize_conditionals()
        self.assertEqual(t.items[0]["status"], "unconfirmed")    # stays open (never skipped)
        self.assertEqual(t.completion_status(), "unverified")

    def test_conditional_typecheck_confirms_when_script_runs_green(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "если в проекте есть `npm run typecheck`, он проходит без ошибок",
        ]))
        t.record(tool_name="run_bash", args={"command": "npm run typecheck"}, ok=True, evidence="exit 0")
        self.assertEqual(t.items[0]["status"], "confirmed")      # present + green → confirmed

    def test_browser_interaction_without_dom_word_classifies_and_confirms(self):
        # Batch B: 'browser interaction: … `Validate` показывает `X`' (no rendered/DOM/на-экране
        # word) is a DOM claim; the result token is the only target, and the real post-action
        # DOM (from the browser tool executing fill/select/check/click) confirms it.
        c1 = "browser interaction: пустой `Job name` + `Validate` показывает `Job name required`"
        c2 = "browser interaction: `nas-backup` + `Daily` + включённый `Encryption` + `Validate` показывает `Backup job valid`"
        from app.application.code_agent.taskspec import _criterion_intent, _dom_targets
        self.assertEqual(_criterion_intent(c1), "dom_contains")
        self.assertEqual(_criterion_intent(c2), "dom_contains")
        self.assertEqual(_dom_targets(c1), {"job name required"})   # only the RESULT, not Job name/Validate
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[c1, c2]))
        t.record(tool_name="browser", args={"url": "http://localhost:5173"}, ok=True,
                 evidence="Backup Form Checker\nJob name required", meta={"interacted": True})
        t.record(tool_name="browser", args={"url": "http://localhost:5173"}, ok=True,
                 evidence="Backup job valid", meta={"interacted": True})
        self.assertEqual(t.items[0]["status"], "confirmed")
        self.assertEqual(t.items[1]["status"], "confirmed")
        # a PLAIN render (no interaction ran) does NOT confirm an interaction criterion,
        # even if the token is statically present (attribution — Batch B review).
        t3 = CriteriaTracker.from_spec(TaskSpec(success_criteria=[c1]))
        t3.record(tool_name="browser", args={"url": "http://localhost:5173"}, ok=True,
                  evidence="Backup Form Checker\nJob name required")  # no meta → interacted False
        self.assertEqual(t3.items[0]["status"], "unconfirmed")
        # a bundle grep of the source does NOT confirm an interaction criterion
        t2 = CriteriaTracker.from_spec(TaskSpec(success_criteria=[c1]))
        t2.record(tool_name="run_bash", args={"command": "grep 'Job name required' src/"},
                  ok=True, evidence="app.js: Job name required", meta={"exit_code": 0})
        self.assertEqual(t2.items[0]["status"], "unconfirmed")

    def test_interaction_cue_without_browser_context_is_not_dom(self):
        # Batch B review: a fill/ввод cue must NOT hijack a FILE or CLI criterion into
        # browser-only dom_contains — the interaction→dom path needs a browser/page word.
        for c in ("заполните файл `config.ini` строкой `mode=prod`, файл содержит `mode=prod`",
                  "введите команду `node app.js`, вывод содержит `OK`"):
            self.assertNotEqual(_criterion_intent(c), "dom_contains", c)
        # the file one stays verifiable by ssh_assert_contains (content_contains), not browser
        self.assertEqual(
            _criterion_intent("заполните файл `config.ini` строкой `mode=prod`, файл содержит `mode=prod`"),
            "content_contains")

    def test_grep_and_node_script_never_confirm_interaction(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "browser interaction: после ввода `192.168.1.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.1.0`",
        ]))
        # a bundle grep that "finds" the string, or a node script printing it, is NOT a verdict
        t.record(tool_name="run_bash", args={"command": "grep -r 'Network: 192.168.1.0' dist/"}, ok=True, evidence="found")
        t.record(tool_name="run_bash", args={"command": "node verify.cjs"}, ok=True, evidence="Network: 192.168.1.0")
        self.assertEqual(t.items[0]["status"], "unconfirmed")

    def test_bundle_grep_and_read_file_never_confirm_dom_text(self):
        # Tool-economy honesty lock: finding the token in the bundle (grep) or a source
        # file (read_file) is NOT a rendered-DOM proof — only browser confirms visibility.
        t = self._vault()
        t.record(tool_name="run_bash", args={"command": "grep -r VaultDesk dist/"}, ok=True,
                 evidence="dist/app.js: VaultDesk")
        t.record(tool_name="read_file", args={"path": "dist/index.html"}, ok=True,
                 evidence="<title>VaultDesk</title>")
        self.assertEqual(self._st(t, "VaultDesk"), "unconfirmed")

    _SUBNET = """Цель:
Создай новый маленький frontend-проект `subnet-helper` в текущей рабочей директории и реализуй интерактивную мини-программу “Subnet Helper”.

Программа:
Мини-калькулятор CIDR для системного администратора.

Функциональность:
- Заголовок страницы: `Subnet Helper`
- Поле ввода с label `CIDR`
- Значение по умолчанию: `192.168.1.0/24`
- Кнопка `Calculate`
- После нажатия `Calculate` для `192.168.1.0/24` показать:
  - `Network: 192.168.1.0`
  - `Mask: 255.255.255.0`
  - `Hosts: 254`
- Если ввести `bad-input` и нажать `Calculate`, показать:
  - `Invalid CIDR`

Критерии готовности:
- создана новая папка `subnet-helper`
- проект находится именно внутри `subnet-helper`
- родительский проект не изменён, кроме создания папки `subnet-helper`
- `npm run build` проходит без ошибок
- если в проекте есть `npm run typecheck`, он проходит без ошибок
- dev server запускается через `run_server`
- `browser` открывает страницу на localhost-порту, поднятом через `run_server`, без SSRF-block
- rendered DOM содержит текст `Subnet Helper`
- rendered DOM содержит текст `CIDR`
- rendered DOM содержит текст `Calculate`
- browser interaction: после ввода `192.168.1.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.1.0`
- browser interaction: после ввода `192.168.1.0/24` и нажатия `Calculate` rendered DOM содержит `Mask: 255.255.255.0`
- browser interaction: после ввода `192.168.1.0/24` и нажатия `Calculate` rendered DOM содержит `Hosts: 254`
- browser interaction: после ввода `bad-input` и нажатия `Calculate` rendered DOM содержит `Invalid CIDR`

Подвох:
- grep/findstr по исходникам или bundle НЕ подтверждает rendered DOM criteria
- HTTP 200 без browser/rendered DOM text НЕ подтверждает UI criteria
- наличие функции расчёта в коде НЕ подтверждает browser interaction criteria
- нельзя писать `COMPLETED/confirmed`, если browser interaction criteria не проверены verifier'ом
- если browser verifier недоступен/заблокирован, completion_status должен быть partial/unverified, не confirmed

Ограничения:
- создать только папку `subnet-helper`
- не менять файлы родительского проекта, кроме создания `subnet-helper`
- не добавлять backend
- не использовать внешние API
- не добавлять тяжёлый UI framework
- не запускать долгоживущие процессы через `run_bash`; dev server только через `run_server`

Tool economy:
- не использовать `todo_update` для каждого мелкого шага
- не повторять один и тот же verifier без новой причины
- для DOM/UI criteria использовать `browser`, не `grep`/`findstr`/bundle search
- ориентир: уложиться примерно в 20-35 tool calls

Финальный отчёт:
- где создан проект
- какие файлы созданы
- какие verifier checks прошли
- результат `typecheck`, если есть
- результат `build`
- результат `run_server`
- результат browser DOM/interactions verifier
- deterministic completion_status
- что осталось unverified/failed, если есть"""

    def test_subnet_helper_functionality_section_never_becomes_criteria(self):
        spec = derive_task_spec(self._SUBNET)
        crit = " || ".join(spec.success_criteria).lower()
        # the "Функциональность" bullets must NOT be readiness criteria
        for leaked in ("поле ввода с label", "значение по умолчанию", "заголовок страницы"):
            self.assertNotIn(leaked, crit, f"Функциональность leaked into criteria: {leaked}")
        # exactly the "Критерии готовности" set (14 lines − 1 scope rule) survives
        self.assertEqual(len(spec.success_criteria), 13)
        # the real criteria ARE present
        self.assertTrue(any("создана новая папка" in c.lower() for c in spec.success_criteria))
        self.assertTrue(any("npm run build" in c.lower() for c in spec.success_criteria))
        self.assertTrue(any("rendered dom содержит текст `subnet helper`" in c.lower() for c in spec.success_criteria))
        self.assertTrue(any("browser interaction" in c.lower() for c in spec.success_criteria))
        # the spec/behaviour lines land in details, not criteria
        self.assertTrue(any("поле ввода с label" in d.lower() for d in spec.details))
        # the non-mutation scope rule is a constraint, not a criterion
        self.assertTrue(any("родительский проект не изменён" in c.lower() for c in spec.constraints))
        self.assertFalse(any("родительский проект не изменён" in c.lower() for c in spec.success_criteria))

    def test_context_adds_economy_route_for_dom_task_only(self):
        dom_block = taskspec_context(derive_task_spec(self._VAULT))
        self.assertIn("Экономный маршрут", dom_block)
        self.assertIn("http_api для тех же DOM-критериев не нужен", dom_block)
        # a pure ssh/file task gets NO frontend route injected
        ssh_block = taskspec_context(TaskSpec(success_criteria=[
            "файл `C:\\lab\\snapshot.txt` существует",
            "файл `C:\\lab\\snapshot.txt` содержит строку `ok`",
        ]))
        self.assertNotIn("Экономный маршрут", ssh_block)

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


# ── Criterion Closure + Cleanup Barrier, end-to-end (Ph7.12) ─────

_DIR = "C:\\AgentLabGlobalCanary"
_SNAP = "C:\\AgentLabGlobalCanary\\snapshot.txt"
_CANARY_TASK = (
    "Цель: канарейка на home-srv01.\n"
    "Критерии готовности:\n"
    f"- директория `{_DIR}` существует\n"
    f"- файл `{_SNAP}` существует\n"
    f"- файл `{_SNAP}` содержит строку `project=frontend-global-live`\n"
    f"- файл `{_SNAP}` содержит строку `status=ok`\n"
    f"- файл `{_SNAP}` содержит строку `dom=verified`\n"
    f"- временный файл `{_DIR}` удалён после cleanup"
)


def _verifier_exec(request, **kw):
    """Mock kernel exec: ssh verifier tools pass with a verifier verdict; a delete
    (ssh_run_ps) runs plainly. The matcher keys pattern/path off the call args."""
    tool = getattr(request, "tool_name", "")
    if tool in ("ssh_exists", "ssh_read", "ssh_assert_contains", "ssh_assert_not_contains", "ssh_not_exists"):
        return SimpleNamespace(status="ok", output={
            "text": "OK", "ok": True, "verifier": True, "evidence": "verified", "touched_host": "home-srv01"})
    return SimpleNamespace(status="ok", output={"text": "done", "ok": True, "touched_host": "home-srv01"})


class ClosureBarrierLoopTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("cb-partial", "cb-happy"):
            deferred_tools.clear_run(rid)

    def _run(self, chat, rid):
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_verifier_exec):
            return list(agent_loop.stream_code_agent(
                user_message=_CANARY_TASK, project_root=tmp, run_id=rid,
                auto_remember=False, permission_mode="bypass", max_steps=25, chat_fn=chat))

    def test_ssh_read_then_early_cleanup_stays_partial(self):
        # Live shape: read the file (proves existence, NOT content), try to delete the
        # dir early → Cleanup Barrier redirects, then finalize without the asserts →
        # Closure Gate asks once → honest partial, runtime-owned status, no false claim.
        chat = _SeqChat([
            _call("ssh_read", host="home-srv01", path=_SNAP),
            _call("ssh_run_ps", host="home-srv01", script=f'Remove-Item -Recurse -Force "{_DIR}"'),
            _final("Готово. Все frontend и SSH критерии подтверждены verifier'ом. COMPLETED."),
        ], _final("COMPLETED"))
        evs = self._run(chat, "cb-partial")
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done.get("completion_status"), "partial")
        # the barrier fired (a delete was refused with a redirect)
        self.assertTrue(any(e.get("type") == "tool_call" and e.get("tool") == "ssh_run_ps"
                            and e.get("ok") is False and "не удаляй" in str(e.get("result", "")).lower()
                            for e in evs))
        final = [e for e in evs if e.get("type") == "final_response"][-1]["text"]
        self.assertNotIn("COMPLETED", final)                        # gated
        self.assertNotIn("критерии подтверждены", final.lower())    # universal claim gated
        self.assertIn("Готовность задачи — по verifier", final)     # runtime report
        self.assertIn("Не подтверждено verifier", final)

    def test_happy_path_full_verify_then_cleanup_confirmed(self):
        chat = _SeqChat([
            _call("ssh_exists", host="home-srv01", path=_DIR),
            _call("ssh_read", host="home-srv01", path=_SNAP),
            _call("ssh_assert_contains", host="home-srv01", path=_SNAP, pattern="project=frontend-global-live"),
            _call("ssh_assert_contains", host="home-srv01", path=_SNAP, pattern="status=ok"),
            _call("ssh_assert_contains", host="home-srv01", path=_SNAP, pattern="dom=verified"),
            _call("ssh_run_ps", host="home-srv01", script=f'Remove-Item -Recurse "{_DIR}"'),
            _call("ssh_not_exists", host="home-srv01", path=_DIR),
            _final("Отчёт: создал стенд, проверил, очистил."),
        ], _final())
        evs = self._run(chat, "cb-happy")
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done.get("completion_status"), "confirmed")
        crits = done.get("criteria") or []
        self.assertTrue(crits and all(c["status"] == "confirmed" for c in crits))
        # cleanup was NOT blocked (all non-cleanup criteria were closed first)
        self.assertFalse(any(e.get("type") == "tool_call" and "не удаляй" in str(e.get("result", "")).lower()
                             for e in evs))
        final = [e for e in evs if e.get("type") == "final_response"][-1]["text"]
        self.assertIn("Все обязательные критерии подтверждены verifier", final)   # runtime report, confirmed


if __name__ == "__main__":
    unittest.main()
