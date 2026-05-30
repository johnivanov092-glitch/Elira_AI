"""Tests for the real code-agent loop and sandboxed tools."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import (  # noqa: E402
    DEFAULT_NUM_CTX,
    _extract_inline_tool_calls,
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
    tool_read_file,
    tool_recall,
    tool_run_bash,
    tool_write_file,
)


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


class AgentLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_loop_executes_tool_call_then_answers(self) -> None:
        """Simulate: turn1 → call write_file, turn2 → plain answer."""
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
        ])

        def fake_chat(**kwargs):
            return next(scripted_responses)

        result = run_code_agent(
            user_message="Создай out.txt с текстом Hello Elira",
            project_root=self.root,
            model="test-model",
            chat_fn=fake_chat,
        )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["stop_reason"], "answer")
        self.assertEqual(result["steps"], 2)
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

        self.assertFalse(result["ok"])
        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertEqual(result["steps"], 3)

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
        self.assertIn("tool_call", types)
        self.assertIn("final_response", types)
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
        self.assertIn("Project-specific instructions", sys_content)

    def test_num_ctx_passed_to_ollama_options(self) -> None:
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

    def test_num_ctx_defaults_to_large_window(self) -> None:
        captured: dict[str, Any] = {}

        def fake_chat(**kwargs):
            captured.update(kwargs)
            return {"message": {"content": "ok", "tool_calls": []}}

        run_code_agent(
            user_message="ping",
            project_root=self.root,
            chat_fn=fake_chat,
        )
        # Whatever the default, it must NOT be Ollama's tiny 2048 default.
        self.assertGreaterEqual(captured["options"]["num_ctx"], 8192)
        self.assertEqual(captured["options"]["num_ctx"], DEFAULT_NUM_CTX)

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

    def test_recall_tool_returns_text(self) -> None:
        # Either "No matches", "Found N items", or "ERROR" (if Ollama offline)
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
        # If RAG/Ollama isn't running, chunks_indexed may be 0 but the walker still works
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

    def test_inline_tool_call_fallback_parses_qwen_coder_format(self) -> None:
        """qwen2.5-coder on Ollama dumps tool calls as raw JSON in content."""
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

    def test_stream_uses_inline_tool_call_fallback(self) -> None:
        """End-to-end: a model that emits JSON-in-content should still trigger
        tool execution via the fallback parser."""
        # Step 1: model returns JSON tool call in content (qwen-coder style)
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


if __name__ == "__main__":
    unittest.main()
