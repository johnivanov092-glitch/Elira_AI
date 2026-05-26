"""Tests for pure helpers across three modules.

  domain/agents/planner_runtime.py:
    extract_first_url
    planner_safe_terminal_command
    task_graph_context_from_deps
    build_task_graph_state_blob

  domain/agents/orchestrator_postprocess_runtime.py:
    build_finalize_prompt

  domain/tools/terminal_tool.py:
    is_dangerous_command

All functions are pure (no DB, no HTTP, no FS).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.agents.planner_runtime import (  # noqa: E402
    extract_first_url,
    planner_safe_terminal_command,
    task_graph_context_from_deps,
    build_task_graph_state_blob,
)
from app.domain.agents.orchestrator_postprocess_runtime import (  # noqa: E402
    build_finalize_prompt,
)
from app.domain.tools.terminal_tool import is_dangerous_command  # noqa: E402


# planner_runtime.py - extract_first_url

class ExtractFirstUrlTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(extract_first_url("hello"), str)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(extract_first_url(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(extract_first_url(None), "")  # type: ignore[arg-type]

    def test_no_url_returns_empty(self) -> None:
        self.assertEqual(extract_first_url("no url here at all"), "")

    def test_http_url_extracted(self) -> None:
        result = extract_first_url("visit http://example.com for more")
        self.assertEqual(result, "http://example.com")

    def test_https_url_extracted(self) -> None:
        result = extract_first_url("see https://example.com/path")
        self.assertIn("https://example.com", result)

    def test_url_with_path_extracted(self) -> None:
        result = extract_first_url("check https://example.com/docs/guide.html here")
        self.assertIn("https://example.com/docs/guide.html", result)

    def test_trailing_period_stripped(self) -> None:
        result = extract_first_url("see https://example.com.")
        self.assertFalse(result.endswith("."))

    def test_trailing_comma_stripped(self) -> None:
        result = extract_first_url("see https://example.com,")
        self.assertFalse(result.endswith(","))

    def test_trailing_paren_stripped(self) -> None:
        result = extract_first_url("(https://example.com)")
        self.assertFalse(result.endswith(")"))

    def test_only_first_url_returned(self) -> None:
        result = extract_first_url("http://first.com and http://second.com")
        self.assertEqual(result, "http://first.com")

    def test_url_only_text(self) -> None:
        result = extract_first_url("https://github.com/repo")
        self.assertIn("github.com", result)

    def test_url_with_query_string(self) -> None:
        result = extract_first_url("search at https://duckduckgo.com/?q=python now")
        self.assertIn("duckduckgo.com", result)


# planner_runtime.py - planner_safe_terminal_command

class PlannerSafeTerminalCommandTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(planner_safe_terminal_command("ls"), bool)

    def test_empty_string_false(self) -> None:
        self.assertFalse(planner_safe_terminal_command(""))

    def test_none_false(self) -> None:
        self.assertFalse(planner_safe_terminal_command(None))  # type: ignore[arg-type]

    def test_ls_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("ls"))

    def test_ls_with_flags_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("ls -la"))

    def test_dir_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("dir"))

    def test_pwd_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("pwd"))

    def test_git_status_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("git status"))

    def test_git_branch_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("git branch"))

    def test_pip_list_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("pip list"))

    def test_python_version_is_safe(self) -> None:
        self.assertTrue(planner_safe_terminal_command("python --version"))

    def test_git_commit_is_unsafe(self) -> None:
        # "git commit" is not in allowed prefixes
        self.assertFalse(planner_safe_terminal_command("git commit -m 'msg'"))

    def test_pip_install_is_unsafe(self) -> None:
        self.assertFalse(planner_safe_terminal_command("pip install requests"))

    def test_dangerous_blocked_command_false(self) -> None:
        # "rm -rf /" is explicitly blocked
        self.assertFalse(planner_safe_terminal_command("rm -rf /"))

    def test_shutdown_blocked(self) -> None:
        self.assertFalse(planner_safe_terminal_command("shutdown now"))

    def test_case_insensitive(self) -> None:
        # Function lowercases before checking
        self.assertTrue(planner_safe_terminal_command("LS -la"))

    def test_arbitrary_command_false(self) -> None:
        self.assertFalse(planner_safe_terminal_command("curl http://example.com"))


# planner_runtime.py - task_graph_context_from_deps

class TaskGraphContextFromDepsTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(task_graph_context_from_deps({}, {}), str)

    def test_no_depends_on_returns_empty(self) -> None:
        node = {"id": "n1", "tool": "reasoning", "goal": "analyze"}
        self.assertEqual(task_graph_context_from_deps(node, {}), "")

    def test_empty_depends_on_returns_empty(self) -> None:
        node = {"depends_on": []}
        self.assertEqual(task_graph_context_from_deps(node, {}), "")

    def test_dep_not_in_results_skipped(self) -> None:
        node = {"depends_on": ["n1"]}
        result = task_graph_context_from_deps(node, {})
        self.assertEqual(result, "")

    def test_dep_with_result_included(self) -> None:
        node = {"depends_on": ["n1"]}
        node_results = {
            "n1": {"tool": "browser", "output": "page content here"}
        }
        result = task_graph_context_from_deps(node, node_results)
        self.assertIn("n1", result)
        self.assertIn("page content here", result)

    def test_dep_tool_shown(self) -> None:
        node = {"depends_on": ["n1"]}
        node_results = {"n1": {"tool": "browser", "output": "data"}}
        result = task_graph_context_from_deps(node, node_results)
        self.assertIn("browser", result)

    def test_multiple_deps_joined(self) -> None:
        node = {"depends_on": ["n1", "n2"]}
        node_results = {
            "n1": {"tool": "browser", "output": "first output"},
            "n2": {"tool": "reasoning", "output": "second output"},
        }
        result = task_graph_context_from_deps(node, node_results)
        self.assertIn("first output", result)
        self.assertIn("second output", result)

    def test_missing_output_handled(self) -> None:
        node = {"depends_on": ["n1"]}
        node_results = {"n1": {"tool": "browser"}}  # no "output" key
        result = task_graph_context_from_deps(node, node_results)
        self.assertIsInstance(result, str)

    def test_none_output_handled(self) -> None:
        node = {"depends_on": ["n1"]}
        node_results = {"n1": {"tool": "browser", "output": None}}
        result = task_graph_context_from_deps(node, node_results)
        self.assertIsInstance(result, str)


# planner_runtime.py - build_task_graph_state_blob

class BuildTaskGraphStateBlobTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(build_task_graph_state_blob([]), str)

    def test_empty_log_returns_empty(self) -> None:
        self.assertEqual(build_task_graph_state_blob([]), "")

    def test_single_item_formatted(self) -> None:
        log = [{"id": "n1", "tool": "reasoning", "ok": True, "goal": "analyze", "output": "done"}]
        result = build_task_graph_state_blob(log)
        self.assertIn("n1", result)
        self.assertIn("reasoning", result)
        self.assertIn("OK", result)
        self.assertIn("done", result)

    def test_failed_item_shows_fail(self) -> None:
        log = [{"id": "n1", "tool": "browser", "ok": False, "goal": "fetch", "output": "error"}]
        result = build_task_graph_state_blob(log)
        self.assertIn("FAIL", result)

    def test_goal_included(self) -> None:
        log = [{"id": "n1", "tool": "reasoning", "ok": True, "goal": "my special goal", "output": "out"}]
        result = build_task_graph_state_blob(log)
        self.assertIn("my special goal", result)

    def test_multiple_items_all_present(self) -> None:
        log = [
            {"id": "n1", "tool": "browser", "ok": True, "goal": "goal1", "output": "out1"},
            {"id": "n2", "tool": "reasoning", "ok": True, "goal": "goal2", "output": "out2"},
        ]
        result = build_task_graph_state_blob(log)
        self.assertIn("n1", result)
        self.assertIn("n2", result)
        self.assertIn("out1", result)
        self.assertIn("out2", result)

    def test_result_max_30000_chars(self) -> None:
        log = [{"id": "n1", "tool": "reasoning", "ok": True, "goal": "g", "output": "X" * 50000}]
        result = build_task_graph_state_blob(log)
        self.assertLessEqual(len(result), 30000)

    def test_missing_output_key_handled(self) -> None:
        log = [{"id": "n1", "tool": "reasoning", "ok": True, "goal": "g"}]
        result = build_task_graph_state_blob(log)
        self.assertIsInstance(result, str)


# orchestrator_postprocess_runtime.py - build_finalize_prompt

class BuildFinalizePromptTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(build_finalize_prompt(task="task"), str)

    def test_nonempty(self) -> None:
        result = build_finalize_prompt(task="task")
        self.assertTrue(len(result) > 0)

    def test_contains_task(self) -> None:
        result = build_finalize_prompt(task="analyze python code")
        self.assertIn("analyze python code", result)

    def test_different_tasks_different_prompts(self) -> None:
        r1 = build_finalize_prompt(task="task one")
        r2 = build_finalize_prompt(task="task two")
        self.assertNotEqual(r1, r2)


# terminal_tool.py - is_dangerous_command

class IsDangerousCommandTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(is_dangerous_command("ls"), bool)

    def test_safe_command_false(self) -> None:
        self.assertFalse(is_dangerous_command("ls -la"))

    def test_empty_string_false(self) -> None:
        self.assertFalse(is_dangerous_command(""))

    def test_none_false(self) -> None:
        self.assertFalse(is_dangerous_command(None))  # type: ignore[arg-type]

    def test_rm_rf_root_dangerous(self) -> None:
        self.assertTrue(is_dangerous_command("rm -rf /"))

    def test_shutdown_dangerous(self) -> None:
        self.assertTrue(is_dangerous_command("shutdown"))

    def test_reboot_dangerous(self) -> None:
        self.assertTrue(is_dangerous_command("reboot"))

    def test_format_c_dangerous(self) -> None:
        self.assertTrue(is_dangerous_command("format c:"))

    def test_mkfs_dangerous(self) -> None:
        self.assertTrue(is_dangerous_command("mkfs"))

    def test_deltree_dangerous(self) -> None:
        self.assertTrue(is_dangerous_command("deltree"))

    def test_git_status_safe(self) -> None:
        self.assertFalse(is_dangerous_command("git status"))

    def test_pip_list_safe(self) -> None:
        self.assertFalse(is_dangerous_command("pip list"))

    def test_case_insensitive(self) -> None:
        # Function lowercases before checking
        self.assertTrue(is_dangerous_command("SHUTDOWN"))

    def test_blocked_as_substring(self) -> None:
        # "shutdown" appears as substring
        self.assertTrue(is_dangerous_command("sudo shutdown -h now"))


if __name__ == "__main__":
    unittest.main()
