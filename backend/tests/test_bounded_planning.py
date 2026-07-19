# -*- coding: utf-8 -*-
"""Bounded planning → execution → verification for «Мозг» (thinking=true).

Contract (batch spec) — red→green set A…J:
  A. only the planner model call gets enable_thinking=true; all execution/
     verification calls run thinking OFF;
  B. the planner runs at most ONCE per run (incl. Resume / auto-continuation);
  C. a side-effect tool cannot run during planning;
  D. invalid/timeout planner → fallback → execution continues thinking OFF;
  E. the PlanArtifact is durable — Resume reuses it, no second planning;
  F. any window (64K…512K) uses the same algorithm / the real effective ctx —
     no size list, no frontend caps (planning adds no context constant);
  G. structured Mini-CRM benchmark: plan → implement → typecheck/test/build →
     verifier-confirmed; answer does not finish before the checks;
  H. repeated failed edit_file is not progress and the recovery is a precise
     diagnostic (not the generic "read the file" when it was already read);
  I. thinking=false regression: normal execution is unchanged;
  J. Stop during planning ends the run immediately (no execution, durable
     cancelled terminal).
"""
from __future__ import annotations

import contextlib
import concurrent.futures
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS, stream_code_agent  # noqa: E402
from app.application.code_agent.planning import (  # noqa: E402
    PlanArtifact,
    error_fingerprint,
    parse_plan_from_text,
    plan_artifact_from_dict,
    recovery_hint,
)
from app.application.code_agent.run_journal import RunJournal  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402

_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]

_STRUCTURED_TASK = (
    "Собери модуль.\n\nЦель:\nПодготовить файлы.\n\n"
    "Критерии готовности:\n1. создан файл out.txt\n"
)
_SIMPLE_TASK = "поправь мелочь"

_PLAN_JSON = (
    '{"goal":"собрать модуль","current_state":"пусто",'
    '"ordered_steps":["создать out.txt","проверить"],'
    '"acceptance_checks":["out.txt существует"],"risks":["нет"],"current_step":1}'
)


@contextlib.contextmanager
def _loop_env():
    with patch.object(agent_loop, "_resolve_code_route",
                      side_effect=lambda model, num_ctx, agent_id="code-agent": ("test-model", int(num_ctx or 0), None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch.object(agent_loop, "_server_url_alive", return_value=True), \
         patch.object(agent_loop, "_run_owned_servers", return_value=[]), \
         patch.object(agent_loop, "_stop_run_servers", return_value=[]), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


def _thinking_on(options: dict) -> bool:
    ctk = (options or {}).get("chat_template_kwargs") or {}
    return bool(ctk.get("enable_thinking"))


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


class _RecordingChat:
    """Records every model call's options + whether it was a tool call. The
    planner call is a NO-tools call; execution calls pass tools."""

    def __init__(self, plan_reply, steps, fallback=None):
        self.plan_reply = plan_reply
        self.steps = list(steps)
        self.fallback = fallback or _final()
        self.calls = []          # (has_tools, thinking_on)
        self.call_kwargs = []
        self.tool_step_i = 0

    def __call__(self, **kw):
        has_tools = bool(kw.get("tools"))
        self.calls.append((has_tools, _thinking_on(kw.get("options") or {})))
        self.call_kwargs.append(kw)
        if not has_tools:
            # planner (no tools + thinking) OR a tool-less finalize/wrap call.
            if _thinking_on(kw.get("options") or {}):
                return {"message": {"content": self.plan_reply, "tool_calls": []}}
            return _final("сводка")
        entry = self.steps[self.tool_step_i] if self.tool_step_i < len(self.steps) else self.fallback
        self.tool_step_i += 1
        return entry


def _run(chat, tmp, *, thinking, run_id, task=_STRUCTURED_TASK, resume=False):
    return list(stream_code_agent(
        user_message=task, project_root=tmp, model="test-model",
        max_steps=20, chat_fn=chat, run_id=run_id,
        num_ctx=131072, approval_wait_seconds=0, auto_remember=False,
        permission_mode="bypass", thinking=thinking, resume=resume,
    ))


def _events(evs, t):
    return [e for e in evs if e.get("type") == t]


class ThinkingScopedToPlannerTest(unittest.TestCase):
    def test_only_planner_call_has_enable_thinking(self):
        """(A) exactly the planner call carries enable_thinking; every
        execution/verification call runs thinking OFF."""
        chat = _RecordingChat(_PLAN_JSON, [
            _call("write_file", path="out.txt", content="x"),
            _final("out.txt создан"),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = _run(chat, tmp, thinking=True, run_id="plan-A")
        thinking_calls = [c for c in chat.calls if c[1]]
        self.assertEqual(len(thinking_calls), 1, "exactly one call may enable thinking")
        self.assertFalse(thinking_calls[0][0], "the thinking call must be the NO-tools planner")
        # every tool-enabled (execution) call is thinking OFF
        for has_tools, thinking_on in chat.calls:
            if has_tools:
                self.assertFalse(thinking_on, "execution/verification must be thinking OFF")
        self.assertEqual(len(_events(evs, "planning_started")), 1)
        self.assertEqual(len(_events(evs, "plan_ready")), 1)
        phases = [event.get("phase") for event in _events(evs, "phase_changed")]
        self.assertEqual(phases[0], "execution")
        self.assertIn("verification", phases)
        planner_options = next(
            (kw.get("options") or {}) for kw in chat.call_kwargs
            if _thinking_on(kw.get("options") or {})
        )
        self.assertGreater(int(planner_options.get("max_tokens") or 0), 0,
                           "the planner output must have a real per-call cap")

        first_execution = next(kw for kw in chat.call_kwargs if kw.get("tools"))
        plan_messages = [
            message for message in first_execution.get("messages") or []
            if "[ПЛАН]" in str(message.get("content") or "")
        ]
        self.assertEqual(len(plan_messages), 1)
        self.assertEqual(plan_messages[0].get("role"), "user",
                         "model-authored plan context must not be elevated to assistant/system")

    def test_thinking_false_run_never_plans(self):
        """(I) thinking=false → no planning stage, unchanged normal execution."""
        chat = _RecordingChat(_PLAN_JSON, [
            _call("write_file", path="out.txt", content="x"), _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = _run(chat, tmp, thinking=False, run_id="plan-I")
        self.assertEqual(_events(evs, "planning_started"), [])
        self.assertEqual(_events(evs, "plan_ready"), [])
        self.assertFalse(any(t for _, t in chat.calls), "no call may enable thinking")
        # No planning → NO new structural events; the existing event contract is
        # byte-for-byte unchanged for a normal run.
        self.assertEqual(_events(evs, "phase_changed"), [])

    def test_simple_task_thinking_true_does_not_plan(self):
        """A conversational/simple task (no TaskSpec) never enters planning even
        with thinking=true — planning is for STRUCTURAL tasks."""
        chat = _RecordingChat(_PLAN_JSON, [_final("ок")])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = _run(chat, tmp, thinking=True, run_id="plan-simple", task=_SIMPLE_TASK)
        self.assertEqual(_events(evs, "planning_started"), [])


class PlannerRunsOnceTest(unittest.TestCase):
    def test_planner_runs_once_across_resume(self):
        """(B)+(E) the plan is durable; Resume reuses it and never re-plans."""
        rid = "plan-once"
        chat = _RecordingChat(_PLAN_JSON, [
            _call("write_file", path="a.txt", content="A"),
            _final("частично"),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            _run(chat, tmp, thinking=True, run_id=rid)
            plan1 = RunJournal.load(rid).state.get("plan")
            self.assertIsInstance(plan1, dict)
            self.assertEqual(RunJournal.load(rid).state.get("applied_thinking_mode"),
                             "planning_then_execution")
            # Resume the SAME run with thinking still true in the request.
            resume_chat = _RecordingChat("{invalid plan}", [
                _call("write_file", path="out.txt", content="x"), _final(),
            ])
            evs2 = _run(resume_chat, tmp, thinking=True, run_id=rid, resume=True)
        # No planning on resume, plan reused, and NO call enabled thinking.
        self.assertEqual(_events(evs2, "planning_started"), [])
        self.assertEqual(_events(evs2, "plan_ready"), [])
        self.assertFalse(any(t for _, t in resume_chat.calls),
                         "resume must not enable thinking (plan reused)")
        ph = _events(evs2, "phase_changed")[0]
        self.assertEqual(ph["applied_thinking_mode"], "plan_reused")
        self.assertEqual(RunJournal.load(rid).state.get("plan"), plan1,
                         "the durable plan is unchanged")


class PlanningFallbackTest(unittest.TestCase):
    def test_invalid_planner_output_falls_back_and_executes(self):
        """(D) invalid planner reply → planning_fallback → execution thinking OFF,
        the run does not hang."""
        chat = _RecordingChat("не json, просто болтовня", [
            _call("write_file", path="out.txt", content="x"),
            _final("сделано"),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = _run(chat, tmp, thinking=True, run_id="plan-D")
        self.assertEqual(len(_events(evs, "planning_started")), 1)
        self.assertEqual(len(_events(evs, "planning_fallback")), 1)
        self.assertEqual(_events(evs, "plan_ready"), [])
        ph = _events(evs, "phase_changed")[0]
        self.assertEqual(ph["applied_thinking_mode"], "planning_fallback")
        # execution still happened and finished
        self.assertTrue(any(e.get("type") == "tool_call" for e in evs))
        self.assertTrue(_events(evs, "done"))
        # only the one planner call used thinking
        self.assertEqual(len([c for c in chat.calls if c[1]]), 1)

    def test_planner_exception_is_fallback_not_crash(self):
        """A planner that raises must degrade to fallback, never bubble up."""
        class _BoomChat(_RecordingChat):
            def __call__(self, **kw):
                self.calls.append((bool(kw.get("tools")), _thinking_on(kw.get("options") or {})))
                if not kw.get("tools") and _thinking_on(kw.get("options") or {}):
                    raise RuntimeError("planner model down")
                if not kw.get("tools"):
                    return _final("сводка")
                entry = self.steps[self.tool_step_i] if self.tool_step_i < len(self.steps) else self.fallback
                self.tool_step_i += 1
                return entry

        chat = _BoomChat(_PLAN_JSON, [_call("write_file", path="out.txt", content="x"), _final()])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = _run(chat, tmp, thinking=True, run_id="plan-boom")
        self.assertEqual(len(_events(evs, "planning_fallback")), 1)
        self.assertTrue(_events(evs, "done"))


class PlanningFallbackNoRePlanTest(unittest.TestCase):
    def test_fallback_run_does_not_replan_on_resume(self):
        """(B/4) after a planning_fallback (no stored plan), Resume must NOT run
        the planner again — the attempt is durable; no infinite re-plan."""
        rid = "plan-fb-resume"
        chat = _RecordingChat("не json", [
            _call("write_file", path="a.txt", content="A"), _final("частично"),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            _run(chat, tmp, thinking=True, run_id=rid)
            self.assertTrue(RunJournal.load(rid).state.get("planning_attempted"))
            self.assertIsNone(RunJournal.load(rid).state.get("plan"))
            resume_chat = _RecordingChat("не json", [
                _call("write_file", path="out.txt", content="x"), _final(),
            ])
            evs2 = _run(resume_chat, tmp, thinking=True, run_id=rid, resume=True)
        self.assertEqual(_events(evs2, "planning_started"), [],
                         "resume after fallback must not re-plan")
        self.assertFalse(any(t for _, t in resume_chat.calls),
                         "resume after fallback must not enable thinking")


class PlanningCancelTest(unittest.TestCase):
    def test_cancel_during_planning_ends_run_before_execution(self):
        """(J) Stop while the planner is producing → cancelled terminal, no
        execution model calls."""
        rid = "plan-J"

        class _CancelDuringPlan(_RecordingChat):
            def __call__(self, **kw):
                has_tools = bool(kw.get("tools"))
                self.calls.append((has_tools, _thinking_on(kw.get("options") or {})))
                if not has_tools and _thinking_on(kw.get("options") or {}):
                    agent_loop.request_cancel(rid)  # user Stop mid-planning
                    return {"message": {"content": _PLAN_JSON, "tool_calls": []}}
                if not has_tools:
                    return _final("сводка")
                entry = self.steps[self.tool_step_i] if self.tool_step_i < len(self.steps) else self.fallback
                self.tool_step_i += 1
                return entry

        chat = _CancelDuringPlan(_PLAN_JSON, [_call("write_file", path="out.txt", content="x"), _final()])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = _run(chat, tmp, thinking=True, run_id=rid)
        done = _events(evs, "done")
        self.assertTrue(done)
        self.assertEqual(done[-1]["stop_reason"], "cancelled")
        self.assertFalse(any(t for h, t in chat.calls if h), "no execution call may run")
        self.assertFalse(any(e.get("type") == "tool_call" for e in evs),
                         "no tool may execute after Stop in planning")

    def test_stop_interrupts_a_blocked_planner_call(self):
        """A real Stop must end the SSE run without waiting for a blocked
        synchronous planner provider call to return."""
        rid = "plan-J-blocked"
        entered = threading.Event()
        release = threading.Event()

        class _BlockedPlanner(_RecordingChat):
            def __call__(self, **kw):
                if not kw.get("tools") and _thinking_on(kw.get("options") or {}):
                    entered.set()
                    release.wait(timeout=5)
                    return {"message": {"content": _PLAN_JSON, "tool_calls": []}}
                return super().__call__(**kw)

        chat = _BlockedPlanner(_PLAN_JSON, [_final()])
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run, chat, tmp, thinking=True, run_id=rid)
            self.assertTrue(entered.wait(timeout=1), "planner call did not start")
            agent_loop.request_cancel(rid)
            try:
                events = future.result(timeout=1)
            except concurrent.futures.TimeoutError:
                release.set()
                future.result(timeout=2)
                self.fail("Stop waited for the blocked planner provider call")
            finally:
                release.set()
        done = _events(events, "done")[-1]
        self.assertEqual(done.get("stop_reason"), "cancelled")


class WindowAgnosticTest(unittest.TestCase):
    def test_same_planning_path_for_any_window(self):
        """(F) planning behaves identically at 64K/128K/256K/512K — the code
        path has no size list and adds no context constant."""
        for n_ctx in (65536, 131072, 262144, 524288):
            with self.subTest(n_ctx=n_ctx):
                chat = _RecordingChat(_PLAN_JSON, [
                    _call("write_file", path="out.txt", content="x"), _final(),
                ])
                with tempfile.TemporaryDirectory() as tmp, _loop_env():
                    evs = list(stream_code_agent(
                        user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                        max_steps=20, chat_fn=chat, run_id=f"plan-F-{n_ctx}",
                        num_ctx=n_ctx, approval_wait_seconds=0, auto_remember=False,
                        permission_mode="bypass", thinking=True,
                    ))
                self.assertEqual(len(_events(evs, "plan_ready")), 1)
                self.assertEqual(len([c for c in chat.calls if c[1]]), 1)


class BoundedRecoveryTest(unittest.TestCase):
    def test_error_fingerprint_stable_across_line_numbers(self):
        fp1 = error_fingerprint("edit_file", {"path": "src/App.tsx"},
                                {"ok": False, "error": "old_string not found (line 12)"})
        fp2 = error_fingerprint("edit_file", {"path": "src/App.tsx"},
                                {"ok": False, "error": "old_string not found (line 40)"})
        self.assertTrue(fp1)
        self.assertEqual(fp1, fp2, "shifting line number must not change the fingerprint")
        self.assertEqual(error_fingerprint("edit_file", {"path": "x"}, {"ok": True}), "",
                         "a success has no fingerprint")

    def test_repeated_edit_failure_gets_precise_diagnostic_not_generic(self):
        """(H) repeated failed edit_file (file already read) → the injected
        recovery names the file + says re-read/rewrite; never the bare
        «прочитай файл или используй примитив»."""
        captured: list = []

        def chat(**kw):
            if kw.get("tools"):
                captured.append(list(kw.get("messages") or []))
                n = len([m for m in captured])
                if n == 1:
                    return _call("read_file", path="src/App.tsx")
                if n <= 4:
                    return _call("edit_file", path="src/App.tsx",
                                 old_string="nope", new_string="y")
                return _final("не смог")
            return _final("сводка")

        def _exec(req, *a, **k):
            from types import SimpleNamespace
            if req.tool_name == "read_file":
                return SimpleNamespace(status="ok", output={
                    "ok": True, "text": "actual content", "touched_path": "src/App.tsx"})
            if req.tool_name == "edit_file":
                return SimpleNamespace(status="ok", output={
                    "ok": False, "text": "ERROR: old_string not found in src/App.tsx",
                    "error": "old_string not found in src/App.tsx"})
            return SimpleNamespace(status="ok", output={"text": "ok"})

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            evs = list(stream_code_agent(
                user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                max_steps=12, chat_fn=chat, run_id="plan-H",
                num_ctx=131072, approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", thinking=False,
            ))
        recovery = [m for msgs in captured for m in msgs
                    if m.get("role") == "user" and "[recovery]" in str(m.get("content"))]
        self.assertTrue(recovery, "a repeated failure must inject a recovery nudge")
        text = str(recovery[0]["content"])
        self.assertIn("src/App.tsx", text)
        self.assertIn("Перечитай", text)  # precise: re-read the ALREADY-read file
        self.assertNotIn("используй специализированный примитив", text)
        recovery_messages = next(
            msgs for msgs in captured
            if any("[recovery]" in str(m.get("content") or "") for m in msgs)
        )
        recovery_index = next(
            i for i, message in enumerate(recovery_messages)
            if "[recovery]" in str(message.get("content") or "")
        )
        self.assertEqual(recovery_messages[recovery_index - 1].get("role"), "tool",
                         "recovery user turn must follow the tool response")
        # repeated failed edits are NOT progress: nothing verifier-confirmed
        done = _events(evs, "done")[-1]
        self.assertNotEqual(done.get("completion_status"), "confirmed")

    def test_recovery_hint_not_generic_when_already_read(self):
        hint = recovery_hint(tool="edit_file", path="a.tsx",
                             error_text="old_string not found", times=3,
                             already_read=True, open_criterion="tsc проходит")
        self.assertIn("уже читался", hint)
        self.assertIn("tsc проходит", hint)

    def test_loop_stop_reports_a_concrete_next_step_for_the_last_failure(self):
        calls = 0

        def chat(**kw):
            nonlocal calls
            if not kw.get("tools"):
                return _final("сводка")
            calls += 1
            if calls == 1:
                return _call("read_file", path="src/App.tsx")
            return _call(
                "edit_file", path="src/App.tsx",
                old_string="missing fragment", new_string="replacement",
            )

        def _exec(req, *a, **k):
            from types import SimpleNamespace
            if req.tool_name == "read_file":
                return SimpleNamespace(status="ok", output={
                    "ok": True, "text": "actual content", "touched_path": "src/App.tsx",
                })
            return SimpleNamespace(status="error", output={
                "ok": False, "text": "ERROR: old_string not found",
                "error": "old_string_not_found",
            })

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            events = list(stream_code_agent(
                user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                max_steps=12, chat_fn=chat, run_id="plan-H-stop",
                num_ctx=131072, approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", thinking=False,
            ))
        final = _events(events, "final_response")[-1]["text"]
        next_step = final.split("Следующий безопасный шаг:", 1)[-1]
        self.assertIn("src/App.tsx", next_step)
        self.assertNotIn("смени инструмент", next_step.lower())
        self.assertNotIn("используй специализированный примитив", next_step.lower())


_CRM_TASK = (
    "Сделай мини-CRM.\n\nЦель:\nЯдро CRM с тестом.\n\n"
    "Критерии готовности:\n1. smoke-тест `python test_app.py` проходит\n"
)
_CRM_TEST = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import add, find
def run():
    if os.path.exists("crm.db"): os.remove("crm.db")
    cid = add("Ромашка")
    assert find("Ромашка")[0][0] == cid
    os.remove("crm.db")
    print("CRM OK")
if __name__ == "__main__":
    run()
"""
_CRM_STORE = """import sqlite3
def _c():
    conn = sqlite3.connect("crm.db")
    conn.execute("CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT)")
    return conn
def add(name):
    conn = _c(); cur = conn.execute("INSERT INTO clients(name) VALUES(?)", (name,)); conn.commit(); return cur.lastrowid
def find(q):
    return _c().execute("SELECT id,name FROM clients WHERE name LIKE ?", ("%"+q+"%",)).fetchall()
"""


class MiniCrmBenchmarkTest(unittest.TestCase):
    def test_plan_then_implement_then_verifier_confirmed(self):
        """(G) thinking=true structural build: plan → implement → real green
        `python test_app.py` → criterion verifier-confirmed; the answer does not
        finish before the check runs."""
        rid = "plan-G"
        chat = _RecordingChat(
            '{"goal":"CRM","current_state":"пусто",'
            '"ordered_steps":["store.py","test_app.py","прогнать тест"],'
            '"acceptance_checks":["python test_app.py проходит"],"risks":[],"current_step":1}',
            [
                _call("write_file", path="store.py", content=_CRM_STORE),
                _call("write_file", path="test_app.py", content=_CRM_TEST),
                _call("run_bash", command="python test_app.py"),
                _final("CRM готов, тест зелёный"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            evs = list(stream_code_agent(
                user_message=_CRM_TASK, project_root=tmp, model="test-model",
                max_steps=20, chat_fn=chat, run_id=rid,
                num_ctx=131072, approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", thinking=True,
            ))
            self.assertTrue((Path(tmp) / "store.py").is_file())
            self.assertTrue((Path(tmp) / "test_app.py").is_file())
        # planning happened once, thinking only for the planner
        self.assertEqual(len(_events(evs, "plan_ready")), 1)
        self.assertEqual(len([c for c in chat.calls if c[1]]), 1)
        # the verification command really ran and was GREEN
        verify = [e for e in evs if e.get("type") == "tool_call"
                  and e.get("tool") == "run_bash"
                  and "test_app.py" in str((e.get("arguments") or {}).get("command"))]
        self.assertTrue(verify and any(e.get("exit_code") == 0 for e in verify))
        # criterion verifier-confirmed — evidence, not model text
        done = _events(evs, "done")[-1]
        self.assertEqual(done.get("completion_status"), "confirmed",
                         done.get("criteria"))
        self.assertIn(
            "verification",
            [event.get("phase") for event in _events(evs, "phase_changed")],
        )
        # answer terminal only AFTER the verifier confirmed
        self.assertEqual(done.get("stop_reason"), "answer")


class PlanArtifactSchemaTest(unittest.TestCase):
    def test_validation_rejects_empty_and_accepts_full(self):
        self.assertIsNone(plan_artifact_from_dict({}))
        self.assertIsNone(plan_artifact_from_dict({"goal": "g", "ordered_steps": [], "acceptance_checks": ["x"]}))
        self.assertIsNone(plan_artifact_from_dict({"goal": "", "ordered_steps": ["a"], "acceptance_checks": ["x"]}))
        self.assertIsNone(plan_artifact_from_dict({
            "goal": "g", "ordered_steps": ["a"], "acceptance_checks": ["x"],
        }), "all declared PlanArtifact fields are required")
        self.assertIsNone(plan_artifact_from_dict({
            "goal": "g", "current_state": "s", "ordered_steps": [{"step": "a"}],
            "acceptance_checks": ["x"], "risks": [], "current_step": 1,
        }), "structured/non-string step values must fail validation")
        plan = plan_artifact_from_dict({
            "goal": "g", "current_state": "s", "ordered_steps": ["a", "b", "c"],
            "acceptance_checks": ["npm test"], "risks": ["r"], "current_step": 5,
        })
        self.assertIsNotNone(plan)
        self.assertEqual(plan.current_step, 3, "current_step clamped into range")

    def test_parse_tolerates_fence_and_prose(self):
        self.assertIsNotNone(parse_plan_from_text(
            "Вот план:\n```json\n" + _PLAN_JSON + "\n```\nготово"))
        self.assertIsNone(parse_plan_from_text("никакого json тут нет"))


if __name__ == "__main__":
    unittest.main()
