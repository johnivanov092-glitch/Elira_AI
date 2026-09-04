# -*- coding: utf-8 -*-
"""Regressions for the four runtime defects reproduced by the real Mini CRM run
(.agent/runs/10e92241fa774df18f1aa415385a64c8).

D1  write_file failure was recorded as success (ok=true on an encoding ERROR);
    an existing ASCII file must promote to UTF-8 (no BOM) for Unicode content.
D2  the verifier confirmed «тесты проходят» from `npm create vite@latest`
    (substring "test" inside "latest"; kind-"any" verdicts confirming specific
    criteria).
D3  the delivery session stopped after an `answer` terminal with 0/N confirmed
    criteria, real slice progress and an open durable checklist — instead of a
    bounded continuation toward verification.
D4  todo_update hid WHICH update id was missing (bare `ERROR: item_not_found`).
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool  # noqa: E402
from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS  # noqa: E402
from app.application.code_agent.delivery_session import stream_delivery_session  # noqa: E402
from app.application.code_agent.taskspec import (  # noqa: E402
    CriteriaTracker,
    _command_kind,
    _run_bash_verdict,
    derive_task_spec,
)
from app.application.tool_providers import ToolRegistry  # noqa: E402
from app.application.tool_providers.builtin import BuiltinToolProvider  # noqa: E402

_AUTO_SPEC = {
    "permission": "auto", "max_output_chars": 50000, "policy_classified": True,
    "enabled": True, "side_effect": True, "timeout_seconds": 60,
}


def _exec_builtin(root: Path, tool: str, args: dict, run_id: str = "crm-fix-run"):
    """The REAL path: ToolExecutionRequest -> executor -> BuiltinToolProvider."""
    provider = BuiltinToolProvider(root)
    req = ToolExecutionRequest(
        run_id=run_id, agent_id="code-agent", project_scope_id="",
        tool_name=tool, args=args, source="code_agent",
        permission_mode="bypass",
    )
    with patch("app.application.tool_registry.runtime.get_tool", return_value=dict(_AUTO_SPEC)):
        return execute_tool(req, provider.dispatch)


# ── D1: write_file honesty + ASCII→UTF-8 promotion ───────────────────────────

class WriteFileHonestyTest(unittest.TestCase):
    def test_unicode_into_existing_ascii_file_promotes_to_utf8_no_bom(self):
        """The REAL App.css failure: existing ASCII file + em-dash content must
        succeed as UTF-8 (no BOM, LF), not fail — and never fail silently."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.css").write_bytes(b"/* base */\nbody { margin: 0; }\n")
            content = "/* header — primary */\nbody { margin: 0; }\n"
            res = _exec_builtin(root, "write_file", {"path": "App.css", "content": content})
            self.assertEqual(res.status, "ok", res.output.get("text"))
            self.assertNotIn("ERROR", str(res.output.get("text")))
            self.assertNotEqual(res.output.get("ok"), False)
            raw = (root / "App.css").read_bytes()
            self.assertEqual(raw, content.encode("utf-8"), "bytes must be UTF-8, LF")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "no BOM")

    def test_write_failure_reports_ok_false_and_stable_error(self):
        """A real write failure must surface as status=error / ok=False /
        state_changed-incompatible — never as a success event."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.txt").write_text("old", encoding="utf-8")
            with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
                res = _exec_builtin(root, "write_file", {"path": "x.txt", "content": "new"})
            self.assertEqual(res.status, "error")
            self.assertIs(res.output.get("ok"), False)
            self.assertTrue(res.output.get("error"), "stable error code required")
            self.assertIn("ERROR", str(res.output.get("text")))
            from app.application.code_agent.loop_helpers import tool_state_changed
            self.assertFalse(tool_state_changed(
                "write_file", res.output, exec_ok=res.status == "ok"))
            self.assertEqual((root / "x.txt").read_text(encoding="utf-8"), "old")

    def test_existing_file_read_failure_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "x.txt"
            target.write_text("ORIGINAL", encoding="utf-8")
            with patch("pathlib.Path.read_bytes", side_effect=OSError("access denied")):
                res = _exec_builtin(
                    root,
                    "write_file",
                    {"path": "x.txt", "content": "REPLACED"},
                )
            self.assertEqual(res.status, "error")
            self.assertIs(res.output.get("ok"), False)
            self.assertEqual(res.output.get("error"), "read_failed")
            self.assertNotIn("access denied", str(res.output.get("text")).lower())
            self.assertEqual(target.read_text(encoding="utf-8"), "ORIGINAL")

    def test_edit_file_explicit_failures_carry_ok_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            missing = _exec_builtin(root, "edit_file",
                                    {"path": "nope.py", "old_string": "x", "new_string": "y"})
            self.assertIs(missing.output.get("ok"), False)
            self.assertTrue(missing.output.get("error"))
            notfound = _exec_builtin(root, "edit_file",
                                     {"path": "a.py", "old_string": "zzz", "new_string": "y"})
            self.assertIs(notfound.output.get("ok"), False)
            self.assertTrue(notfound.output.get("error"))

    def test_read_file_content_starting_with_error_stays_success(self):
        """No blanket text-based failure rule: reading a file whose CONTENT
        starts with `ERROR:` is a legitimate success."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "log.txt").write_text("ERROR: this is just file content\n", encoding="utf-8")
            res = _exec_builtin(root, "read_file", {"path": "log.txt"})
            self.assertEqual(res.status, "ok")
            self.assertNotEqual(res.output.get("ok"), False)
            self.assertIn("this is just file content", str(res.output.get("text")))


# ── D2: verifier kind matching ───────────────────────────────────────────────

def _tracker(criterion: str) -> CriteriaTracker:
    spec = derive_task_spec(f"Цель:\nCRM.\n\nКритерии готовности:\n1. {criterion}\n")
    assert spec is not None
    return CriteriaTracker.from_spec(spec)


def _record_bash(
    crit: CriteriaTracker,
    command: str,
    exit_code: int = 0,
    output: str = "out",
) -> bool:
    return crit.record(
        tool_name="run_bash", args={"command": command}, ok=exit_code == 0,
        evidence=f"$ {command}\nexit={exit_code}\nSTDOUT:\n{output}",
        meta={"exit_code": exit_code},
    )


class VerifierKindMatchingTest(unittest.TestCase):
    def test_latest_is_not_a_test_command(self):
        self.assertNotEqual(
            _command_kind("npm create vite@latest . -- --template react-ts"), "test")

    def test_real_test_commands_still_classify(self):
        for cmd in (
            "npm test",
            "npm run test",
            "npx vitest run",
            "jest",
            "pytest -q",
            "go test ./...",
            "node app.test.js",
            'cmd /c "npm test"',
            'powershell -NoProfile -Command "pytest -q"',
        ):
            self.assertEqual(_command_kind(cmd), "test", cmd)
            self.assertIsNotNone(_run_bash_verdict(cmd), cmd)

    def test_non_executing_test_modes_are_not_verification(self):
        for cmd in (
            "pytest --collect-only",
            "python -m pytest --co",
            "cargo test --no-run",
            "go test -run ^$ ./...",
            "npx jest --listTests",
        ):
            self.assertIsNone(_run_bash_verdict(cmd), cmd)

    def test_vite_scaffold_does_not_confirm_tests_pass(self):
        """The REAL false evidence: `npm create vite@latest` exit 0 confirmed
        «тесты проходят». It must stay unconfirmed."""
        crit = _tracker("тесты проходят")
        _record_bash(crit, "npm create vite@latest . -- --template react-ts 2>&1")
        self.assertEqual(crit.items[0]["status"], "unconfirmed",
                         crit.items[0].get("evidence"))

    def test_test_named_path_is_not_a_test_verdict(self):
        for cmd in (
            "mkdir test",
            "mkdir pytest",
            "echo test",
            "echo pytest",
            "type test",
            "rm -rf test",
        ):
            self.assertIsNone(_run_bash_verdict(cmd), cmd)
            crit = _tracker("тесты проходят")
            _record_bash(crit, cmd)
            self.assertEqual(crit.items[0]["status"], "unconfirmed", cmd)

    def test_dependency_install_with_test_packages_does_not_confirm_tests(self):
        """The live Test 123 false evidence: installing jest/testing-library is
        dependency mutation, not execution of the test suite."""
        cmd = (
            "npm install -D @testing-library/react @testing-library/jest-dom "
            "@testing-library/user-event jest @types/jest ts-jest jsdom"
        )
        self.assertEqual(_command_kind(cmd), "any")
        crit = _tracker("тесты проходят")
        _record_bash(crit, cmd)
        self.assertEqual(crit.items[0]["status"], "unconfirmed")

    def test_wrapped_dependency_install_does_not_confirm_tests(self):
        cmd = 'cmd /c "npm install -D jest ts-jest @testing-library/react"'
        self.assertEqual(_command_kind(cmd), "any")
        crit = _tracker("тесты проходят")
        _record_bash(crit, cmd)
        self.assertEqual(crit.items[0]["status"], "unconfirmed")

    def test_install_then_real_test_uses_the_executed_test_segment(self):
        crit = _tracker("тесты проходят")
        _record_bash(crit, "npm install --silent && npm test")
        self.assertEqual(crit.items[0]["status"], "confirmed")

    def test_green_test_run_confirms_and_red_fails(self):
        crit = _tracker("тесты проходят")
        _record_bash(crit, "npx vitest run")
        self.assertEqual(crit.items[0]["status"], "confirmed")
        crit2 = _tracker("тесты проходят")
        _record_bash(crit2, "npm test", exit_code=1)
        self.assertEqual(crit2.items[0]["status"], "failed")

    def test_build_and_test_do_not_cross_confirm(self):
        build_crit = _tracker("сборка проходит")
        _record_bash(build_crit, "npm test")
        self.assertEqual(build_crit.items[0]["status"], "unconfirmed")
        test_crit = _tracker("тесты проходят")
        _record_bash(test_crit, "npm run build")
        self.assertEqual(test_crit.items[0]["status"], "unconfirmed")
        _record_bash(build_crit, "npm run build")
        self.assertEqual(build_crit.items[0]["status"], "confirmed")

    def test_any_kind_verdict_does_not_confirm_specific_criterion(self):
        """A generic green command (kind 'any') must not confirm a SPECIFIC
        typecheck/test criterion; a specific check still satisfies an 'any'
        criterion («доступные проверки проходят»)."""
        ty = _tracker("typecheck проходит")
        _record_bash(ty, "npm run check")     # generic check verb → kind any
        self.assertEqual(ty.items[0]["status"], "unconfirmed")
        anyc = _tracker("доступные проверки проходят")
        _record_bash(anyc, "npm run typecheck")
        self.assertEqual(anyc.items[0]["status"], "confirmed")


class TaskSpecWorkflowBoundaryTest(unittest.TestCase):
    def test_numbered_singular_readiness_heading_bounds_long_spec(self):
        task = """1. ОБЩАЯ ЗАДАЧА
Создай коммерческий сайт.
- локальные LLM;
- корпоративный RAG;

14. АНИМАЦИИ
- reveal on scroll;
- prefers-reduced-motion.

19. КРИТЕРИЙ ГОТОВНОСТИ
- сайт запускается;
- npm run build завершается успешно.
"""
        spec = derive_task_spec(task)
        self.assertIsNotNone(spec)
        self.assertEqual(
            spec.success_criteria,
            ["сайт запускается;", "npm run build завершается успешно."],
        )
        self.assertIn("локальные LLM;", spec.details)
        self.assertIn("reveal on scroll;", spec.details)

    def test_work_order_after_criteria_does_not_become_more_criteria(self):
        task = """Цель:
Сделать Mini CRM.

Критерии готовности:
- приложение запускается
- тесты проходят

Порядок работы:
1. Осмотри текущую папку и существующий стек.
2. Сразу реализуй приложение целиком.
3. Запусти тесты и самостоятельно исправь ошибки.
4. Запусти приложение и сообщи локальный URL.
Не спрашивай о мелких деталях.
"""
        spec = derive_task_spec(task)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.success_criteria, ["приложение запускается", "тесты проходят"])
        details = " ".join(spec.details).lower()
        self.assertIn("осмотри текущую папку", details)
        self.assertIn("локальный url", details)


class NamedBehaviorEvidenceTest(unittest.TestCase):
    def test_plain_green_summary_does_not_claim_crud_behavior(self):
        crit = _tracker("клиент создаётся, редактируется, ищется и удаляется")
        _record_bash(crit, "npm test", output="Tests: 24 passed, 24 total")
        self.assertEqual(crit.items[0]["status"], "unconfirmed")

    def test_all_named_passed_operations_confirm_crud_behavior(self):
        crit = _tracker("клиент создаётся, редактируется, ищется и удаляется")
        output = """
PASS src/App.test.tsx
  ✓ creates a new client
  ✓ edits an existing client
  ✓ searches clients by name
  ✓ deletes a client with confirmation
Tests: 4 passed, 4 total
"""
        _record_bash(crit, "npm test -- --verbose", output=output)
        self.assertEqual(crit.items[0]["status"], "confirmed")

    def test_missing_named_operation_keeps_behavior_unconfirmed(self):
        crit = _tracker("клиент создаётся, редактируется, ищется и удаляется")
        output = """
  ✓ creates a new client
  ✓ edits an existing client
  ✓ deletes a client
Tests: 3 passed, 3 total
"""
        _record_bash(crit, "npm test -- --verbose", output=output)
        self.assertEqual(crit.items[0]["status"], "unconfirmed")

    def test_named_deal_task_and_persistence_checks_are_supported(self):
        cases = [
            (
                "сделка создаётся и меняет этап",
                "  ✓ adds a deal linked to a client\n  ✓ changes deal stage\n",
            ),
            (
                "задача создаётся и завершается",
                "  ✓ adds a task linked to a client\n  ✓ completes a task\n",
            ),
            (
                "данные переживают перезагрузку",
                "  ✓ loads existing data from localStorage after reload\n",
            ),
        ]
        for text, output in cases:
            with self.subTest(text=text):
                crit = _tracker(text)
                _record_bash(crit, "npm test -- --verbose", output=output)
                self.assertEqual(crit.items[0]["status"], "confirmed")

    def test_focused_tests_criterion_is_a_test_check(self):
        crit = _tracker("добавлены focused-тесты для хранилища и основных CRUD-операций")
        _record_bash(crit, "npm test", output="Tests: 24 passed, 24 total")
        self.assertEqual(crit.items[0]["status"], "confirmed")


# ── D3: delivery continues after answer/unverified with open checklist ───────

_REAL_MONOTONIC = time.monotonic


class _Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return _REAL_MONOTONIC() + self.offset


_CLOCK = _Clock()

_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]


@contextlib.contextmanager
def _loop_env():
    _CLOCK.offset = 0.0
    with patch.object(agent_loop, "_resolve_code_route", return_value=("test-model", 32768, None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_runtime_tool_registry", return_value=ToolRegistry([])), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch.object(agent_loop, "_server_url_alive", return_value=True), \
         patch.object(agent_loop, "_run_owned_servers", return_value=[]), \
         patch.object(agent_loop.time, "monotonic", _CLOCK):
        yield


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


class _ScriptChat:
    def __init__(self, steps, fallback=None):
        self.steps = list(steps)
        self.fallback = fallback or _final()
        self.tool_calls = 0

    def __call__(self, **kw):
        if not kw.get("tools"):
            return _final("сводка")
        idx = self.tool_calls
        self.tool_calls += 1
        entry = self.steps[idx] if idx < len(self.steps) else self.fallback
        return entry


_STRUCTURED_TASK = (
    "Собери модуль.\n\nЦель:\nПодготовить файлы.\n\n"
    "Критерии готовности:\n1. создан файл out.txt\n"
)


def _run(chat, tmp, rid):
    return list(stream_delivery_session(
        user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
        chat_fn=chat, run_id=rid,
        auto_remember=False, permission_mode="bypass",
    ))


def _dones(events):
    return [e for e in events if e.get("type") == "done"]


def _continuings(events):
    return [e for e in events if e.get("type") == "delivery_continuing"]


# ── D4: todo_update names the missing id ─────────────────────────────────────

class TodoUpdateMissingIdTest(unittest.TestCase):
    def test_missing_id_is_named_and_state_untouched(self):
        from app.application.code_agent.tools._meta import tool_todo_update
        from app.application.task_planner.service import list_checklist, todo_update
        rid = "crm-todo-1"
        ids = ["init", "deps", "types", "store", "components", "pages",
               "tests", "typecheck", "build", "run"]
        todo_update(run_id=rid, items=[
            {"id": i, "text": f"шаг {i}", "status": "pending", "position": n}
            for n, i in enumerate(ids)
        ])
        before = [(r["id"], r["status"]) for r in list_checklist(rid)["items"]]
        res = tool_todo_update(run_id=rid, updates=[
            {"id": "pages", "status": "completed"},
            {"id": "css", "status": "in_progress"},
        ])
        self.assertIs(res.get("ok"), False)
        text = str(res.get("text"))
        self.assertIn("item_not_found: css", text, text)
        for known in ("pages", "tests", "build"):
            self.assertIn(known, text, "existing ids must be listed")
        after = [(r["id"], r["status"]) for r in list_checklist(rid)["items"]]
        self.assertEqual(after, before, "atomic: the valid update must NOT apply")
        # after adding the missing item explicitly, its update works
        todo_update(run_id=rid, items=[
            {"id": "css", "text": "стили", "status": "pending", "position": 10}])
        res2 = tool_todo_update(run_id=rid, updates=[{"id": "css", "status": "in_progress"}])
        self.assertNotEqual(res2.get("ok"), False)
        rows = {r["id"]: r["status"] for r in list_checklist(rid)["items"]}
        self.assertEqual(rows["css"], "in_progress")
        self.assertEqual(rows["pages"], "pending", "earlier atomic refusal stayed atomic")


if __name__ == "__main__":
    unittest.main()
