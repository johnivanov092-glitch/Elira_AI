"""Near-duplicate loop guard — live-observed regression.

A model spamming ONE tool with slightly-varying args (`ping -n 1 1`, `ping -n 2 1`,
… — seen live running to step 50 / ~59 calls, and `recall "192.1 88"` / "192.169 88"
churn) evades the exact-fingerprint guard because each variant is a distinct
fingerprint. The near-dup guard collapses these and cuts the spin in ~6 calls, and
nudges the model to change approach first. Legit work — `read_file` over DIFFERENT
files — has low token overlap and must NOT be flagged.
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
from app.application.code_agent.loop_helpers import (  # noqa: E402
    _NEAR_DUP_LIMIT,
    _arg_tokens,
    _is_near_dup,
    _jaccard,
)
from app.application.agent_kernel import deferred_tools  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402


class HelperTest(unittest.TestCase):
    def test_numbers_normalized_so_ping_variants_collapse(self):
        a = _arg_tokens({"command": "ping -n 1 192.168.88.1"})
        b = _arg_tokens({"command": "ping -n 2 192.168.88.1"})
        self.assertGreaterEqual(_jaccard(a, b), 0.6)  # same shape → near-dup
        self.assertTrue(_is_near_dup("run_bash", a, [("run_bash", b)]))

    def test_recall_query_churn_collapses(self):
        a = _arg_tokens({"query": "192.1 88 роутер"})
        b = _arg_tokens({"query": "192.169.88.2 192 88 роутер"})
        self.assertGreaterEqual(_jaccard(a, b), 0.6)

    def test_different_files_are_not_near_dup(self):
        a = _arg_tokens({"path": "src/main.py"})
        b = _arg_tokens({"path": "src/utils.py"})
        self.assertLess(_jaccard(a, b), 0.6)
        self.assertFalse(_is_near_dup("read_file", a, [("read_file", b)]))

    def test_different_tool_never_near_dup(self):
        a = _arg_tokens({"command": "ping -n 1 1"})
        self.assertFalse(_is_near_dup("run_bash", a, [("recall", a)]))


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
    """Returns a scripted response per step; a fallback for any extra steps."""
    def __init__(self, responses, fallback):
        self._r = responses
        self._fb = fallback
        self._i = 0

    def __call__(self, **kw):
        r = self._r[self._i] if self._i < len(self._r) else self._fb
        self._i += 1
        return r


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


class NearDupLoopTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("nd-ping", "nd-files"):
            deferred_tools.clear_run(rid)

    def test_ping_churn_is_cut_fast(self):
        # 20 pings with varying args — must stop near _NEAR_DUP_LIMIT, not run to 20.
        pings = [_call("run_bash", command=f"ping -n {i} 192.168.88.1") for i in range(1, 21)]
        chat = _SeqChat(pings, pings[-1])
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(status="ok", output={"text": "timeout", "ok": True})):
            evs = list(agent_loop.stream_code_agent(
                user_message="просканируй сеть", project_root=tmp, run_id="nd-ping",
                auto_remember=False, permission_mode="bypass", max_steps=40, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "loop_guard")
        self.assertIn("near-duplicate", done["error"])
        self.assertLessEqual(done["steps"], _NEAR_DUP_LIMIT + 2)  # ~6, not 20

    def test_reading_different_files_is_not_flagged(self):
        # 6 reads of DIFFERENT files then finalize — must NOT trip the near-dup guard.
        reads = [_call("read_file", path=f"src/{n}.py") for n in ("main", "utils", "config", "models", "views", "urls")]
        chat = _SeqChat(reads + [_final("прочитал всё")], _final())
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec",
                          return_value=SimpleNamespace(status="ok", output={"text": "code...", "ok": True})):
            evs = list(agent_loop.stream_code_agent(
                user_message="изучи проект", project_root=tmp, run_id="nd-files",
                auto_remember=False, permission_mode="bypass", max_steps=40, chat_fn=chat,
            ))
        done = [e for e in evs if e.get("type") == "done"][-1]
        self.assertEqual(done["stop_reason"], "answer")
        self.assertNotIn("near-duplicate", str(done.get("error")))


if __name__ == "__main__":
    unittest.main()
