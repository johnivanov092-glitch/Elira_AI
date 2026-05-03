"""Tests for run_history, python_runner, and terminal application modules."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.python_runner.runtime import ALLOWED_IMPORTS, SAFE_BUILTINS, execute_python  # noqa: E402
from app.application.run_history.runtime import RunHistoryService  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# RunHistoryService
# ─────────────────────────────────────────────────────────────────────────────

def _in_memory_service():
    """Return (service, keeper_conn).

    Uses a shared-cache in-memory URI.  The keeper_conn must stay open for
    the lifetime of the test; closing it destroys the shared-cache database.
    """
    import uuid
    db_name = f"file:test_rh_{uuid.uuid4().hex}?mode=memory&cache=shared"

    _CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS run_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT UNIQUE,
        user_input TEXT, started_at TEXT,
        finished_at TEXT, ok INTEGER, route TEXT,
        model TEXT, answer_len INTEGER, error TEXT
    )
    """

    # "keeper" connection — never closed, keeps the in-memory DB alive
    keeper = sqlite3.connect(db_name, uri=True)
    keeper.row_factory = sqlite3.Row
    keeper.execute(_CREATE_SQL)
    keeper.commit()

    def connect():
        c = sqlite3.connect(db_name, uri=True)
        c.row_factory = sqlite3.Row
        c.execute(_CREATE_SQL)
        c.commit()
        return c

    def rotate(conn):
        pass

    return RunHistoryService(connect_func=connect, rotate_func=rotate), keeper


class RunHistoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc, self._keeper = _in_memory_service()

    def tearDown(self) -> None:
        self._keeper.close()
        super().tearDown()

    def test_start_run_returns_run_id(self) -> None:
        run = self.svc.start_run("Hello")
        self.assertIn("run_id", run)
        self.assertEqual(run["user_input"], "Hello")

    def test_finish_run_persists_to_db(self) -> None:
        run = self.svc.start_run("test query")
        self.svc.finish_run(
            run["run_id"],
            {"ok": True, "answer": "pong", "meta": {"route": "chat", "model_name": "llama3"}},
        )
        rows = self.svc.list_runs(limit=10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["run_id"], run["run_id"])
        self.assertEqual(row["ok"], 1)
        self.assertEqual(row["route"], "chat")
        self.assertEqual(row["model"], "llama3")

    def test_finish_failed_run(self) -> None:
        run = self.svc.start_run("fail test")
        self.svc.finish_run(run["run_id"], {"ok": False, "error": "boom", "meta": {}})
        rows = self.svc.list_runs()
        self.assertEqual(rows[0]["ok"], 0)
        self.assertEqual(rows[0]["error"], "boom")

    def test_add_event_before_finish(self) -> None:
        run = self.svc.start_run("with events")
        self.svc.add_event(run["run_id"], "tool.executed", {"tool": "search"})
        # Active run keeps the event in memory
        active = self.svc._active_runs.get(run["run_id"])
        self.assertIsNotNone(active)
        self.assertEqual(len(active["events"]), 1)
        self.assertEqual(active["events"][0]["event_type"], "tool.executed")

    def test_add_event_unknown_run_is_noop(self) -> None:
        # Should not raise
        self.svc.add_event("nonexistent", "type", {})

    def test_list_runs_empty_db(self) -> None:
        rows = self.svc.list_runs()
        self.assertEqual(rows, [])

    def test_list_runs_respects_limit(self) -> None:
        for i in range(5):
            run = self.svc.start_run(f"query {i}")
            self.svc.finish_run(run["run_id"], {"ok": True, "answer": "ok", "meta": {}})
        self.assertEqual(len(self.svc.list_runs(limit=3)), 3)

    def test_answer_len_stored(self) -> None:
        run = self.svc.start_run("q")
        self.svc.finish_run(run["run_id"], {"ok": True, "answer": "hello world", "meta": {}})
        rows = self.svc.list_runs()
        self.assertEqual(rows[0]["answer_len"], len("hello world"))


# ─────────────────────────────────────────────────────────────────────────────
# execute_python
# ─────────────────────────────────────────────────────────────────────────────

class ExecutePythonTest(unittest.TestCase):
    def test_basic_arithmetic(self) -> None:
        result = execute_python("x = 2 + 3")
        self.assertTrue(result["ok"])
        self.assertEqual(result["locals"]["x"], "5")

    def test_print_captured(self) -> None:
        result = execute_python("print('hello')")
        self.assertTrue(result["ok"])
        self.assertIn("hello", result["stdout"])

    def test_empty_code_returns_not_ok(self) -> None:
        result = execute_python("")
        self.assertFalse(result["ok"])
        self.assertIn("Empty", result["error"])

    def test_whitespace_only_is_empty(self) -> None:
        result = execute_python("   ")
        self.assertFalse(result["ok"])

    def test_syntax_error(self) -> None:
        result = execute_python("def bad(:")
        self.assertFalse(result["ok"])
        self.assertIn("traceback", result)

    def test_runtime_exception(self) -> None:
        result = execute_python("1 / 0")
        self.assertFalse(result["ok"])
        self.assertIn("division by zero", result["error"])

    def test_allowed_import_json(self) -> None:
        result = execute_python("import json; x = json.dumps({'a': 1})")
        self.assertTrue(result["ok"])
        self.assertIn('"a"', result["locals"]["x"])

    def test_allowed_import_math(self) -> None:
        result = execute_python("import math; x = math.sqrt(16)")
        self.assertTrue(result["ok"])
        self.assertEqual(result["locals"]["x"], "4.0")

    def test_blocked_import_os(self) -> None:
        result = execute_python("import os")
        self.assertFalse(result["ok"])
        self.assertIn("blocked", result["error"].lower())

    def test_blocked_import_subprocess(self) -> None:
        result = execute_python("import subprocess")
        self.assertFalse(result["ok"])

    def test_blocked_import_sys(self) -> None:
        result = execute_python("import sys")
        self.assertFalse(result["ok"])

    def test_locals_excluded_dunder(self) -> None:
        result = execute_python("x = 1")
        self.assertTrue(result["ok"])
        for key in result["locals"]:
            self.assertFalse(key.startswith("__"), f"dunder key leaked: {key}")

    def test_multiline_code(self) -> None:
        result = execute_python("total = 0\nfor i in range(5):\n    total += i")
        self.assertTrue(result["ok"])
        self.assertEqual(result["locals"]["total"], "10")

    def test_safe_builtins_available(self) -> None:
        cases = {
            "len": "len([1, 2, 3])",
            "range": "list(range(3))",
            "sum": "sum([1, 2, 3])",
            "sorted": "sorted([3, 1, 2])",
            "min": "min([1, 2, 3])",
            "max": "max([1, 2, 3])",
        }
        for name, expr in cases.items():
            result = execute_python(f"x = {expr}")
            self.assertTrue(result["ok"], f"{name} should be available, got: {result.get('error')}")

    def test_allowed_imports_set_is_non_empty(self) -> None:
        self.assertGreater(len(ALLOWED_IMPORTS), 0)

    def test_safe_builtins_has_print(self) -> None:
        self.assertIn("print", SAFE_BUILTINS)


# ─────────────────────────────────────────────────────────────────────────────
# terminal — exec_command and change_dir
# ─────────────────────────────────────────────────────────────────────────────

class TerminalTest(unittest.TestCase):
    def setUp(self) -> None:
        import app.application.terminal.runtime as term

        self._term = term
        # snapshot state
        self._orig_cwd = term._cwd

    def tearDown(self) -> None:
        self._term._cwd = self._orig_cwd

    def test_empty_command_returns_not_ok(self) -> None:
        result = self._term.exec_command("")
        self.assertFalse(result["ok"])
        self.assertIn("Empty", result["error"])

    def test_blocked_command_refused(self) -> None:
        result = self._term.exec_command("rm -rf /")
        self.assertFalse(result["ok"])
        self.assertIn("blocked", result["error"].lower())

    def test_change_dir_to_valid_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._term.change_dir(tmpdir)
        self.assertTrue(result["ok"])

    def test_change_dir_nonexistent_returns_error(self) -> None:
        result = self._term.change_dir("/path/that/does/not/exist_xyz")
        self.assertFalse(result["ok"])
        self.assertIn("cwd", result)

    def test_cd_command_delegates_to_change_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._term.exec_command(f"cd {tmpdir}")
        self.assertTrue(result["ok"])
        self.assertIn("cwd", result)

    def test_exec_echo(self) -> None:
        result = self._term.exec_command("echo hello")
        self.assertTrue(result["ok"])
        self.assertIn("hello", result["stdout"])

    def test_exec_returns_returncode(self) -> None:
        result = self._term.exec_command("echo ok")
        self.assertIn("returncode", result)

    def test_get_cwd_returns_string(self) -> None:
        cwd = self._term.get_cwd()
        self.assertIsInstance(cwd, str)
        self.assertTrue(len(cwd) > 0)

    def test_timeout_handled(self) -> None:
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep", 1)):
            result = self._term.exec_command("sleep 999")
        self.assertFalse(result["ok"])
        self.assertIn("imeout", result["error"])


if __name__ == "__main__":
    unittest.main()
