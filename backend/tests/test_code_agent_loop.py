"""Tests for the real code-agent loop and sandboxed tools."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import (  # noqa: E402
    get_project_prompt,
    request_cancel,
    run_code_agent,
    set_project_prompt,
    stream_code_agent,
)
from app.application.code_agent.tools import (  # noqa: E402
    SandboxError,
    tool_edit_file,
    tool_glob,
    tool_grep,
    tool_read_file,
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
