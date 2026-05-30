"""Tests for SshToolProvider.

The real ssh binary isn't called — every subprocess.run is patched.
What we're verifying is the security/contract layer:

  * Allowlist gating: every tool refuses hosts not in the list
  * Argument validation: empty/whitespace/metacharacters rejected
  * Stdin used for write content (so no shell escaping of body)
  * `ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new`
    always present in the argv we hand to subprocess
  * Provider auto-disables when allowlist is empty
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess, TimeoutExpired


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


class SshProviderTestBase(unittest.TestCase):
    """Common setup: every test gets a fresh ELIRA_DATA_DIR + a
    reloaded ssh_acl module, so persistence is isolated."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name
        from app.core import data_files
        importlib.reload(data_files)
        from app.application.tool_providers import ssh_acl
        importlib.reload(ssh_acl)
        from app.application.tool_providers import ssh_provider
        importlib.reload(ssh_provider)
        self.ssh_acl = ssh_acl
        self.ssh = ssh_provider

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("ELIRA_DATA_DIR", None)


# ── ACL ─────────────────────────────────────────────────────────


class AclTest(SshProviderTestBase):
    def test_empty_by_default(self) -> None:
        self.assertEqual(self.ssh_acl.get_allowed_hosts(), [])
        self.assertFalse(self.ssh_acl.is_ssh_enabled())

    def test_set_and_get_round_trip(self) -> None:
        result = self.ssh_acl.set_allowed_hosts(["prod-1", "staging.example"])
        self.assertEqual(result, ["prod-1", "staging.example"])
        self.assertEqual(self.ssh_acl.get_allowed_hosts(), ["prod-1", "staging.example"])
        self.assertTrue(self.ssh_acl.is_ssh_enabled())

    def test_normalization_strips_dedups_empty(self) -> None:
        result = self.ssh_acl.set_allowed_hosts(["  a  ", "b", "a", "", "b", "  "])
        self.assertEqual(result, ["a", "b"])

    def test_is_host_allowed_exact_match(self) -> None:
        self.ssh_acl.set_allowed_hosts(["prod"])
        self.assertTrue(self.ssh_acl.is_host_allowed("prod"))
        self.assertFalse(self.ssh_acl.is_host_allowed("PROD"))
        self.assertFalse(self.ssh_acl.is_host_allowed("prod-2"))
        self.assertFalse(self.ssh_acl.is_host_allowed(""))

    def test_persistence_across_imports(self) -> None:
        self.ssh_acl.set_allowed_hosts(["host1"])
        # Reload to simulate process restart
        importlib.reload(self.ssh_acl)
        self.assertEqual(self.ssh_acl.get_allowed_hosts(), ["host1"])

    def test_set_empty_disables(self) -> None:
        self.ssh_acl.set_allowed_hosts(["x"])
        self.assertTrue(self.ssh_acl.is_ssh_enabled())
        self.ssh_acl.set_allowed_hosts([])
        self.assertFalse(self.ssh_acl.is_ssh_enabled())


# ── ssh_run ────────────────────────────────────────────────────


class SshRunTest(SshProviderTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.ssh_acl.set_allowed_hosts(["prod-1"])

    def test_unknown_host_rejected_without_subprocess(self) -> None:
        with patch("subprocess.run") as mock:
            result = self.ssh.tool_ssh_run(host="not-in-list", command="ls")
        self.assertIn("ERROR", result["text"])
        self.assertIn("allowlist", result["text"])
        mock.assert_not_called()  # never touches the network

    def test_empty_host_rejected(self) -> None:
        result = self.ssh.tool_ssh_run(host="", command="ls")
        self.assertIn("ERROR", result["text"])

    def test_empty_command_rejected(self) -> None:
        result = self.ssh.tool_ssh_run(host="prod-1", command="")
        self.assertIn("ERROR", result["text"])

    def test_host_with_shell_meta_rejected(self) -> None:
        for bad_host in ("prod-1; rm -rf /", "prod-1 && evil", "prod`whoami`", "a$b"):
            with patch("subprocess.run") as mock:
                result = self.ssh.tool_ssh_run(host=bad_host, command="ls")
            self.assertIn("ERROR", result["text"], f"failed for {bad_host!r}")
            mock.assert_not_called()

    def test_successful_run_returns_stdout_and_exit(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "hello\n", "")) as mock:
            result = self.ssh.tool_ssh_run(host="prod-1", command="echo hello")
        self.assertNotIn("ERROR", result["text"])
        self.assertIn("hello", result["text"])
        self.assertIn("exit=0", result["text"])
        # Verify the ssh invocation flags
        argv = mock.call_args[0][0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=accept-new", argv)
        self.assertIn("prod-1", argv)
        self.assertIn("echo hello", argv)

    def test_nonzero_exit_propagates(self) -> None:
        with patch("subprocess.run", return_value=_proc(2, "", "permission denied")):
            result = self.ssh.tool_ssh_run(host="prod-1", command="cat /etc/shadow")
        self.assertIn("exit=2", result["text"])
        self.assertIn("permission denied", result["text"])

    def test_timeout_returns_error(self) -> None:
        with patch("subprocess.run", side_effect=TimeoutExpired(cmd="ssh", timeout=5)):
            result = self.ssh.tool_ssh_run(host="prod-1", command="sleep 100", timeout=5)
        self.assertIn("ERROR", result["text"])
        self.assertIn("timed out", result["text"])

    def test_ssh_binary_missing_returns_error(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = self.ssh.tool_ssh_run(host="prod-1", command="ls")
        self.assertIn("ERROR", result["text"])
        self.assertIn("ssh", result["text"].lower())

    def test_timeout_clamped(self) -> None:
        """Caller can't request 1-hour-plus timeouts."""
        with patch("subprocess.run", return_value=_proc(0, "", "")) as mock:
            self.ssh.tool_ssh_run(host="prod-1", command="x", timeout=99999)
        self.assertLessEqual(mock.call_args.kwargs["timeout"], 600)

    def test_stdout_truncated_for_llm_when_huge(self) -> None:
        big = "X" * 30000
        with patch("subprocess.run", return_value=_proc(0, big, "")):
            result = self.ssh.tool_ssh_run(host="prod-1", command="x")
        self.assertLess(len(result["text"]), 20000)
        self.assertIn("truncated", result["text"])


# ── ssh_read ───────────────────────────────────────────────────


class SshReadTest(SshProviderTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.ssh_acl.set_allowed_hosts(["prod-1"])

    def test_unknown_host_rejected(self) -> None:
        with patch("subprocess.run") as mock:
            r = self.ssh.tool_ssh_read(host="other", path="/etc/hosts")
        self.assertIn("ERROR", r["text"])
        mock.assert_not_called()

    def test_path_shell_quoted_against_injection(self) -> None:
        """If the agent passes a path with single quotes, it must
        be safely escaped — not blow up the remote shell."""
        with patch("subprocess.run", return_value=_proc(0, "ok", "")) as mock:
            self.ssh.tool_ssh_read(host="prod-1", path="/tmp/a'b.txt")
        argv = mock.call_args[0][0]
        # Last arg is the remote shell command containing head -c N -- '<quoted>'
        cmd = argv[-1]
        # Check that the single quote was escaped (POSIX form: '"'"')
        self.assertIn("'\"'\"'", cmd)
        # The quoted path is bounded by single quotes
        self.assertIn("/tmp/a", cmd)

    def test_returns_file_body_with_header(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "127.0.0.1 localhost\n", "")):
            r = self.ssh.tool_ssh_read(host="prod-1", path="/etc/hosts")
        self.assertIn("[ssh:prod-1:/etc/hosts]", r["text"])
        self.assertIn("localhost", r["text"])

    def test_truncation_marker_when_file_exceeds_cap(self) -> None:
        # head -c (cap+1) returned (cap+1) bytes → marker added
        with patch("subprocess.run", return_value=_proc(0, "X" * 200, "")):
            r = self.ssh.tool_ssh_read(host="prod-1", path="/big", max_chars=100)
        self.assertIn("truncated", r["text"])

    def test_remote_failure_returns_error(self) -> None:
        with patch("subprocess.run", return_value=_proc(1, "", "No such file")):
            r = self.ssh.tool_ssh_read(host="prod-1", path="/missing")
        self.assertIn("ERROR", r["text"])
        self.assertIn("No such file", r["text"])


# ── ssh_write ──────────────────────────────────────────────────


class SshWriteTest(SshProviderTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.ssh_acl.set_allowed_hosts(["prod-1"])

    def test_unknown_host_rejected(self) -> None:
        with patch("subprocess.run") as mock:
            r = self.ssh.tool_ssh_write(host="other", path="/tmp/x", content="data")
        self.assertIn("ERROR", r["text"])
        mock.assert_not_called()

    def test_content_sent_via_stdin_not_argv(self) -> None:
        """Body must travel through stdin so embedded shell metas
        and newlines in content never touch shell parsing."""
        nasty = "line1\n$evil; rm -rf /\n`whoami`\nline3"
        with patch("subprocess.run", return_value=_proc(0, "", "")) as mock:
            self.ssh.tool_ssh_write(host="prod-1", path="/tmp/x.txt", content=nasty)
        argv = mock.call_args[0][0]
        # Content NOT in argv anywhere
        for arg in argv:
            self.assertNotIn("$evil", arg)
            self.assertNotIn("`whoami`", arg)
        # Content IS in stdin
        self.assertEqual(mock.call_args.kwargs["input"], nasty)

    def test_append_flag_uses_double_redirect(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "", "")) as mock:
            self.ssh.tool_ssh_write(host="prod-1", path="/log", content="msg", append=True)
        cmd = mock.call_args[0][0][-1]
        self.assertIn(">>", cmd)
        self.assertNotIn("cat > ", cmd.replace(">>", "##"))  # no single-> form

    def test_overwrite_uses_single_redirect(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "", "")) as mock:
            self.ssh.tool_ssh_write(host="prod-1", path="/log", content="msg")
        cmd = mock.call_args[0][0][-1]
        # `cat > 'path'` — exactly one >
        self.assertIn(" > ", cmd)
        self.assertNotIn(">>", cmd)

    def test_oversize_content_rejected_without_subprocess(self) -> None:
        with patch("subprocess.run") as mock:
            r = self.ssh.tool_ssh_write(host="prod-1", path="/x", content="X" * 200000)
        self.assertIn("ERROR", r["text"])
        self.assertIn("exceeds", r["text"])
        mock.assert_not_called()

    def test_non_string_content_rejected(self) -> None:
        # Type check at the boundary
        r = self.ssh.tool_ssh_write(host="prod-1", path="/x", content=12345)  # type: ignore[arg-type]
        self.assertIn("ERROR", r["text"])


# ── ssh_list_hosts ─────────────────────────────────────────────


class SshListHostsTest(SshProviderTestBase):
    def test_empty_allowlist_says_disabled(self) -> None:
        r = self.ssh.tool_ssh_list_hosts()
        self.assertIn("disabled", r["text"].lower())

    def test_lists_hosts_when_allowed(self) -> None:
        self.ssh_acl.set_allowed_hosts(["a", "b"])
        r = self.ssh.tool_ssh_list_hosts()
        self.assertIn("- a", r["text"])
        self.assertIn("- b", r["text"])


# ── Provider integration with ToolRegistry ─────────────────────


class SshProviderIntegrationTest(SshProviderTestBase):
    def test_provider_disabled_when_allowlist_empty(self) -> None:
        provider = self.ssh.SshToolProvider()
        self.assertFalse(provider.is_enabled())

    def test_provider_enabled_after_set_allowed_hosts(self) -> None:
        self.ssh_acl.set_allowed_hosts(["x"])
        provider = self.ssh.SshToolProvider()
        self.assertTrue(provider.is_enabled())

    def test_provider_exposes_all_four_tools(self) -> None:
        self.ssh_acl.set_allowed_hosts(["x"])
        provider = self.ssh.SshToolProvider()
        names = {s["function"]["name"] for s in provider.get_schemas()}
        self.assertEqual(names, {"ssh_run", "ssh_read", "ssh_write", "ssh_list_hosts"})

    def test_registry_skips_disabled_provider(self) -> None:
        from app.application.tool_providers import ToolRegistry
        # allowlist empty → SSH disabled → its tools shouldn't appear
        reg = ToolRegistry([self.ssh.SshToolProvider()])
        self.assertEqual(reg.collect_schemas(), [])
        self.assertEqual(reg.known_tools(), set())

    def test_registry_includes_enabled_ssh(self) -> None:
        from app.application.tool_providers import ToolRegistry
        self.ssh_acl.set_allowed_hosts(["host"])
        reg = ToolRegistry([self.ssh.SshToolProvider()])
        names = reg.known_tools()
        self.assertIn("ssh_run", names)
        self.assertIn("ssh_list_hosts", names)

    def test_dispatch_via_registry_validates_host(self) -> None:
        """End-to-end: registry → SshProvider.dispatch → ACL check."""
        from app.application.tool_providers import ToolRegistry
        self.ssh_acl.set_allowed_hosts(["allowed"])
        reg = ToolRegistry([self.ssh.SshToolProvider()])
        # Disallowed host — error returned, no subprocess call.
        with patch("subprocess.run") as mock:
            result = reg.dispatch("ssh_run", {"host": "denied", "command": "ls"})
        self.assertIn("allowlist", result.tool_meta["text"])
        mock.assert_not_called()

    def test_unknown_ssh_tool_returns_error(self) -> None:
        provider = self.ssh.SshToolProvider()
        r = provider.dispatch("ssh_telekinesis", {})
        self.assertIn("ERROR", r["text"])


if __name__ == "__main__":
    unittest.main()
