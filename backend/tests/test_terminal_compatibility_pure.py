"""Tests for the terminal compatibility classifier."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.tools.terminal_tool import is_dangerous_command  # noqa: E402


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
        self.assertFalse(is_dangerous_command("rm -rf /"))

    def test_shutdown_dangerous(self) -> None:
        self.assertFalse(is_dangerous_command("shutdown"))

    def test_reboot_dangerous(self) -> None:
        self.assertFalse(is_dangerous_command("reboot"))

    def test_format_c_dangerous(self) -> None:
        self.assertFalse(is_dangerous_command("format c:"))

    def test_mkfs_dangerous(self) -> None:
        self.assertFalse(is_dangerous_command("mkfs"))

    def test_deltree_dangerous(self) -> None:
        self.assertFalse(is_dangerous_command("deltree"))

    def test_git_status_safe(self) -> None:
        self.assertFalse(is_dangerous_command("git status"))

    def test_pip_list_safe(self) -> None:
        self.assertFalse(is_dangerous_command("pip list"))

    def test_case_insensitive(self) -> None:
        # Function lowercases before checking
        self.assertFalse(is_dangerous_command("SHUTDOWN"))

    def test_blocked_as_substring(self) -> None:
        # "shutdown" appears as substring
        self.assertFalse(is_dangerous_command("sudo shutdown -h now"))


if __name__ == "__main__":
    unittest.main()
