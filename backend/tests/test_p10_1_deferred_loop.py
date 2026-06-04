"""P10.1 commit 3 — code-agent loop wired to deferred tool mode (expanded base).

Integration over stream_code_agent: base-only schemas at start (core read/edit/
shell tools + tool_search), long-tail tools hidden until tool_search activates
them (run_id auto-injected), executor blocking guessed unactivated tools,
side-effect (non-base) tools not auto-activated, base side-effect tools still
gated by the executor, and clear_run on every terminal exit. Model routing /
preflight / MCP / registry schemas are stubbed for determinism.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS  # noqa: E402
from app.application.agent_kernel import deferred_tools  # noqa: E402
from app.application.agent_kernel import executor as ex  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402


_LONG_TAIL = ("web_search", "web_fetch", "sandbox_run", "sandbox_reset")


def _schema(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


_FAKE_SCHEMAS = [_schema(n) for n in (list(_CODE_AGENT_BASE_TOOLS) + list(_LONG_TAIL))]


def _final(text="done"):
    return {"message": {"content": text, "tool_calls": []}}


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _match(name, *, side_effect):
    return {"name": name, "activatable": True, "side_effect": side_effect, "category": "x",
            "source": "builtin", "scopes": [], "permission": "auto", "reason": None, "description": ""}


class ScriptedChat:
    def __init__(self, responses):
        self._responses = responses
        self._i = 0
        self.tools_per_call: list[list[str]] = []

    def __call__(self, *, model, messages, tools, options):
        self.tools_per_call.append([(t.get("function") or {}).get("name") for t in (tools or [])])
        resp = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        if isinstance(resp, Exception):
            raise resp
        return resp


@contextlib.contextmanager
def _loop_env():
    """Isolate stream_code_agent from routing / preflight / MCP / registry I/O."""
    with patch.object(agent_loop, "_resolve_code_route", return_value=("test-model", 16384, None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


def _run(chat, *, run_id, auto_remember=False):
    with tempfile.TemporaryDirectory() as tmp:
        return list(agent_loop.stream_code_agent(
            user_message="do it",
            project_root=tmp,
            run_id=run_id,
            auto_remember=auto_remember,
            chat_fn=chat,
        ))


class DeferredLoopTest(unittest.TestCase):
    def tearDown(self):
        for rid in ("r1", "r2", "rTodo", "r3", "r4", "r5", "rE", "r6a", "r6b", "r6c"):
            deferred_tools.clear_run(rid)

    def test_initial_schemas_are_expanded_base_only(self):
        chat = ScriptedChat([_final()])
        with _loop_env():
            _run(chat, run_id="r1")
        first = set(chat.tools_per_call[0])
        self.assertEqual(first, set(_CODE_AGENT_BASE_TOOLS) | {"tool_search"})
        for lt in _LONG_TAIL:
            self.assertNotIn(lt, first)
        for core in ("write_file", "edit_file", "run_bash"):
            self.assertIn(core, first)

    def test_tool_search_called_with_injected_run_id(self):
        chat = ScriptedChat([_call("tool_search", query="web"), _final()])
        spy_result = {"ok": True, "text": "ok", "matches": [], "activated": []}
        with _loop_env(), patch("app.application.code_agent.tools.tool_search",
                                return_value=spy_result) as spy:
            _run(chat, run_id="r2")
        self.assertTrue(spy.called)
        self.assertEqual(spy.call_args.kwargs.get("run_id"), "r2")
        self.assertEqual(spy.call_args.kwargs.get("query"), "web")

    def test_todo_update_called_with_injected_run_id(self):
        chat = ScriptedChat([_call("todo_update", updates=[{"id": "a", "status": "completed"}]), _final()])
        with _loop_env(), patch.object(
            agent_loop,
            "_kernel_exec",
            return_value=SimpleNamespace(output={"text": "ok"}),
        ) as spy:
            _run(chat, run_id="rTodo")
        req = spy.call_args.args[0]
        self.assertEqual(req.tool_name, "todo_update")
        self.assertEqual(req.args["run_id"], "rTodo")

    def test_activated_long_tail_tool_visible_next_step(self):
        chat = ScriptedChat([_call("tool_search", query="web"), _final()])
        with _loop_env(), patch("app.application.tool_registry.runtime.search_tool_specs",
                                return_value=[_match("web_search", side_effect=False)]):
            _run(chat, run_id="r3")
        self.assertNotIn("web_search", chat.tools_per_call[0])   # hidden at start
        self.assertIn("web_search", chat.tools_per_call[1])      # visible after activation

    def test_guessed_unactivated_tool_blocked_before_dispatch(self):
        chat = ScriptedChat([_call("web_search", query="x"), _final()])  # long-tail, not activated
        classified = {
            "name": "web_search", "policy_classified": True, "enabled": True,
            "permission": "auto", "scopes": [], "max_output_chars": 50000,
        }
        with _loop_env(), \
             patch("app.application.tool_registry.runtime.get_tool", return_value=classified), \
             patch.object(ToolRegistry, "dispatch_raw") as dispatch_spy:
            events = _run(chat, run_id="r4")
        self.assertFalse(dispatch_spy.called)  # provider never reached
        tc = [e for e in events if e.get("type") == "tool_call" and e.get("tool") == "web_search"]
        self.assertEqual(len(tc), 1)
        self.assertIn("not activated", tc[0]["result"].lower())

    def test_tool_search_does_not_activate_side_effect_tool(self):
        chat = ScriptedChat([_call("tool_search", query="sandbox"), _final()])
        with _loop_env(), patch("app.application.tool_registry.runtime.search_tool_specs",
                                return_value=[_match("sandbox_run", side_effect=True)]):
            _run(chat, run_id="r5")
        # side-effect tool was never activated -> still hidden on the next step
        self.assertNotIn("sandbox_run", chat.tools_per_call[1])

    def test_base_side_effect_tool_still_enforced_by_executor(self):
        from app.application.agent_registry.sandbox import _make_error

        deferred_tools.enable_deferred_tools("rE", _CODE_AGENT_BASE_TOOLS)
        self.addCleanup(deferred_tools.clear_run, "rE")
        self.assertTrue(deferred_tools.is_tool_active("rE", "run_bash"))  # base => active/visible

        spec = {"name": "run_bash", "policy_classified": True, "enabled": True,
                "permission": "auto", "scopes": [], "max_output_chars": 50000}
        req = ex.ToolExecutionRequest(run_id="rE", agent_id="code-agent", project_scope_id="p",
                                      tool_name="run_bash", args={"command": "x"}, source="code_agent")
        dispatched = {"called": False}

        def dispatch_fn(name, args):
            dispatched["called"] = True
            return {"ok": True}

        block = _make_error(agent_id="code-agent", reason="rate_limit_exceeded", message="blocked")
        with patch("app.application.tool_registry.runtime.get_tool", return_value=spec), \
             patch("app.application.agent_registry.sandbox.preflight_or_raise", side_effect=block), \
             patch.object(ex, "_emit_blocked"):
            result = ex.execute_tool(req, dispatch_fn)
        # Base + active, yet executor policy still blocks it before dispatch.
        self.assertFalse(dispatched["called"])
        self.assertEqual(result.status, "blocked")

    def test_clear_run_on_success_error_and_max_steps(self):
        with _loop_env():
            _run(ScriptedChat([_final()]), run_id="r6a")
            self.assertFalse(deferred_tools.is_deferred_run("r6a"))

            _run(ScriptedChat([RuntimeError("boom")]), run_id="r6b")
            self.assertFalse(deferred_tools.is_deferred_run("r6b"))

            # never emits a final answer -> loop exhausts max_steps -> finally
            _run(ScriptedChat([_call("tool_search", query="x")]), run_id="r6c")
            self.assertFalse(deferred_tools.is_deferred_run("r6c"))

    def test_non_code_agent_run_is_never_deferred(self):
        # Nothing outside stream_code_agent opts a run into deferred mode, so the
        # chat path is unaffected (executor gate stays inert for its runs).
        self.assertFalse(deferred_tools.is_deferred_run("chat-run-xyz"))


if __name__ == "__main__":
    unittest.main()
