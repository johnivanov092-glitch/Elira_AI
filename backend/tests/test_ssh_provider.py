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


def _bproc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> CompletedProcess:
    """capture_output=True yields BYTES — the read/write cores and verifiers all
    consume raw bytes, so their tests must mock bytes, not str."""
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

    def test_run_returns_exit_code_and_semantic_ok(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "hi", "")):
            r = self.ssh.tool_ssh_run(host="prod-1", command="echo hi")
        self.assertEqual(r["exit_code"], 0)
        self.assertTrue(r["ok"])
        with patch("subprocess.run", return_value=_proc(3, "", "boom")):
            r2 = self.ssh.tool_ssh_run(host="prod-1", command="does-not-exist")
        self.assertEqual(r2["exit_code"], 3)
        self.assertFalse(r2["ok"])           # non-zero exit → failure, not "it ran"

    def test_all_error_branches_return_ok_false(self) -> None:
        # bad host / empty command — no subprocess at all
        for r in (self.ssh.tool_ssh_run(host="evil", command="ls"),
                  self.ssh.tool_ssh_run(host="prod-1", command="   ")):
            self.assertIn("ERROR", r["text"])
            self.assertFalse(r["ok"])
        # timeout / ssh-missing / generic exception
        for exc, needle in (
            (TimeoutExpired(cmd="ssh", timeout=5), "timed out"),
            (FileNotFoundError(), "not found"),
            (RuntimeError("boom"), "ERROR"),
        ):
            with patch("subprocess.run", side_effect=exc):
                r = self.ssh.tool_ssh_run(host="prod-1", command="ls")
            self.assertIn(needle, r["text"])
            self.assertFalse(r["ok"])

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
        # Content IS in stdin (now sent as UTF-8 bytes — no text=True)
        self.assertEqual(mock.call_args.kwargs["input"], nasty.encode("utf-8"))

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


# ── ssh_run_ps (Windows PowerShell, base64 EncodedCommand) ─────


class SshRunPsTest(SshProviderTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.ssh_acl.set_allowed_hosts(["prod-1"])

    def test_script_sent_base64_utf16le_not_raw(self) -> None:
        import base64

        script = "Get-Content 'C:\\a.ps1' | Where-Object { $_ -notmatch 'X' } | Set-Content 'C:\\a.ps1'"
        with patch("subprocess.run", return_value=_proc(0, "ok", "")) as mock:
            r = self.ssh.tool_ssh_run_ps(host="prod-1", script=script)
        cmd = mock.call_args[0][0][-1]
        # The raw script (with its quotes/pipes/$_) must NOT be in the wire command —
        # only the base64 blob is, so cmd.exe/ssh never parse the body.
        self.assertNotIn("$_", cmd)
        self.assertNotIn("Where-Object", cmd)
        self.assertIn("-EncodedCommand", cmd)
        expected_b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        self.assertIn(expected_b64, cmd)
        self.assertEqual(r["touched_host"], "prod-1")

    def test_batchmode_flags_present(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "", "")) as mock:
            self.ssh.tool_ssh_run_ps(host="prod-1", script="Get-Date")
        argv = mock.call_args[0][0]
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=accept-new", argv)

    def test_host_not_in_allowlist_rejected_without_subprocess(self) -> None:
        with patch("subprocess.run") as mock:
            r = self.ssh.tool_ssh_run_ps(host="evil", script="Get-Date")
        self.assertIn("ERROR", r["text"])
        mock.assert_not_called()

    def test_empty_script_rejected(self) -> None:
        with patch("subprocess.run") as mock:
            r = self.ssh.tool_ssh_run_ps(host="prod-1", script="   ")
        self.assertIn("ERROR", r["text"])
        mock.assert_not_called()

    def test_timeout_clamped(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "", "")) as mock:
            self.ssh.tool_ssh_run_ps(host="prod-1", script="Get-Date", timeout=99999)
        self.assertLessEqual(mock.call_args.kwargs["timeout"], 600)

    def test_run_ps_returns_exit_code_and_semantic_ok(self) -> None:
        with patch("subprocess.run", return_value=_proc(0, "ok", "")):
            r = self.ssh.tool_ssh_run_ps(host="prod-1", script="Get-Date")
        self.assertEqual(r["exit_code"], 0)
        self.assertTrue(r["ok"])
        with patch("subprocess.run", return_value=_proc(1, "", "err")):
            r2 = self.ssh.tool_ssh_run_ps(host="prod-1", script="throw 'x'")
        self.assertEqual(r2["exit_code"], 1)
        self.assertFalse(r2["ok"])

    def test_all_error_branches_return_ok_false(self) -> None:
        # bad host / empty script — no subprocess at all
        for r in (self.ssh.tool_ssh_run_ps(host="evil", script="Get-Date"),
                  self.ssh.tool_ssh_run_ps(host="prod-1", script="   ")):
            self.assertIn("ERROR", r["text"])
            self.assertFalse(r["ok"])
        # timeout / ssh-missing / generic exception
        for exc, needle in (
            (TimeoutExpired(cmd="ssh", timeout=5), "timed out"),
            (FileNotFoundError(), "not found"),
            (RuntimeError("boom"), "ERROR"),
        ):
            with patch("subprocess.run", side_effect=exc):
                r = self.ssh.tool_ssh_run_ps(host="prod-1", script="Get-Date")
            self.assertIn(needle, r["text"])
            self.assertFalse(r["ok"])


# ── high-level primitives: replace / assert / port_check ───────


class SshPrimitivesTest(SshProviderTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.ssh_acl.set_allowed_hosts(["prod-1"])

    def test_replace_reads_edits_and_writes_back(self) -> None:
        content = b"line1\r\nContent-Length: 5\r\nline3\r\n"
        with patch("subprocess.run", side_effect=[_bproc(0, content), _bproc(0, b"")]) as mock:
            r = self.ssh.tool_ssh_replace(host="prod-1", path="/f", old="Content-Length: 5\r\n", new="")
        self.assertEqual(r["touched_path"], "/f")
        self.assertIn("заменено 1", r["text"])
        # the write got the EDITED bytes via stdin, and Content-Length is gone
        write_input = mock.call_args_list[1].kwargs["input"]
        self.assertNotIn(b"Content-Length", write_input)

    def test_replace_noop_when_pattern_absent_makes_no_write(self) -> None:
        with patch("subprocess.run", side_effect=[_bproc(0, b"nothing here")]) as mock:
            r = self.ssh.tool_ssh_replace(host="prod-1", path="/f", old="ZZZ", new="Y")
        self.assertNotIn("touched_path", r)
        self.assertFalse(r["ok"])
        self.assertEqual(mock.call_count, 1)  # read only, no write attempted

    def test_replace_rejects_bad_host_without_subprocess(self) -> None:
        with patch("subprocess.run") as mock:
            r = self.ssh.tool_ssh_replace(host="evil", path="/f", old="a", new="b")
        self.assertIn("ERROR", r["text"])
        mock.assert_not_called()

    def test_replace_rejects_empty_old(self) -> None:
        r = self.ssh.tool_ssh_replace(host="prod-1", path="/f", old="", new="x")
        self.assertIn("ERROR", r["text"])

    def test_windows_path_writes_via_powershell_no_cat_probe(self) -> None:
        # A C:\ path goes STRAIGHT to PowerShell — never a destructive `cat >`.
        with patch("subprocess.run", side_effect=[_bproc(0, b"")]) as mock:
            r = self.ssh.tool_ssh_write(host="prod-1", path="C:\\a.txt", content="hi")
        self.assertNotIn("ERROR", r["text"])
        self.assertEqual(mock.call_count, 1)                       # one call, no probe
        wire = mock.call_args_list[0][0][0][-1]
        self.assertIn("-EncodedCommand", wire)                     # via PowerShell
        self.assertNotIn("cat", wire)
        self.assertEqual(mock.call_args_list[0].kwargs["input"], b"hi")  # content via stdin

    def test_windows_write_failure_never_probes_with_cat(self) -> None:
        # P1: even if the PowerShell write FAILS, a Windows path must NEVER be
        # probed with `cat > C:\...` (which would truncate the target).
        with patch("subprocess.run", side_effect=[_bproc(1, b"", b"Access is denied")]) as mock:
            r = self.ssh.tool_ssh_write(host="prod-1", path="C:\\a.txt", content="hi")
        self.assertIn("ERROR", r["text"])                          # failure reported honestly
        self.assertEqual(mock.call_count, 1)                       # no second (cat) attempt
        self.assertNotIn("cat", mock.call_args_list[0][0][0][-1])

    def test_windows_overwrite_is_atomic_temp_then_move(self) -> None:
        # P1(4): overwrite writes a temp then Move-replaces — a failed write leaves
        # the original untouched (no zeroed target).
        import base64 as _b64
        with patch("subprocess.run", side_effect=[_bproc(0, b"")]) as mock:
            self.ssh.tool_ssh_write(host="prod-1", path="C:\\a.txt", content="hi")
        wire = mock.call_args_list[0][0][0][-1]
        script = _b64.b64decode(wire.split("-EncodedCommand ", 1)[1].strip()).decode("utf-16-le")
        self.assertIn(".elira-tmp", script)                        # writes to temp first
        self.assertIn("Move-Item", script)                         # then atomic replace

    def test_replace_on_windows_never_calls_cat(self) -> None:
        content = b"Content-Length: 5\r\nok\r\n"
        head_win = _bproc(1, b"", b"'head' is not recognized")     # POSIX read probe fails
        read_ps = _bproc(0, content)                               # windows read fallback
        write_ps = _bproc(0, b"")                                  # windows write (direct)
        with patch("subprocess.run", side_effect=[head_win, read_ps, write_ps]) as mock:
            r = self.ssh.tool_ssh_replace(host="prod-1", path="C:\\a.ps1", old="Content-Length: 5\r\n", new="")
        self.assertEqual(r["touched_path"], "C:\\a.ps1")
        for call in mock.call_args_list:
            self.assertNotIn("cat >", call[0][0][-1])              # no destructive cat anywhere
        self.assertIn("-EncodedCommand", mock.call_args_list[-1][0][0][-1])  # write via PS

    def test_assert_contains_true_and_false(self) -> None:
        with patch("subprocess.run", return_value=_bproc(0, b"has Content-Length here")):
            self.assertTrue(self.ssh.tool_ssh_assert_contains(host="prod-1", path="/f", pattern="Content-Length")["ok"])
        with patch("subprocess.run", return_value=_bproc(0, b"clean")):
            self.assertFalse(self.ssh.tool_ssh_assert_contains(host="prod-1", path="/f", pattern="Content-Length")["ok"])

    def test_assert_not_contains_is_the_inverse(self) -> None:
        with patch("subprocess.run", return_value=_bproc(0, b"clean file")):
            self.assertTrue(self.ssh.tool_ssh_assert_not_contains(host="prod-1", path="/f", pattern="Content-Length")["ok"])
        with patch("subprocess.run", return_value=_bproc(0, b"has Content-Length")):
            self.assertFalse(self.ssh.tool_ssh_assert_not_contains(host="prod-1", path="/f", pattern="Content-Length")["ok"])

    def test_port_check_listening_and_not(self) -> None:
        with patch("subprocess.run", return_value=_bproc(0, b"LISTENING 127.0.0.1:18080 pid=4488")):
            r = self.ssh.tool_ssh_port_check(host="prod-1", port=18080)
        self.assertTrue(r["ok"])
        with patch("subprocess.run", return_value=_bproc(0, b"NOT-LISTENING")):
            r2 = self.ssh.tool_ssh_port_check(host="prod-1", port=18080)
        self.assertFalse(r2["ok"])

    def test_port_check_rejects_out_of_range(self) -> None:
        r = self.ssh.tool_ssh_port_check(host="prod-1", port=99999)
        self.assertIn("ERROR", r["text"])

    def test_port_check_encodes_powershell_base64(self) -> None:
        with patch("subprocess.run", return_value=_bproc(0, b"NOT-LISTENING")) as mock:
            self.ssh.tool_ssh_port_check(host="prod-1", port=18080)
        cmd = mock.call_args[0][0][-1]
        self.assertIn("-EncodedCommand", cmd)  # no raw quoting on the wire


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

    def test_provider_exposes_all_tools(self) -> None:
        self.ssh_acl.set_allowed_hosts(["x"])
        provider = self.ssh.SshToolProvider()
        names = {s["function"]["name"] for s in provider.get_schemas()}
        self.assertEqual(
            names,
            {"ssh_run", "ssh_read", "ssh_write", "ssh_run_ps", "ssh_replace",
             "ssh_assert_contains", "ssh_assert_not_contains", "ssh_port_check",
             "ssh_list_hosts"},
        )

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
