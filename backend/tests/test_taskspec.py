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
        self.assertIsNotNone(done.get("task_spec"))          # spec carried to the UI
        self.assertFalse(done.get("criteria_confirmed"))     # никакой verifier не прошёл
        self.assertEqual(len([e for e in evs if e.get("type") == "step_started"]), 2)
        final = [e for e in evs if e.get("type") == "final_response"][-1]
        self.assertIn("критерии не подтверждены verifier", final["text"])

    def test_passing_verifier_marks_criteria_confirmed(self):
        # A passing ssh_assert_not_contains confirms a criterion → done flags it.
        chat = _SeqChat([
            _call("write_file", path="a.ps1", content="x"),
            _call("ssh_assert_not_contains", host="home-srv01", path="C:\\a.ps1", pattern="Content-Length"),
            _final("готово, проверено"),
        ], _final())

        def _exec(request, **kw):
            tool = getattr(request, "tool_name", "")
            if "assert" in str(tool):
                return SimpleNamespace(status="ok", output={"text": "OK", "ok": True, "touched_host": "home-srv01"})
            return SimpleNamespace(status="ok", output={"text": "ok", "ok": True, "touched_path": "a.ps1"})

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            evs = list(agent_loop.stream_code_agent(
                user_message=_STRUCTURED, project_root=tmp, run_id="ts-confirmed",
                auto_remember=False, permission_mode="bypass", max_steps=20, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")
        self.assertTrue(done.get("criteria_confirmed"))


if __name__ == "__main__":
    unittest.main()
