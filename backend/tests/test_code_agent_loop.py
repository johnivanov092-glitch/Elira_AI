"""Tests for the real code-agent loop and sandboxed tools."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import (  # noqa: E402
    DEFAULT_NUM_CTX,
    _extract_inline_tool_calls,
    _resolve_code_route,
    _temperature_for_role,
    get_project_prompt,
    index_project,
    recall_from_rag,
    request_cancel,
    run_code_agent,
    set_project_prompt,
    stream_code_agent,
    summarize_history,
)
from app.application.code_agent.tools import (  # noqa: E402
    SandboxError,
    build_tool_schemas,
    tool_edit_file,
    tool_glob,
    tool_grep,
    tool_project_map,
    tool_read_file,
    tool_recall,
    tool_delegate_task,
    tool_run_bash,
    tool_todo_update,
    tool_write_file,
)
from app.application.projects.scope import project_scope_id  # noqa: E402


class SandboxedToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "hello.py").write_text("print('hi')\n", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "data.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_read_file_returns_numbered_lines(self) -> None:
        out = tool_read_file(self.root, path="hello.py")
        self.assertIn("print('hi')", out["text"])
        self.assertIn("1\t", out["text"])
        self.assertEqual(out["touched_path"], "hello.py")

    def test_read_file_rejects_escape(self) -> None:
        with self.assertRaises(SandboxError):
            tool_read_file(self.root, path="../outside.txt")

    def test_write_file_creates_and_overwrites(self) -> None:
        res = tool_write_file(self.root, path="new.py", content="x = 1\n")
        self.assertIn("Created", res["text"])
        self.assertEqual(res["diff_action"], "create")
        self.assertEqual(res["old_content"], "")
        self.assertEqual(res["new_content"], "x = 1\n")
        self.assertEqual((self.root / "new.py").read_text(encoding="utf-8"), "x = 1\n")
        res2 = tool_write_file(self.root, path="new.py", content="x = 2\n")
        self.assertIn("Overwrote", res2["text"])
        self.assertEqual(res2["diff_action"], "overwrite")
        self.assertEqual(res2["old_content"], "x = 1\n")

    def test_write_file_rejects_escape(self) -> None:
        with self.assertRaises(SandboxError):
            tool_write_file(self.root, path="../escape.py", content="oops")

    def test_edit_file_unique_replacement(self) -> None:
        res = tool_edit_file(
            self.root, path="hello.py", old_string="print('hi')", new_string="print('bye')"
        )
        self.assertIn("Edited", res["text"])
        self.assertEqual(res["diff_action"], "edit")
        self.assertIn("print('bye')", res["new_content"])
        self.assertEqual((self.root / "hello.py").read_text(encoding="utf-8"), "print('bye')\n")

    def test_edit_file_errors_on_missing_old_string(self) -> None:
        res = tool_edit_file(self.root, path="hello.py", old_string="nope", new_string="X")
        self.assertTrue(res["text"].startswith("ERROR"))
        self.assertNotIn("new_content", res)

    def test_glob_lists_matches_relative(self) -> None:
        res = tool_glob(self.root, pattern="**/*.txt")
        self.assertIn("sub/data.txt", res["text"])

    def test_grep_returns_file_line_match(self) -> None:
        res = tool_grep(self.root, pattern="beta", path=".")
        self.assertIn("sub/data.txt", res["text"])
        self.assertIn(":2:beta", res["text"])

    def test_run_bash_captures_stdout_and_exit(self) -> None:
        res = tool_run_bash(self.root, command="python -c \"print(42)\"")
        self.assertIn("exit=0", res["text"])
        self.assertIn("42", res["text"])

    def test_run_bash_blocks_dangerous_command(self) -> None:
        res = tool_run_bash(self.root, command="git reset --hard")
        self.assertIn("blocked dangerous", res["text"])

    def test_run_bash_truncates_large_output(self) -> None:
        # tool_run_bash now streams via Popen (killable for the Stop button), so we
        # drive the real truncation path with a command that prints >16k chars.
        res = tool_run_bash(
            self.root,
            command="python -c \"print('A' * 30000)\"",
        )
        self.assertIn("truncated", res["text"])
        self.assertLess(len(res["text"]), 17000)

    # --- project_map (Variant B) ----------------------------------------

    def test_project_map_reports_tree_manifests_and_signatures(self) -> None:
        # Enrich the sandbox fixture with a manifest, an entry point, and a
        # module carrying a real function + class so signatures are exercised.
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'demo'\n", encoding="utf-8")
        (self.root / "app.py").write_text(
            "def main():\n    return 1\n", encoding="utf-8")
        (self.root / "service.py").write_text(
            "import os\n\n\n"
            "def helper(x):\n    return x\n\n\n"
            "class Engine:\n"
            "    def start(self):\n        return True\n",
            encoding="utf-8",
        )
        # A pruned dir must NOT show up in the tree.
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "junk.js").write_text(
            "x", encoding="utf-8")

        out = tool_project_map(self.root)
        text = out["text"]

        # Tree lists real files but skips the pruned directory.
        self.assertIn("service.py", text)
        self.assertIn("hello.py", text)
        self.assertNotIn("node_modules", text)
        # Manifest + entry-point detection.
        self.assertIn("pyproject.toml", text)
        self.assertIn("app.py", text)
        # Signatures: function + class with its method.
        self.assertIn("def helper", text)
        self.assertIn("class Engine", text)
        self.assertIn("start", text)

    def test_project_map_rejects_escape(self) -> None:
        with self.assertRaises(SandboxError):
            tool_project_map(self.root, path="../outside")


class AgentLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_loop_executes_tool_call_then_answers(self) -> None:
        """Simulate: turn1 → call write_file, turn2 → plain answer.

        Note: editing without running tests trips the soft verification gate,
        which injects one extra round before the run may close — so the model
        is asked once more and answers again (turn3).
        """
        scripted_responses = iter([
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "write_file",
                            "arguments": {"path": "out.txt", "content": "Hello Elira"},
                        }
                    }],
                }
            },
            {
                "message": {
                    "content": "Готово, файл создан.",
                    "tool_calls": [],
                }
            },
            # turn3: post-gate answer (gate already fired, run closes here).
            {
                "message": {
                    "content": "Готово, файл создан и проверять тут нечего.",
                    "tool_calls": [],
                }
            },
        ])

        def fake_chat(**kwargs):
            return next(scripted_responses)

        # Stub a classified auto spec so the executor applies no approval gate (the
        # fail-closed kernel blocks an absent/unclassified spec). This test verifies
        # loop behaviour, not P2 approval policy.
        with patch("app.application.tool_registry.runtime.get_tool",
                   return_value={"permission": "auto", "max_output_chars": 50000,
                                 "policy_classified": True, "enabled": True}):
            result = run_code_agent(
                user_message="Создай out.txt с текстом Hello Elira",
                project_root=self.root,
                model="test-model",
                chat_fn=fake_chat,
            )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["stop_reason"], "answer")
        self.assertEqual(result["steps"], 3)
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["tool"], "write_file")
        self.assertEqual(
            (self.root / "out.txt").read_text(encoding="utf-8"),
            "Hello Elira",
        )
        self.assertIn("Готово", result["response"])

    def test_loop_reports_max_steps_reached(self) -> None:
        """If the model keeps calling tools forever, we stop at max_steps."""
        def looping_chat(**kwargs):
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "glob",
                            "arguments": {"pattern": "*"},
                        }
                    }],
                }
            }

        result = run_code_agent(
            user_message="спин",
            project_root=self.root,
            model="test-model",
            max_steps=3,
            chat_fn=looping_chat,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertEqual(result["steps"], 3)
        # F2: wrap-up fallback (model kept tool-calling, no summary text) —
        # the user still gets a deterministic «что сделано» response.
        self.assertIn("max_steps=3", result["response"])
        self.assertIn("glob", result["response"])

    def test_max_steps_wrap_up_uses_model_summary_when_available(self) -> None:
        """F2: if the wrap-up call returns text, it becomes final_response."""
        responses = iter([
            {"message": {"content": "", "tool_calls": [{
                "function": {"name": "glob", "arguments": {"pattern": "*"}},
            }]}},
            {"message": {"content": "", "tool_calls": [{
                "function": {"name": "glob", "arguments": {"pattern": "*"}},
            }]}},
            # wrap-up (no-tools) call:
            {"message": {"content": "Итог: посмотрел файлы, не успел правки.", "tool_calls": []}},
        ])

        result = run_code_agent(
            user_message="спин",
            project_root=self.root,
            model="test-model",
            max_steps=2,
            chat_fn=lambda **kw: next(responses),
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertEqual(result["response"], "Итог: посмотрел файлы, не успел правки.")

    def test_stream_reports_preflight_block(self) -> None:
        with patch(
            "app.application.agent_registry.sandbox.preflight_or_raise",
            side_effect=RuntimeError("context rejected"),
        ):
            events = list(stream_code_agent(
                user_message="x",
                project_root=self.root,
                chat_fn=lambda **kw: {"message": {"content": "unused", "tool_calls": []}},
            ))
        self.assertEqual([event["type"] for event in events], ["run_started", "done"])
        self.assertFalse(events[-1]["ok"])
        self.assertIn("preflight blocked", events[-1]["error"])

    def test_stream_stops_at_wall_clock_boundary(self) -> None:
        with patch(
            "app.application.agent_registry.sandbox.preflight_or_raise",
            return_value={"limit": {"max_execution_seconds": 10}},
        ), patch(
            # Isolate the wall-clock test from the P9.3 routing probe (its
            # get_models() HTTP call would otherwise consume the finite
            # time.monotonic side_effect via urllib3 internals).
            "app.application.code_agent.agent_loop._resolve_code_route",
            return_value=("code-model", 8192, None),
        ), patch(
            "app.application.code_agent.agent_loop._record_code_route_metric",
        ), patch(
            "app.application.code_agent.agent_loop.time.monotonic",
            side_effect=[0.0, 11.0],
        ):
            events = list(stream_code_agent(
                user_message="x",
                project_root=self.root,
                chat_fn=lambda **kw: {"message": {"content": "unused", "tool_calls": []}},
            ))
        # F2: deadline now emits a wrap-up final_response before done, and the
        # stop_reason is the honest "timeout" instead of generic "error".
        self.assertEqual(
            [event["type"] for event in events],
            ["run_started", "final_response", "done"],
        )
        self.assertFalse(events[-1]["ok"])
        self.assertEqual(events[-1]["stop_reason"], "timeout")
        self.assertIn("timed out", events[-1]["error"])
        self.assertEqual(events[1]["text"], "unused")  # wrap-up call answer

    def test_loop_rejects_invalid_project_root(self) -> None:
        result = run_code_agent(
            user_message="ничего",
            project_root="/definitely/does/not/exist/123",
            chat_fn=lambda **kw: {"message": {"content": "", "tool_calls": []}},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stop_reason"], "error")

    def test_loop_handles_sandbox_violation_gracefully(self) -> None:
        """Sandbox violation in a tool call surfaces as a tool error, not a crash."""
        responses = iter([
            {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "../../../etc/passwd"},
                        }
                    }],
                }
            },
            {"message": {"content": "Не получилось.", "tool_calls": []}},
        ])

        def chat_fn(**kwargs):
            return next(responses)

        result = run_code_agent(
            user_message="x",
            project_root=self.root,
            chat_fn=chat_fn,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertIn("sandbox", result["tool_calls"][0]["result"])

    def test_stream_emits_event_types_in_order(self) -> None:
        responses = iter([
            {"message": {"content": "", "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}]}},
            {"message": {"content": "done.", "tool_calls": []}},
        ])

        def fake_chat(**kwargs):
            return next(responses)

        events = list(stream_code_agent(
            user_message="list files",
            project_root=self.root,
            model="test-model",
            chat_fn=fake_chat,
        ))
        types = [e.get("type") for e in events]
        self.assertEqual(types[0], "run_started")
        self.assertEqual(types[-1], "done")
        self.assertIn("step_started", types)
        self.assertIn("tool_started", types)
        self.assertIn("tool_call", types)
        self.assertIn("final_response", types)
        self.assertLess(types.index("tool_started"), types.index("tool_call"))
        done = events[-1]
        self.assertTrue(done["ok"])
        self.assertEqual(done["stop_reason"], "answer")

    def test_stream_includes_diff_data_for_write_file(self) -> None:
        (self.root / "exists.txt").write_text("v1\n", encoding="utf-8")
        responses = iter([
            {"message": {"content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "exists.txt", "content": "v2\n"}}}]}},
            {"message": {"content": "updated.", "tool_calls": []}},
        ])

        def fake_chat(**kwargs):
            return next(responses)

        # Stub a classified auto spec so the executor applies no approval gate (the
        # fail-closed kernel blocks an absent/unclassified spec). This test verifies
        # diff metadata propagation, not P2 approval policy.
        with patch("app.application.tool_registry.runtime.get_tool",
                   return_value={"permission": "auto", "max_output_chars": 50000,
                                 "policy_classified": True, "enabled": True}):
            events = list(stream_code_agent(
                user_message="bump version",
                project_root=self.root,
                chat_fn=fake_chat,
            ))
        tool_events = [e for e in events if e.get("type") == "tool_call"]
        self.assertEqual(len(tool_events), 1)
        tc = tool_events[0]
        self.assertEqual(tc["touched_path"], "exists.txt")
        self.assertEqual(tc["old_content"], "v1\n")
        self.assertEqual(tc["new_content"], "v2\n")
        self.assertEqual(tc["diff_action"], "overwrite")

    def test_stream_cancel_mid_loop(self) -> None:
        """request_cancel(run_id) flips the flag — the loop must exit cleanly."""
        run_id = "test-cancel-run"
        step_calls = {"n": 0}

        def fake_chat(**kwargs):
            step_calls["n"] += 1
            if step_calls["n"] == 1:
                return {"message": {"content": "", "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}]}}
            # Before step 2, simulate user pressing cancel:
            request_cancel(run_id)
            return {"message": {"content": "", "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}]}}

        events = list(stream_code_agent(
            user_message="loop forever",
            project_root=self.root,
            model="test-model",
            max_steps=10,
            run_id=run_id,
            chat_fn=fake_chat,
        ))
        done = events[-1]
        self.assertFalse(done["ok"])
        self.assertEqual(done["stop_reason"], "cancelled")

    def test_request_cancel_logs_when_kill_fails(self) -> None:
        """A failing kill_run_processes must be logged, not silently swallowed,
        and cancel must still flip the event (return True)."""
        import app.application.code_agent.agent_loop as loop_mod

        run_id = "test-cancel-kill-fail"
        loop_mod._register_run(run_id)
        try:
            with patch(
                "app.application.code_agent.tools.kill_run_processes",
                side_effect=RuntimeError("boom"),
            ), self.assertLogs(loop_mod.logger, level="WARNING") as cm:
                result = loop_mod.request_cancel(run_id)
            self.assertTrue(result)
            self.assertTrue(
                any("kill_run_processes failed" in line for line in cm.output)
            )
        finally:
            loop_mod._unregister_run(run_id)

    def test_run_code_agent_uses_conversation_history(self) -> None:
        captured: dict[str, list] = {}

        def fake_chat(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            return {"message": {"content": "ok", "tool_calls": []}}

        result = run_code_agent(
            user_message="new question",
            project_root=self.root,
            chat_fn=fake_chat,
            conversation_history=[
                {"role": "user", "content": "earlier"},
                {"role": "assistant", "content": "earlier reply"},
            ],
        )
        roles = [m["role"] for m in captured["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(captured["messages"][1]["content"], "earlier")
        self.assertEqual(captured["messages"][3]["content"], "new question")
        self.assertTrue(result["ok"])

    def test_project_prompt_appended_to_system(self) -> None:
        elira_dir = self.root / ".elira"
        elira_dir.mkdir(parents=True, exist_ok=True)
        (elira_dir / "agent.md").write_text("Never touch backend/legacy/.\n", encoding="utf-8")

        captured: dict[str, list] = {}

        def fake_chat(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            return {"message": {"content": "noted", "tool_calls": []}}

        run_code_agent(
            user_message="hi",
            project_root=self.root,
            chat_fn=fake_chat,
        )
        sys_content = captured["messages"][0]["content"]
        self.assertIn("Never touch backend/legacy", sys_content)
        # Header changed in Шаг 7: loader now uses "Instructions (.elira/agent.md)"
        self.assertIn("Instructions (.elira/agent.md)", sys_content)

    def test_num_ctx_passed_to_chat_options(self) -> None:
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured.update(kwargs)
            return {"message": {"content": "ok", "tool_calls": []}}

        result = run_code_agent(
            user_message="ping",
            project_root=self.root,
            num_ctx=8192,
            chat_fn=fake_chat,
        )
        self.assertTrue(result["ok"])
        self.assertIn("options", captured)
        self.assertEqual(captured["options"]["num_ctx"], 8192)

    def test_temperature_for_role_strict_profile(self) -> None:
        # Strict profile: code edits the most deterministic, casual replies looser.
        self.assertEqual(_temperature_for_role("code"), 0.1)
        self.assertEqual(_temperature_for_role("strong"), 0.3)
        self.assertEqual(_temperature_for_role("fast"), 0.4)
        # Case-insensitive and whitespace-tolerant.
        self.assertEqual(_temperature_for_role("  CODE "), 0.1)
        # Unknown / empty roles fall back to the default temperature.
        self.assertEqual(_temperature_for_role(None), 0.2)
        self.assertEqual(_temperature_for_role(""), 0.2)
        self.assertEqual(_temperature_for_role("embedding"), 0.2)

    def test_code_role_temperature_passed_to_chat_options(self) -> None:
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured.update(kwargs)
            return {"message": {"content": "ok", "tool_calls": []}}

        # The default code-agent route resolves to role "code", so the strict
        # profile must emit temperature 0.1 in the forwarded options.
        result = run_code_agent(
            user_message="ping",
            project_root=self.root,
            num_ctx=8192,
            chat_fn=fake_chat,
        )
        self.assertTrue(result["ok"])
        self.assertIn("options", captured)
        self.assertEqual(captured["options"]["temperature"], 0.1)

    def test_num_ctx_defaults_to_large_window(self) -> None:
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured.update(kwargs)
            return {"message": {"content": "ok", "tool_calls": []}}

        # This asserts the local provider-path default. Pin the local LLM provider off
        # so a developer's .env.local (which app.main loads into os.environ for
        # the whole pytest process) cannot route this to the server model —
        # whose smaller served context (8192) is correct there but is a
        # different scenario from the default tested here.
        with patch.dict(os.environ, {"LLAMA_SERVER_ENABLED": "false"}, clear=False):
            run_code_agent(
                user_message="ping",
                project_root=self.root,
                chat_fn=fake_chat,
            )
        # Whatever the default, it must NOT be local provider's tiny 2048 default.
        self.assertGreaterEqual(captured["options"]["num_ctx"], 8192)
        self.assertEqual(captured["options"]["num_ctx"], DEFAULT_NUM_CTX)

    def test_llama_server_code_profile_does_not_shrink_default_window(self) -> None:
        profile = {
            "id": "00-local-llama-code",
            "provider": "llama_server",
            "model": "local-model",
            "role": "code",
            "context_limit": 16384,
            "timeout_seconds": 180,
            "enabled": True,
            "cloud_consent_required": False,
        }

        with patch(
            "app.infrastructure.llm.local_models.get_models",
            return_value={"ok": True, "models": [{"name": "local-model", "model": "local-model"}]},
        ), patch(
            "app.core.config._get_profile_for_role",
            return_value=profile,
        ), patch(
            "app.application.monitoring.runtime.ensure_agent_limit",
            return_value={"max_context_tokens": DEFAULT_NUM_CTX},
        ):
            model, effective_num_ctx, decision = _resolve_code_route("auto", DEFAULT_NUM_CTX)

        self.assertEqual(model, "local-model")
        self.assertEqual(decision.source, "profile")
        self.assertEqual(effective_num_ctx, DEFAULT_NUM_CTX)

    def test_summarize_history_returns_assistant_text(self) -> None:
        def fake_chat(**kwargs):
            messages = kwargs.get("messages", [])
            # The summarize prompt should be the second message (after system).
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("USER:", messages[1]["content"])
            self.assertIn("AGENT:", messages[1]["content"])
            return {"message": {"content": "- file a.py reviewed\n- bug found in line 42", "tool_calls": []}}

        result = summarize_history(
            messages=[
                {"role": "user", "content": "review a.py"},
                {"role": "assistant", "content": "found a bug on line 42"},
                {"role": "user", "content": "fix it"},
                {"role": "assistant", "content": "patched and tested"},
            ],
            chat_fn=fake_chat,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["turn_count"], 4)
        self.assertIn("a.py", result["summary"])

    def test_summarize_history_empty_returns_blank(self) -> None:
        result = summarize_history(messages=[], chat_fn=lambda **kw: {"message": {"content": "x", "tool_calls": []}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["turn_count"], 0)

    def test_tool_schemas_include_recall(self) -> None:
        names = [t["function"]["name"] for t in build_tool_schemas()]
        self.assertIn("recall", names)
        self.assertIn("read_file", names)
        self.assertIn("run_bash", names)
        self.assertIn("todo_update", names)
        self.assertIn("delegate_task", names)

    def test_todo_update_tool_delegates_to_task_planner(self) -> None:
        with patch(
            "app.application.task_planner.service.todo_update",
            return_value={
                "ok": True,
                "run_id": "run-x",
                "items": [{"id": "a", "text": "step", "status": "pending", "position": 0}],
                "changed": [],
            },
        ) as spy:
            result = tool_todo_update(run_id="run-x")
        self.assertTrue(result["ok"])
        self.assertIn("Checklist for run run-x", result["text"])
        self.assertEqual(spy.call_args.kwargs["run_id"], "run-x")

    def test_todo_update_toolspec_is_policy_classified(self) -> None:
        from app.application.tool_registry.builtins import _build_native_code_agent_tools

        specs = {tool["name"]: tool for tool in _build_native_code_agent_tools()}
        spec = specs["todo_update"]
        self.assertEqual(spec["source"], "code_agent")
        self.assertEqual(spec["permission"], "auto")
        self.assertTrue(spec["side_effect"])
        self.assertEqual(spec["scopes"], ["task.write"])

    def test_delegate_task_toolspec_is_policy_classified(self) -> None:
        from app.application.tool_registry.builtins import _build_native_code_agent_tools

        specs = {tool["name"]: tool for tool in _build_native_code_agent_tools()}
        spec = specs["delegate_task"]
        self.assertEqual(spec["source"], "code_agent")
        self.assertEqual(spec["permission"], "auto")
        self.assertTrue(spec["side_effect"])
        self.assertEqual(spec["scopes"], ["task.write", "fs.read"])

    def test_delegate_task_runs_bounded_readonly_subagent(self) -> None:
        started = {
            "ok": True,
            "subagent_run_id": "sub-1",
            "parent_run_id": "parent-1",
            "role": "explore",
            "status": "in_progress",
        }
        finished = {**started, "status": "completed", "result_text": "found target"}
        with patch("app.application.task_planner.service.start_subagent_run", return_value=started) as start, \
             patch("app.application.task_planner.service.finish_subagent_run", return_value={"ok": True, **finished}) as finish, \
             patch("app.application.code_agent.agent_loop.run_code_agent",
                   return_value={"ok": True, "response": "found target", "error": None}) as run:
            result = tool_delegate_task(self.root, run_id="parent-1", role="explore", task="find target")

        self.assertTrue(result["ok"])
        self.assertEqual(result["subagent_run_id"], "sub-1")
        self.assertIn("found target", result["text"])
        self.assertEqual(start.call_args.kwargs["tool_allowlist"], ["read_file", "glob", "grep", "recall"])
        self.assertEqual(run.call_args.kwargs["run_id"], "sub-1")
        self.assertEqual(run.call_args.kwargs["agent_id"], "subagent-explore")
        self.assertEqual(tuple(run.call_args.kwargs["base_tools"]), ("read_file", "glob", "grep", "recall"))
        self.assertFalse(run.call_args.kwargs["auto_remember"])
        self.assertEqual(finish.call_args.kwargs["status"], "completed")

    def test_delegate_task_failure_is_terminal_not_exception(self) -> None:
        started = {"ok": True, "subagent_run_id": "sub-fail", "parent_run_id": "parent-1", "role": "verify"}
        with patch("app.application.task_planner.service.start_subagent_run", return_value=started), \
             patch("app.application.task_planner.service.finish_subagent_run",
                   return_value={"ok": True, **started, "status": "failed"}) as finish, \
             patch("app.application.code_agent.agent_loop.run_code_agent",
                   side_effect=RuntimeError("model down")):
            result = tool_delegate_task(self.root, run_id="parent-1", role="verify", task="check")
        self.assertFalse(result["ok"])
        self.assertIn("model down", result["text"])
        self.assertEqual(finish.call_args.kwargs["status"], "failed")

    def test_recall_tool_returns_text(self) -> None:
        # Either "No matches", "Found N items", or "ERROR" (if local provider offline)
        result = tool_recall(self.root, query="xyz_zzz_unlikely_phrase", top_k=3)
        text = result.get("text", "")
        self.assertTrue(
            "No matches" in text or text.startswith("ERROR") or text.startswith("Found"),
            f"unexpected recall output: {text}",
        )

    def test_recall_from_rag_endpoint_helper(self) -> None:
        result = recall_from_rag(query="anything", top_k=3)
        # ok=True even if no items, as long as RAG service is importable
        self.assertIn("ok", result)
        self.assertIn("items", result)

    def test_recall_from_rag_scopes_project_root(self) -> None:
        with patch(
            "app.application.rag_memory.service.search_rag",
            return_value={"ok": True, "items": [], "count": 0},
        ) as search:
            recall_from_rag(query="anything", top_k=3, project_root=self.root)
        self.assertEqual(search.call_args.kwargs["project"], project_scope_id(self.root))

    def test_index_project_walks_files(self) -> None:
        # Lay down a small fake project
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("def bar():\n    return 'hi'\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "junk.py").write_text("# should be skipped\n", encoding="utf-8")
        result = index_project(self.root, replace=False)
        self.assertTrue(result["ok"], result.get("error"))
        # Must process the 2 real files, not the node_modules one
        self.assertEqual(result["files_processed"], 2)
        # If RAG/local provider isn't running, chunks_indexed may be 0 but the walker still works
        self.assertIn("chunks_indexed", result)
        self.assertIn("failed_chunks", result)

    def test_index_project_rejects_bad_root(self) -> None:
        result = index_project("/clearly/not/a/path/zzz")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_auto_remember_skipped_when_disabled(self) -> None:
        responses = iter([{"message": {"content": "done", "tool_calls": []}}])

        remember_calls: list[Any] = []
        with patch(
            "app.application.code_agent.agent_loop._try_remember_turn",
            side_effect=lambda **kw: remember_calls.append(kw),
        ):
            run_code_agent(
                user_message="task A",
                project_root=self.root,
                auto_remember=False,
                chat_fn=lambda **kw: next(responses),
            )
        self.assertEqual(remember_calls, [])

    def test_inline_tool_call_fallback_parses_json_content_format(self) -> None:
        """code-model on local provider dumps tool calls as raw JSON in content."""
        known = {"glob", "read_file", "run_bash"}
        out = _extract_inline_tool_calls(
            '{"name": "glob", "arguments": {"pattern": "**/*.py"}}',
            known,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["function"]["name"], "glob")
        self.assertEqual(out[0]["function"]["arguments"]["pattern"], "**/*.py")

    def test_inline_tool_call_fallback_handles_code_fences(self) -> None:
        known = {"read_file"}
        out = _extract_inline_tool_calls(
            '```json\n{"name": "read_file", "arguments": {"path": "src/foo.py"}}\n```',
            known,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["function"]["name"], "read_file")

    def test_inline_tool_call_fallback_handles_function_wrap(self) -> None:
        known = {"run_bash"}
        out = _extract_inline_tool_calls(
            '{"function": {"name": "run_bash", "arguments": {"command": "pytest"}}}',
            known,
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["function"]["arguments"]["command"], "pytest")

    def test_inline_tool_call_fallback_handles_tool_calls_wrapper(self) -> None:
        known = {"glob"}
        out = _extract_inline_tool_calls(
            '{"tool_calls": [{"name": "glob", "arguments": {"pattern": "*"}}]}',
            known,
        )
        self.assertEqual(len(out), 1)

    def test_inline_tool_call_fallback_handles_array(self) -> None:
        known = {"glob", "grep"}
        out = _extract_inline_tool_calls(
            '[{"name": "glob", "arguments": {"pattern": "*"}}, {"name": "grep", "arguments": {"pattern": "TODO"}}]',
            known,
        )
        self.assertEqual(len(out), 2)

    def test_inline_tool_call_fallback_drops_unknown_tools(self) -> None:
        """Models love to hallucinate tool names like 'Find' or 'search_files'."""
        known = {"glob"}
        out = _extract_inline_tool_calls(
            '{"name": "Find", "arguments": {"pattern": "*.py"}}',
            known,
        )
        self.assertEqual(out, [])

    def test_inline_tool_call_fallback_ignores_plain_text(self) -> None:
        out = _extract_inline_tool_calls("This is a regular answer.", {"glob"})
        self.assertEqual(out, [])

    # ── F5: call-expression fallback fires only for pure call lines ──────

    def test_call_expr_pure_line_is_parsed(self) -> None:
        out = _extract_inline_tool_calls('read_file(path="src/foo.py")', {"read_file"})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["function"]["name"], "read_file")
        self.assertEqual(out[0]["function"]["arguments"]["path"], "src/foo.py")

    def test_call_expr_inside_prose_is_not_executed(self) -> None:
        """A final answer MENTIONING a call must not re-execute it."""
        out = _extract_inline_tool_calls(
            'я запустил run_bash(command="pytest") и всё зелёное',
            {"run_bash"},
        )
        self.assertEqual(out, [])

    def test_call_expr_in_code_fence_is_parsed(self) -> None:
        out = _extract_inline_tool_calls(
            '```bash\nrun_bash(command="pytest -q")\n```',
            {"run_bash"},
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["function"]["arguments"]["command"], "pytest -q")

    def test_call_expr_multiple_pure_lines_all_parsed(self) -> None:
        out = _extract_inline_tool_calls(
            'Сейчас посмотрю файлы:\nglob(pattern="**/*.py")\ngrep(pattern="TODO")',
            {"glob", "grep"},
        )
        self.assertEqual([c["function"]["name"] for c in out], ["glob", "grep"])

    def test_call_expr_trailing_semicolon_ok(self) -> None:
        out = _extract_inline_tool_calls('  glob(pattern="*");  ', {"glob"})
        self.assertEqual(len(out), 1)

    # ── F4: system prompt generated from the actual active tool set ──────

    def test_default_prompt_lists_base_tools_and_tool_search(self) -> None:
        from app.application.code_agent.agent_loop import _build_base_system_prompt

        prompt = _build_base_system_prompt(Path("/fake/project"))
        self.assertIn("- read_file(path)", prompt)
        self.assertIn("- todo_update(", prompt)
        self.assertIn("- delegate_task(", prompt)
        self.assertIn("tool_search(query)", prompt)
        # Long-tail tools are not advertised as directly available — the
        # executor would block them as not-activated (P10.1 deferred mode).
        self.assertNotIn("- web_search(query", prompt)
        self.assertNotIn("- sandbox_run(code", prompt)

    def test_custom_base_tools_reflected_in_prompt(self) -> None:
        from app.application.code_agent.agent_loop import _build_base_system_prompt

        prompt = _build_base_system_prompt(
            Path("/fake/project"), active_tools=("read_file", "web_search"),
        )
        self.assertIn("- read_file(path)", prompt)
        self.assertIn("- web_search(query", prompt)
        self.assertNotIn("- write_file(path", prompt)

    def test_unknown_active_tool_gets_generic_line(self) -> None:
        from app.application.code_agent.agent_loop import _build_base_system_prompt

        prompt = _build_base_system_prompt(
            Path("/fake/project"), active_tools=("read_file", "mcp_db_query"),
        )
        self.assertIn("- mcp_db_query(", prompt)

    def test_connected_project_prompt_says_glob_and_ask(self) -> None:
        # A real project root must get the "project connected" guidance: on a
        # path-less file request, glob & ask which file — not "attach a file".
        from app.application.code_agent.agent_loop import _build_base_system_prompt

        prompt = _build_base_system_prompt(Path("/fake/project"))
        self.assertIn("Проект подключён", prompt)
        self.assertNotIn("Проект не подключён", prompt)

    def test_scratch_workspace_prompt_says_no_project(self) -> None:
        # The scratch workspace (empty project_root default) must get the
        # "no project — attach a file" fallback instead.
        from app.application.code_agent import prompts as _p

        prompt = _p._build_base_system_prompt(_p._scratch_workspace_root())
        self.assertIn("Проект не подключён", prompt)
        self.assertNotIn("Проект подключён", prompt)

    def test_stream_uses_inline_tool_call_fallback(self) -> None:
        """End-to-end: a model that emits JSON-in-content should still trigger
        tool execution via the fallback parser."""
        # Step 1: model returns JSON tool call in content.
        # Step 2: model returns final answer
        responses = iter([
            {"message": {"content": '{"name": "glob", "arguments": {"pattern": "*"}}', "tool_calls": []}},
            {"message": {"content": "done.", "tool_calls": []}},
        ])

        def fake_chat(**kwargs):
            return next(responses)

        events = list(stream_code_agent(
            user_message="list files",
            project_root=self.root,
            chat_fn=fake_chat,
        ))
        tool_events = [e for e in events if e.get("type") == "tool_call"]
        self.assertEqual(len(tool_events), 1, "fallback should have produced one tool_call event")
        self.assertEqual(tool_events[0]["tool"], "glob")
        done = events[-1]
        self.assertTrue(done["ok"])

    def test_auto_remember_called_on_success(self) -> None:
        responses = iter([{"message": {"content": "fixed bug X", "tool_calls": []}}])
        remember_calls: list[Any] = []
        with patch(
            "app.application.code_agent.agent_loop._try_remember_turn",
            side_effect=lambda **kw: remember_calls.append(kw),
        ):
            run_code_agent(
                user_message="fix the bug",
                project_root=self.root,
                auto_remember=True,
                chat_fn=lambda **kw: next(responses),
            )
        self.assertEqual(len(remember_calls), 1)
        self.assertEqual(remember_calls[0]["user_message"], "fix the bug")
        self.assertEqual(remember_calls[0]["response_text"], "fixed bug X")

    def test_project_prompt_get_and_set_roundtrip(self) -> None:
        # No prompt yet
        first = get_project_prompt(self.root)
        self.assertTrue(first["ok"])
        self.assertFalse(first["exists"])
        self.assertEqual(first["content"], "")

        # Set it
        written = set_project_prompt(self.root, "Style: tabs, not spaces.")
        self.assertTrue(written["ok"])
        self.assertTrue(written["exists"])

        # Read back
        second = get_project_prompt(self.root)
        self.assertTrue(second["ok"])
        self.assertTrue(second["exists"])
        self.assertEqual(second["content"], "Style: tabs, not spaces.")

    # --- Soft verification gate (Variant 2) -----------------------------

    _AUTO_SPEC = {"permission": "auto", "max_output_chars": 50000,
                  "policy_classified": True, "enabled": True}

    def test_verify_gate_nudges_once_after_edit_without_tests(self) -> None:
        """Edited a file, then tried to answer with no run_bash/run_server:
        the gate must inject one extra round before the run can close."""
        turns = {"n": 0}

        def fake_chat(**kwargs):
            turns["n"] += 1
            if turns["n"] == 1:
                return {"message": {"content": "", "tool_calls": [{
                    "function": {"name": "write_file",
                                 "arguments": {"path": "out.txt", "content": "x"}},
                }]}}
            # turn 2: model tries to close without verifying -> gate fires.
            # turn 3: model answers again -> gate already fired, run closes.
            return {"message": {"content": "Готово.", "tool_calls": []}}

        with patch("app.application.tool_registry.runtime.get_tool",
                   return_value=self._AUTO_SPEC):
            result = run_code_agent(
                user_message="создай out.txt",
                project_root=self.root,
                model="test-model",
                chat_fn=fake_chat,
            )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["stop_reason"], "answer")
        # The gate forced a third model call it would not otherwise make.
        self.assertEqual(turns["n"], 3)

    def test_verify_gate_does_not_fire_when_tests_were_run(self) -> None:
        """Edit + run_bash in the same run satisfies the gate — no extra round."""
        turns = {"n": 0}

        def fake_chat(**kwargs):
            turns["n"] += 1
            if turns["n"] == 1:
                return {"message": {"content": "", "tool_calls": [{
                    "function": {"name": "write_file",
                                 "arguments": {"path": "out.txt", "content": "x"}},
                }]}}
            if turns["n"] == 2:
                return {"message": {"content": "", "tool_calls": [{
                    "function": {"name": "run_bash",
                                 "arguments": {"command": "echo ok"}},
                }]}}
            return {"message": {"content": "Готово, проверено.", "tool_calls": []}}

        with patch("app.application.tool_registry.runtime.get_tool",
                   return_value=self._AUTO_SPEC):
            result = run_code_agent(
                user_message="создай и проверь",
                project_root=self.root,
                model="test-model",
                chat_fn=fake_chat,
            )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["stop_reason"], "answer")
        # No injected round: write(1) -> run_bash(2) -> answer(3), and the
        # answer is accepted immediately.
        self.assertEqual(turns["n"], 3)

    def test_verify_gate_never_fires_on_no_edit_run(self) -> None:
        """A read-only / conversational run must close on the first answer —
        the gate must never fire when nothing was edited."""
        turns = {"n": 0}

        def fake_chat(**kwargs):
            turns["n"] += 1
            if turns["n"] == 1:
                return {"message": {"content": "", "tool_calls": [{
                    "function": {"name": "glob", "arguments": {"pattern": "*"}},
                }]}}
            return {"message": {"content": "Вот что я нашёл.", "tool_calls": []}}

        with patch("app.application.tool_registry.runtime.get_tool",
                   return_value=self._AUTO_SPEC):
            result = run_code_agent(
                user_message="что в проекте?",
                project_root=self.root,
                model="test-model",
                chat_fn=fake_chat,
            )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["stop_reason"], "answer")
        # glob(1) -> answer(2); the answer is accepted with no extra round.
        self.assertEqual(turns["n"], 2)


class ApprovalPauseTest(unittest.TestCase):
    """F1: the loop pauses on waiting_approval and the approval is consumed
    in the SAME run; reject/timeout feed a model-oriented message instead."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _chat_two_steps():
        # write_file edits a file but no run_bash/run_server follows, so the
        # soft verification gate injects one extra round before the run may
        # close. The third response is that post-gate answer; tests that exit
        # earlier (cancel / timeout / zero-wait) simply never consume it.
        responses = iter([
            {"message": {"content": "", "tool_calls": [{
                "function": {"name": "write_file",
                             "arguments": {"path": "a.txt", "content": "hi"}},
            }]}},
            {"message": {"content": "Готово.", "tool_calls": []}},
            {"message": {"content": "Проверять тут нечего — файл записан.",
                         "tool_calls": []}},
        ])
        return lambda **kw: next(responses)

    @staticmethod
    def _waiting(approval_id: str = "ap-1"):
        from app.application.agent_kernel.executor import ToolExecutionResult
        return ToolExecutionResult(
            status="waiting_approval",
            output={"ok": False, "approval_id": approval_id, "text": "ждёт подтверждения"},
            error=f"waiting_approval:{approval_id}",
        )

    @staticmethod
    def _ok_result():
        from app.application.agent_kernel.executor import ToolExecutionResult
        return ToolExecutionResult(status="ok", output={"ok": True, "text": "written"}, error=None)

    def test_approved_executes_in_same_run(self) -> None:
        import app.application.code_agent.agent_loop as loop_mod

        statuses = iter(["pending", "approved"])
        with patch.object(loop_mod, "_kernel_exec",
                          side_effect=[self._waiting(), self._ok_result()]) as ke, \
             patch.object(loop_mod, "_approval_status",
                          side_effect=lambda _id: next(statuses)), \
             patch.object(loop_mod, "_APPROVAL_POLL_INTERVAL", 0.01):
            events = list(loop_mod.stream_code_agent(
                user_message="создай файл",
                project_root=self.root,
                chat_fn=self._chat_two_steps(),
                approval_wait_seconds=5,
            ))

        types = [e["type"] for e in events]
        self.assertIn("approval_pending", types)
        pending = next(e for e in events if e["type"] == "approval_pending")
        self.assertEqual(pending["tool"], "write_file")
        self.assertEqual(pending["approval_id"], "ap-1")
        tool_events = [e for e in events if e["type"] == "tool_call"]
        self.assertEqual(tool_events[0]["result"], "written")
        self.assertTrue(events[-1]["ok"])
        self.assertEqual(events[-1]["stop_reason"], "answer")
        self.assertEqual(ke.call_count, 2)  # waiting + re-exec after approve

    def test_tool_started_for_approval_tool_waits_until_approved(self) -> None:
        import app.application.code_agent.agent_loop as loop_mod

        statuses = iter(["pending", "approved"])
        with patch.object(loop_mod, "_kernel_exec",
                          side_effect=[self._waiting(), self._ok_result()]), \
             patch.object(loop_mod, "_approval_status",
                          side_effect=lambda _id: next(statuses)), \
             patch.object(loop_mod, "_APPROVAL_POLL_INTERVAL", 0.01), \
             patch("app.application.tool_registry.runtime.get_tool",
                   return_value={"permission": "require_approval"}):
            events = list(loop_mod.stream_code_agent(
                user_message="СЃРѕР·РґР°Р№ С„Р°Р№Р»",
                project_root=self.root,
                chat_fn=self._chat_two_steps(),
                approval_wait_seconds=5,
            ))

        types = [e["type"] for e in events]
        started_indexes = [i for i, event_type in enumerate(types) if event_type == "tool_started"]
        self.assertEqual(len(started_indexes), 1)
        self.assertGreater(started_indexes[0], types.index("approval_pending"))
        self.assertLess(started_indexes[0], types.index("tool_call"))

    def test_rejected_feeds_model_and_run_continues(self) -> None:
        import app.application.code_agent.agent_loop as loop_mod

        with patch.object(loop_mod, "_kernel_exec",
                          side_effect=[self._waiting()]) as ke, \
             patch.object(loop_mod, "_approval_status", return_value="rejected"), \
             patch.object(loop_mod, "_APPROVAL_POLL_INTERVAL", 0.01):
            events = list(loop_mod.stream_code_agent(
                user_message="создай файл",
                project_root=self.root,
                chat_fn=self._chat_two_steps(),
                approval_wait_seconds=5,
            ))

        tool_events = [e for e in events if e["type"] == "tool_call"]
        self.assertTrue(tool_events[0]["result"])
        self.assertEqual(events[-1]["stop_reason"], "answer")
        self.assertEqual(ke.call_count, 1)  # no re-exec after reject

    def test_wait_timeout_feeds_model(self) -> None:
        import app.application.code_agent.agent_loop as loop_mod

        with patch.object(loop_mod, "_kernel_exec",
                          side_effect=[self._waiting()]), \
             patch.object(loop_mod, "_approval_status", return_value="pending"), \
             patch.object(loop_mod, "_APPROVAL_POLL_INTERVAL", 0.01):
            events = list(loop_mod.stream_code_agent(
                user_message="создай файл",
                project_root=self.root,
                chat_fn=self._chat_two_steps(),
                approval_wait_seconds=1,
            ))

        tool_events = [e for e in events if e["type"] == "tool_call"]
        self.assertTrue(tool_events[0]["result"])
        self.assertEqual(events[-1]["stop_reason"], "answer")

    def test_cancel_during_wait(self) -> None:
        import app.application.code_agent.agent_loop as loop_mod

        def cancel_then_pending(_id: str) -> str:
            loop_mod.request_cancel("t-cancel")
            return "pending"

        with patch.object(loop_mod, "_kernel_exec",
                          side_effect=[self._waiting()]), \
             patch.object(loop_mod, "_approval_status",
                          side_effect=cancel_then_pending), \
             patch.object(loop_mod, "_APPROVAL_POLL_INTERVAL", 0.01):
            events = list(loop_mod.stream_code_agent(
                user_message="создай файл",
                project_root=self.root,
                chat_fn=self._chat_two_steps(),
                approval_wait_seconds=5,
                run_id="t-cancel",
            ))

        self.assertEqual(events[-1]["stop_reason"], "cancelled")

    def test_zero_wait_keeps_legacy_passthrough(self) -> None:
        import app.application.code_agent.agent_loop as loop_mod

        with patch.object(loop_mod, "_kernel_exec",
                          side_effect=[self._waiting()]) as ke:
            events = list(loop_mod.stream_code_agent(
                user_message="создай файл",
                project_root=self.root,
                chat_fn=self._chat_two_steps(),
                approval_wait_seconds=0,
            ))

        types = [e["type"] for e in events]
        self.assertNotIn("approval_pending", types)
        tool_events = [e for e in events if e["type"] == "tool_call"]
        self.assertTrue(tool_events[0]["result"])
        self.assertEqual(ke.call_count, 1)

    def test_deadline_extended_by_human_wait(self) -> None:
        """Waiting ~1.4s on a 1s execution budget must NOT kill the run."""
        import app.application.code_agent.agent_loop as loop_mod

        statuses = iter(["pending"] * 12 + ["approved"])
        with patch.object(loop_mod, "_kernel_exec",
                          side_effect=[self._waiting(), self._ok_result()]), \
             patch.object(loop_mod, "_approval_status",
                          side_effect=lambda _id: next(statuses)), \
             patch.object(loop_mod, "_APPROVAL_POLL_INTERVAL", 0.12):
            events = list(loop_mod.stream_code_agent(
                user_message="создай файл",
                project_root=self.root,
                chat_fn=self._chat_two_steps(),
                approval_wait_seconds=10,
                execution_timeout_seconds=1,
            ))

        self.assertTrue(events[-1]["ok"], events[-1])
        self.assertEqual(events[-1]["stop_reason"], "answer")


class XmlToolTraceRegressionTest(unittest.TestCase):
    def test_xml_tool_call_is_recovered_for_known_tool(self) -> None:
        result = _extract_inline_tool_calls(
            '<tool_call><function=glob>{"pattern":"*.py"}</function></tool_call>',
            {"glob"},
        )
        self.assertEqual(result, [{"function": {"name": "glob", "arguments": {"pattern": "*.py"}}}])

    def test_xml_tool_call_drops_unknown_tool(self) -> None:
        result = _extract_inline_tool_calls(
            '<tool_call><function=missing>{}</function></tool_call>',
            {"glob"},
        )
        self.assertEqual(result, [])


class LoopHelpersSplitContractTest(unittest.TestCase):
    """Pin the agent_loop <-> loop_helpers split so a future refactor cannot
    silently break it. The split is behaviour-preserving ONLY because of two
    invariants the green test suite does NOT otherwise enforce:

    1. The heartbeat constant, chat-event generators and the cancellation
       registry MUST stay defined in ``agent_loop`` itself. ``_chat_events``
       reads ``_LLM_HEARTBEAT_EVERY`` from its own module globals, and tests
       patch ``agent_loop._LLM_HEARTBEAT_EVERY`` / ``agent_loop._chat_events``.
       If a future split moved any of these into the leaf, those patches would
       target a dead name and silently stop taking effect — no existing test
       would go red. This one does.

    2. ``loop_helpers`` must remain a leaf: importing nothing from
       ``agent_loop``. A back-import would create a cycle (agent_loop imports
       the leaf at module load) and is the classic way a "clean split" rots.
    """

    # Names that must be DEFINED in agent_loop (this module owns them), not
    # merely re-exported from the leaf.
    _OWNED_BY_AGENT_LOOP = (
        "_LLM_HEARTBEAT_EVERY",
        "_chat_events",
        "_local_chat_stream",
        "_CANCEL_REGISTRY",
        "request_cancel",
        "_register_run",
        "_unregister_run",
    )

    def test_kept_items_are_defined_in_agent_loop_not_the_leaf(self) -> None:
        from app.application.code_agent import agent_loop, loop_helpers

        for name in self._OWNED_BY_AGENT_LOOP:
            self.assertTrue(
                hasattr(agent_loop, name),
                f"agent_loop lost ownership of {name!r}",
            )
            # Defined here, not leaked into the leaf. If a refactor moves it to
            # loop_helpers and re-exports, this is the line that fails.
            self.assertFalse(
                hasattr(loop_helpers, name),
                f"{name!r} leaked into loop_helpers — patch('agent_loop.{name}') "
                f"would silently stop working",
            )

    def test_heartbeat_constant_is_read_from_agent_loop_namespace(self) -> None:
        # The whole reason _LLM_HEARTBEAT_EVERY stayed put: _chat_events must
        # resolve it from agent_loop's globals at call time so the patch below
        # actually changes behaviour. We assert the function's module globals
        # are agent_loop's — i.e. the patch target and the reader agree.
        from app.application.code_agent import agent_loop

        self.assertIs(
            agent_loop._chat_events.__globals__,
            vars(agent_loop),
            "_chat_events no longer closes over agent_loop's globals; "
            "patch('agent_loop._LLM_HEARTBEAT_EVERY') would be a no-op",
        )

    def test_loop_helpers_is_a_leaf_no_back_import(self) -> None:
        import sys
        import app.application.code_agent.loop_helpers as leaf

        # The leaf module must not pull agent_loop into its own namespace, and
        # must not have triggered agent_loop's import as a side effect of being
        # imported in isolation. We check its module globals directly rather
        # than the import graph, which is enough to catch a `from ...agent_loop
        # import X` regression.
        self.assertNotIn(
            "agent_loop",
            vars(leaf),
            "loop_helpers imported a name from agent_loop — that is a cycle",
        )
        for attr in vars(leaf).values():
            mod = getattr(attr, "__module__", "")
            self.assertNotEqual(
                mod,
                "app.application.code_agent.agent_loop",
                "loop_helpers re-exports a symbol owned by agent_loop — cycle risk",
            )
        # Sanity: the leaf is actually importable on its own and carries the
        # helpers it is supposed to own.
        self.assertTrue(hasattr(sys.modules[leaf.__name__], "_truncate_for_llm"))


if __name__ == "__main__":
    unittest.main()
