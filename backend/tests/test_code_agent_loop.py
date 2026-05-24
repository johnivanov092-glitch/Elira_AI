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

from app.application.code_agent.agent_loop import run_code_agent  # noqa: E402
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
        self.assertIn("print('hi')", out)
        self.assertIn("1\t", out)

    def test_read_file_rejects_escape(self) -> None:
        with self.assertRaises(SandboxError):
            tool_read_file(self.root, path="../outside.txt")

    def test_write_file_creates_and_overwrites(self) -> None:
        msg = tool_write_file(self.root, path="new.py", content="x = 1\n")
        self.assertIn("Created", msg)
        self.assertEqual((self.root / "new.py").read_text(encoding="utf-8"), "x = 1\n")
        msg2 = tool_write_file(self.root, path="new.py", content="x = 2\n")
        self.assertIn("Overwrote", msg2)

    def test_write_file_rejects_escape(self) -> None:
        with self.assertRaises(SandboxError):
            tool_write_file(self.root, path="../escape.py", content="oops")

    def test_edit_file_unique_replacement(self) -> None:
        result = tool_edit_file(
            self.root, path="hello.py", old_string="print('hi')", new_string="print('bye')"
        )
        self.assertIn("Edited", result)
        self.assertEqual((self.root / "hello.py").read_text(encoding="utf-8"), "print('bye')\n")

    def test_edit_file_errors_on_missing_old_string(self) -> None:
        result = tool_edit_file(self.root, path="hello.py", old_string="nope", new_string="X")
        self.assertTrue(result.startswith("ERROR"))

    def test_glob_lists_matches_relative(self) -> None:
        out = tool_glob(self.root, pattern="**/*.txt")
        self.assertIn("sub/data.txt", out)

    def test_grep_returns_file_line_match(self) -> None:
        out = tool_grep(self.root, pattern="beta", path=".")
        self.assertIn("sub/data.txt", out)
        self.assertIn(":2:beta", out)

    def test_run_bash_captures_stdout_and_exit(self) -> None:
        out = tool_run_bash(self.root, command="python -c \"print(42)\"")
        self.assertIn("exit=0", out)
        self.assertIn("42", out)


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


if __name__ == "__main__":
    unittest.main()
