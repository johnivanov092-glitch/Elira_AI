"""P1.4 — backup-on-edit (recoverability foundation).

Every overwrite/edit of an EXISTING file snapshots its original bytes to a
bounded backup (one per file path) so the last change is recoverable
(single-step undo). New-file creation has nothing to back up.
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

from app.application.code_agent.tools._files import (  # noqa: E402
    tool_edit_file,
    tool_read_file,
    tool_write_file,
)


class EditBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("line1\nOLD\nline3\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_edit_creates_recoverable_backup(self) -> None:
        res = tool_edit_file(self.root, path="a.txt", old_string="OLD", new_string="NEW")
        self.assertNotIn("ERROR", res["text"])
        backup = res.get("backup_path")
        self.assertIsNotNone(backup)
        backup_bytes = Path(backup).read_bytes()
        self.assertIn(b"OLD", backup_bytes)        # original recoverable
        self.assertNotIn(b"NEW", backup_bytes)
        # file itself now has the new content
        self.assertIn("NEW", tool_read_file(self.root, path="a.txt")["text"])

    def test_overwrite_backs_up_original(self) -> None:
        res = tool_write_file(self.root, path="a.txt", content="brand new content")
        self.assertEqual(res["diff_action"], "overwrite")
        backup = res.get("backup_path")
        self.assertIsNotNone(backup)
        self.assertIn(b"OLD", Path(backup).read_bytes())

    def test_new_file_has_no_backup(self) -> None:
        res = tool_write_file(self.root, path="fresh.txt", content="x")
        self.assertEqual(res["diff_action"], "create")
        self.assertIsNone(res.get("backup_path"))


if __name__ == "__main__":
    unittest.main()
