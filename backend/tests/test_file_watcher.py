"""Tests for the realtime project file watcher.

Where possible these are pure unit tests of the debouncing logic
and path-filtering rules. There's also one integration test that
spins up a real `Observer` against a temp directory and verifies
the start/stop lifecycle — that's slow (~1s) but the only way to
catch threading bugs in the watcher itself.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class PathFilteringTest(unittest.TestCase):
    """The handler must skip files outside the allow-list and inside
    SKIP_DIRS. Tested in isolation without standing up a real Observer."""

    def setUp(self) -> None:
        from app.application.code_agent.file_watcher import DebouncedHandler
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "node_modules").mkdir()
        (self.root / ".git").mkdir()
        self.handler = DebouncedHandler(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_allowed_python_file(self) -> None:
        p = self.root / "src" / "foo.py"
        p.write_text("x=1\n", encoding="utf-8")
        self.assertTrue(self.handler._should_handle(str(p)))

    def test_allowed_markdown_file(self) -> None:
        p = self.root / "README.md"
        p.write_text("# hi\n", encoding="utf-8")
        self.assertTrue(self.handler._should_handle(str(p)))

    def test_excluded_extension(self) -> None:
        p = self.root / "src" / "image.png"
        p.write_text("not actually a png", encoding="utf-8")
        self.assertFalse(self.handler._should_handle(str(p)))

    def test_excluded_node_modules(self) -> None:
        p = self.root / "node_modules" / "lib.js"
        p.parent.mkdir(exist_ok=True)
        p.write_text("module.exports = 1", encoding="utf-8")
        self.assertFalse(self.handler._should_handle(str(p)))

    def test_excluded_git_dir(self) -> None:
        p = self.root / ".git" / "HEAD"
        p.write_text("ref: refs/heads/main\n", encoding="utf-8")
        self.assertFalse(self.handler._should_handle(str(p)))

    def test_file_outside_project_root_rejected(self) -> None:
        # A file in some completely unrelated directory must not pass.
        outside = Path(self._tmp.name).parent / "other_outside.py"
        outside.write_text("x=1\n", encoding="utf-8")
        try:
            self.assertFalse(self.handler._should_handle(str(outside)))
        finally:
            outside.unlink(missing_ok=True)


class DebounceTest(unittest.TestCase):
    """Rapid-fire events on the same path must coalesce into one
    reindex call after DEBOUNCE_SECS of quiet."""

    def setUp(self) -> None:
        # Make debounce snappier so the test isn't slow
        from app.application.code_agent import file_watcher
        self._old_debounce = file_watcher.DEBOUNCE_SECS
        file_watcher.DEBOUNCE_SECS = 0.15

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        self.handler = file_watcher.DebouncedHandler(self.root)
        self.file_watcher = file_watcher

    def tearDown(self) -> None:
        self.file_watcher.DEBOUNCE_SECS = self._old_debounce
        self._tmp.cleanup()

    def test_multiple_events_collapsed_to_one_call(self) -> None:
        target = self.root / "src" / "a.py"
        target.write_text("x=1", encoding="utf-8")
        with patch.object(self.file_watcher, "reindex_file", return_value={"ok": True}) as mock:
            # Fire 5 events in quick succession
            for _ in range(5):
                self.handler._schedule(str(target), deleted=False)
                time.sleep(0.02)
            # Wait for the debounce + a margin
            time.sleep(0.4)
        # Despite 5 events, only one reindex_file call survived.
        self.assertEqual(mock.call_count, 1)

    def test_different_files_dont_coalesce(self) -> None:
        a = self.root / "src" / "a.py"; a.write_text("x=1", encoding="utf-8")
        b = self.root / "src" / "b.py"; b.write_text("y=2", encoding="utf-8")
        with patch.object(self.file_watcher, "reindex_file", return_value={"ok": True}) as mock:
            self.handler._schedule(str(a), deleted=False)
            self.handler._schedule(str(b), deleted=False)
            time.sleep(0.4)
        # Two distinct files → two distinct reindex calls
        self.assertEqual(mock.call_count, 2)

    def test_deletion_routes_to_unindex(self) -> None:
        target = self.root / "src" / "deleted.py"
        target.write_text("x=1", encoding="utf-8")
        with patch.object(self.file_watcher, "unindex_file", return_value={"ok": True}) as un_mock, \
             patch.object(self.file_watcher, "reindex_file", return_value={"ok": True}) as re_mock:
            self.handler._schedule(str(target), deleted=True)
            time.sleep(0.4)
        un_mock.assert_called_once()
        re_mock.assert_not_called()

    def test_oversized_file_treated_as_delete(self) -> None:
        """A file that grew past MAX_FILE_BYTES between save and
        reindex shouldn't be indexed — drop its old chunks instead."""
        target = self.root / "src" / "big.py"
        # Write content over the cap
        target.write_text("x = 1\n" * 50000, encoding="utf-8")
        self.assertGreater(target.stat().st_size, self.file_watcher.MAX_FILE_BYTES)
        with patch.object(self.file_watcher, "unindex_file", return_value={"ok": True}) as un_mock, \
             patch.object(self.file_watcher, "reindex_file", return_value={"ok": True}) as re_mock:
            self.handler._schedule(str(target), deleted=False)
            time.sleep(0.4)
        un_mock.assert_called_once()
        re_mock.assert_not_called()


class WatcherLifecycleTest(unittest.TestCase):
    """Real Observer against a real temp dir — verifies start →
    file event → reindex_file invocation → stop."""

    def setUp(self) -> None:
        os.environ["ELIRA_DATA_DIR"] = tempfile.mkdtemp()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        from app.application.code_agent import file_watcher
        self._old_debounce = file_watcher.DEBOUNCE_SECS
        file_watcher.DEBOUNCE_SECS = 0.2
        self.file_watcher = file_watcher

    def tearDown(self) -> None:
        # Make sure nothing leaks
        self.file_watcher.stop_watcher(self.root)
        self.file_watcher.DEBOUNCE_SECS = self._old_debounce
        self._tmp.cleanup()
        os.environ.pop("ELIRA_DATA_DIR", None)

    def test_start_then_stop_idempotent(self) -> None:
        r1 = self.file_watcher.start_watcher(self.root)
        self.assertTrue(r1["ok"])
        self.assertFalse(r1.get("already_watching"))
        # Second call → no-op success
        r2 = self.file_watcher.start_watcher(self.root)
        self.assertTrue(r2["ok"])
        self.assertTrue(r2["already_watching"])
        # Stop → was_watching=True
        s = self.file_watcher.stop_watcher(self.root)
        self.assertTrue(s["ok"])
        self.assertTrue(s["was_watching"])
        # Stop again → was_watching=False
        s2 = self.file_watcher.stop_watcher(self.root)
        self.assertTrue(s2["ok"])
        self.assertFalse(s2["was_watching"])

    def test_status_reflects_active_state(self) -> None:
        status_before = self.file_watcher.watcher_status(self.root)
        self.assertFalse(status_before["watching"])
        self.file_watcher.start_watcher(self.root)
        status_during = self.file_watcher.watcher_status(self.root)
        self.assertTrue(status_during["watching"])
        self.assertEqual(status_during["project_root"], str(self.root))

    def test_file_edit_triggers_reindex(self) -> None:
        """The full pipeline: start observer → modify file → debounce →
        reindex_file gets called with the right arguments."""
        with patch.object(self.file_watcher, "reindex_file", return_value={"ok": True}) as mock:
            self.file_watcher.start_watcher(self.root)
            target = self.root / "src" / "hello.py"
            target.write_text("print('first')\n", encoding="utf-8")
            # Give the observer + debounce a chance to fire
            time.sleep(0.7)
        # Could be 1+ depending on platform's filesystem event spam,
        # but never 0
        self.assertGreaterEqual(mock.call_count, 1)
        called_paths = [str(call.args[1]) for call in mock.call_args_list]
        self.assertTrue(any("hello.py" in p for p in called_paths))


if __name__ == "__main__":
    unittest.main()
