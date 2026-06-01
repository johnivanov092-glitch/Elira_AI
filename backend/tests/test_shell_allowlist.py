"""Tests — Shell read-only allowlist (P4 Шаг 11)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools import is_shell_safe  # noqa: E402


class TestIsShellSafe(unittest.TestCase):

    # ── Safe (read-only) ─────────────────────────────────────────────────────
    def test_git_status(self):       self.assertTrue(is_shell_safe("git status"))
    def test_git_status_short(self): self.assertTrue(is_shell_safe("git status --short"))
    def test_git_log(self):          self.assertTrue(is_shell_safe("git log --oneline -5"))
    def test_git_diff(self):         self.assertTrue(is_shell_safe("git diff HEAD"))
    def test_git_branch(self):       self.assertTrue(is_shell_safe("git branch -a"))
    def test_ls(self):               self.assertTrue(is_shell_safe("ls -la"))
    def test_cat(self):              self.assertTrue(is_shell_safe("cat README.md"))
    def test_grep(self):             self.assertTrue(is_shell_safe("grep -r TODO src/"))
    def test_pytest_collect(self):   self.assertTrue(is_shell_safe("pytest --collect-only -q"))
    def test_pip_list(self):         self.assertTrue(is_shell_safe("pip list"))
    def test_pip_show(self):         self.assertTrue(is_shell_safe("pip show requests"))
    def test_python_version(self):   self.assertTrue(is_shell_safe("python --version"))
    def test_echo(self):             self.assertTrue(is_shell_safe("echo hello"))
    def test_pwd(self):              self.assertTrue(is_shell_safe("pwd"))
    def test_find(self):             self.assertTrue(is_shell_safe("find . -name '*.py'"))
    def test_docker_ps(self):        self.assertTrue(is_shell_safe("docker ps"))
    def test_node_version(self):     self.assertTrue(is_shell_safe("node --version"))
    def test_case_insensitive(self): self.assertTrue(is_shell_safe("GIT STATUS"))

    # ── Unsafe (require approval or blocked) ─────────────────────────────────
    def test_pip_install(self):      self.assertFalse(is_shell_safe("pip install requests"))
    def test_rm(self):               self.assertFalse(is_shell_safe("rm -rf /tmp/test"))
    def test_git_push(self):         self.assertFalse(is_shell_safe("git push origin main"))
    def test_git_commit(self):       self.assertFalse(is_shell_safe("git commit -m msg"))
    def test_arbitrary_bash(self):   self.assertFalse(is_shell_safe("./deploy.sh"))
    def test_curl(self):             self.assertFalse(is_shell_safe("curl http://example.com"))
    def test_empty(self):            self.assertFalse(is_shell_safe(""))
    def test_write_file(self):       self.assertFalse(is_shell_safe("echo x > file.txt"))
    def test_python_exec(self):      self.assertFalse(is_shell_safe("python script.py"))


class TestExecutorBypassForSafeCommands(unittest.TestCase):
    """Executor skips approval gate for allowlisted run_bash commands."""

    def _exec(self, command: str, run_id: str = "run-shell-test"):
        from app.application.agent_kernel.executor import (
            ToolExecutionRequest,
            execute_tool,
        )
        _spec = {"permission": "require_approval", "max_output_chars": 50000}
        dispatch_calls = []

        def _dispatch(name, args):
            dispatch_calls.append(args.get("command", name))
            return {"ok": True, "text": f"executed {name}"}

        with mock.patch("app.application.tool_registry.runtime.get_tool",
                        return_value=_spec), \
             mock.patch("app.application.agent_registry.sandbox.preflight_or_raise",
                        return_value={"ok": True}), \
             mock.patch("app.application.monitoring.runtime.expire_old_approvals"), \
             mock.patch("app.application.monitoring.runtime.find_approved_approval",
                        return_value=None), \
             mock.patch("app.application.monitoring.runtime.create_approval",
                        return_value={"id": "fake-appr"}):
            result = execute_tool(
                ToolExecutionRequest(
                    run_id=run_id,
                    agent_id="code-agent",
                    project_scope_id="scope:test",
                    tool_name="run_bash",
                    args={"command": command},
                    source="code_agent",
                ),
                dispatch_fn=_dispatch,
            )
        return result, dispatch_calls

    def test_git_status_auto_executes(self):
        result, calls = self._exec("git status")
        self.assertEqual(result.status, "ok")
        self.assertIn("git status", calls)

    def test_ls_auto_executes(self):
        result, calls = self._exec("ls -la")
        self.assertEqual(result.status, "ok")

    def test_pip_install_requires_approval(self):
        result, calls = self._exec("pip install numpy")
        self.assertEqual(result.status, "waiting_approval")
        self.assertEqual(calls, [])

    def test_git_push_requires_approval(self):
        result, calls = self._exec("git push origin main")
        self.assertEqual(result.status, "waiting_approval")

    def test_pytest_collect_only_auto_executes(self):
        result, calls = self._exec("pytest --collect-only -q")
        self.assertEqual(result.status, "ok")


if __name__ == "__main__":
    unittest.main()
