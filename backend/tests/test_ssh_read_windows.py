from __future__ import annotations

import base64
import unittest
from unittest.mock import MagicMock, patch

from app.application.tool_providers import ssh_provider


def _proc(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class SshReadWindowsFallbackTest(unittest.TestCase):
    def _read(self, side_effects):
        with patch.object(ssh_provider, "_validate_host", return_value=None), patch.object(
            ssh_provider.subprocess, "run", side_effect=side_effects
        ) as run:
            res = ssh_provider.tool_ssh_read(host="home-srv01", path="C:\\AgentLab\\agent-lab.ps1")
        return res, run

    def test_windows_fallback_uses_encodedcommand(self) -> None:
        head_fail = _proc(
            9009, stderr=b"'head' is not recognized as an internal or external command,"
        )
        win_ok = _proc(0, stdout="param($x)\r\nWrite-Host hi".encode("utf-8"))
        res, run = self._read([head_fail, win_ok])
        self.assertEqual(run.call_count, 2, "should fall back to a second ssh call")
        second_argv = run.call_args_list[1].args[0]
        self.assertTrue(
            any("-EncodedCommand" in str(a) for a in second_argv),
            "Windows fallback must use powershell -EncodedCommand",
        )
        self.assertIn("Write-Host hi", res["text"])

    def test_linux_happy_path_no_fallback(self) -> None:
        head_ok = _proc(0, stdout=b"#!/bin/sh\necho hi")
        res, run = self._read([head_ok])
        self.assertEqual(run.call_count, 1, "Linux read must not trigger the Windows fallback")
        self.assertIn("echo hi", res["text"])

    def test_non_windows_error_is_not_masked(self) -> None:
        # A genuine POSIX error (missing file) must NOT trigger the fallback.
        head_err = _proc(1, stderr=b"head: cannot open 'x' for reading: No such file or directory")
        res, run = self._read([head_err])
        self.assertEqual(run.call_count, 1)
        self.assertIn("No such file", res["text"])

    def test_signature_detection(self) -> None:
        self.assertTrue(ssh_provider._looks_like_windows_no_cmd(b"'head' is not recognized"))
        self.assertTrue(
            ssh_provider._looks_like_windows_no_cmd(
                "\"head\" не является внутренней или внешней командой"
            )
        )
        self.assertFalse(ssh_provider._looks_like_windows_no_cmd(b"head: No such file or directory"))
        self.assertFalse(ssh_provider._looks_like_windows_no_cmd(None))

    def test_encoded_command_decodes_to_reader_script(self) -> None:
        cmd = ssh_provider._windows_read_encoded("C:\\A\\b'.ps1", 100)
        b64 = cmd.split("-EncodedCommand ")[1].strip()
        script = base64.b64decode(b64).decode("utf-16-le")
        self.assertIn("ReadAllBytes", script)
        self.assertIn("C:\\A\\b''.ps1", script)  # single-quote doubled for PS literal
        self.assertIn("100", script)


if __name__ == "__main__":
    unittest.main()
