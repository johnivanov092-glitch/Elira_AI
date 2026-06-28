"""P0.2 — path containment regression lock.

Containment is centralised in one chokepoint, `tools._sandbox._resolve_safe`,
used by every model-facing fs tool (read/write/edit/glob/grep/project_map/
read_image/ocr_file/content tools). These tests pin that chokepoint and the
main write tools so a future tool that forgets `_resolve_safe` — or a
regression in the helper — is caught: any path escaping the project root must
raise SandboxError before any I/O.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._sandbox import SandboxError, _resolve_safe  # noqa: E402
from app.application.code_agent.tools._files import (  # noqa: E402
    tool_edit_file,
    tool_read_file,
    tool_write_file,
)


class ResolveSafeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rejects_parent_escape(self) -> None:
        with self.assertRaises(SandboxError):
            _resolve_safe(self.root, "../escape.txt")

    def test_rejects_deep_parent_escape(self) -> None:
        with self.assertRaises(SandboxError):
            _resolve_safe(self.root, "../../../../etc/passwd")

    def test_rejects_absolute_outside(self) -> None:
        outside = Path(tempfile.gettempdir()).resolve() / "definitely_outside_xyz.txt"
        with self.assertRaises(SandboxError):
            _resolve_safe(self.root, str(outside))

    def test_accepts_in_project(self) -> None:
        resolved = _resolve_safe(self.root, "sub/a.txt")
        self.assertTrue(str(resolved).startswith(str(self.root.resolve())))

    def test_normalizes_dotdot_that_stays_inside(self) -> None:
        # "sub/../a.txt" normalises to "<root>/a.txt" — inside, so allowed.
        resolved = _resolve_safe(self.root, "sub/../a.txt")
        self.assertEqual(resolved, (self.root / "a.txt").resolve())


class ToolContainmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "in.txt").write_text("hello", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_read_file_escape_blocked(self) -> None:
        with self.assertRaises(SandboxError):
            tool_read_file(self.root, path="../../escape.txt")

    def test_write_file_escape_blocked(self) -> None:
        with self.assertRaises(SandboxError):
            tool_write_file(self.root, path="../escape.txt", content="x")

    def test_edit_file_escape_blocked(self) -> None:
        with self.assertRaises(SandboxError):
            tool_edit_file(self.root, path="../escape.txt", old_string="a", new_string="b")

    def test_in_project_read_works(self) -> None:
        out = tool_read_file(self.root, path="in.txt")
        self.assertIn("hello", out["text"])


if __name__ == "__main__":
    unittest.main()
