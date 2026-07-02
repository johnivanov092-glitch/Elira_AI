from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class AuthNegativeTest(unittest.TestCase):
    """FIX-26: non-loopback callers are rejected without a valid token."""

    def test_non_loopback_without_token_rejected(self):
        from app.core.auth import is_authorized
        # LAN host, no token → denied
        self.assertFalse(is_authorized("192.168.1.50", None, token="secret", enabled=True))
        # LAN host, wrong token → denied
        self.assertFalse(is_authorized("192.168.1.50", "Bearer wrong", token="secret", enabled=True))

    def test_valid_token_and_loopback_allowed(self):
        from app.core.auth import is_authorized
        self.assertTrue(is_authorized("192.168.1.50", "Bearer secret", token="secret", enabled=True))
        self.assertTrue(is_authorized("127.0.0.1", None, token="secret", enabled=True))  # loopback trusted
        self.assertTrue(is_authorized("192.168.1.50", None, token="secret", enabled=False))  # auth off


class PathContainmentTest(unittest.TestCase):
    """FIX-30: _resolve_safe keeps every path inside the project root."""

    def setUp(self):
        from app.application.code_agent.tools._sandbox import _resolve_safe, SandboxError
        self._resolve_safe = _resolve_safe
        self.SandboxError = SandboxError

    def test_inside_root_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolved = self._resolve_safe(root, "sub/file.txt")
            self.assertEqual(resolved, (root / "sub" / "file.txt").resolve())

    def test_relative_traversal_escape_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(self.SandboxError):
                self._resolve_safe(Path(tmp), "../../../../etc/passwd")

    def test_absolute_outside_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.abspath(os.sep)  # filesystem root — never inside tmp
            with self.assertRaises(self.SandboxError):
                self._resolve_safe(Path(tmp), outside)

    def test_unc_path_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(self.SandboxError):
                self._resolve_safe(Path(tmp), r"\\some-server\share\secret.txt")

    def test_symlink_escape_blocked(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            link = root / "escape"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted on this platform/run")
            # resolve() follows the symlink to `outside`, which is outside root
            with self.assertRaises(self.SandboxError):
                self._resolve_safe(root, "escape/secret.txt")


if __name__ == "__main__":
    unittest.main()
