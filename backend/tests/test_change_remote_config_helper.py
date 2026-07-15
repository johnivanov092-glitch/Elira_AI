"""Behavior tests for the root-owned Netdata config helper over real temp files."""
from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import remote_netdata_config as helper  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _show(pid: int = 100) -> bytes:
    return ("Id=netdata.service\nLoadState=loaded\nActiveState=active\n"
            f"SubState=running\nMainPID={pid}\n").encode()


class FakeSystemctl:
    def __init__(self, *, restart_codes: list[int] | None = None,
                 mutate_on_restart=None, show_pids: list[int] | None = None):
        self.restart_codes = list(restart_codes or [0])
        self.mutate_on_restart = mutate_on_restart
        self.show_pids = list(show_pids or [])
        self.restarts = 0
        self.shows = 0

    def __call__(self, argv, **_kwargs):
        if "restart" in argv:
            self.restarts += 1
            if self.mutate_on_restart:
                self.mutate_on_restart(self.restarts)
            code = self.restart_codes[min(self.restarts - 1, len(self.restart_codes) - 1)]
            return subprocess.CompletedProcess(argv, code, b"", b"")
        self.shows += 1
        pid = (self.show_pids[min(self.shows - 1, len(self.show_pids) - 1)]
               if self.show_pids else 100 + self.shows)
        return subprocess.CompletedProcess(argv, 0, _show(pid), b"")


class RemoteHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = self.tmp / "netdata.conf"
        self.state = self.tmp / "state"
        self.state.mkdir()
        (self.state / ".lock").write_bytes(b"")
        self.original = b"# token=TOP-SECRET\n# stock netdata configuration\n"
        self.config.write_bytes(self.original)
        self.patches = (
            mock.patch.object(helper, "CONFIG_PATH", self.config),
            mock.patch.object(helper, "STATE_DIR", self.state),
            mock.patch.object(helper, "_fsync_dir", lambda _p: None),
        )
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def _inspect(self, runner=None):
        return helper.inspect(run=runner or FakeSystemctl())

    def _apply(self, runner, before, after):
        return helper.apply(
            run_id="chg-" + "a" * 32, before_sha256=before, after_sha256=after,
            run=runner, sleep=lambda _s: None, lock=lambda: contextlib.nullcontext())

    def test_inspect_projects_only_typed_fields_and_never_raw_config(self):
        out = self._inspect()
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["comment_only"])
        self.assertEqual(out["safe_settings"], {})
        self.assertEqual(out["desired_settings"], {"global.update_every": 1})
        self.assertNotIn("TOP-SECRET", json.dumps(out))
        self.assertNotIn("raw", out)

    def test_patcher_handles_existing_global_header_without_final_newline(self):
        self.assertEqual(helper._patched(b"[global]"), b"[global]\nupdate every = 1\n")

    def test_apply_hash_cas_snapshot_atomic_write_and_postcheck(self):
        preview = self._inspect()
        out = self._apply(FakeSystemctl(), preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "applied")
        self.assertFalse(out["rollback_attempted"])
        changed = self.config.read_text(encoding="utf-8")
        self.assertIn("[global]", changed)
        self.assertIn("update every = 1", changed)
        self.assertIn("TOP-SECRET", changed)  # unrelated comments preserved byte-for-byte
        snapshot = self.state / ("chg-" + "a" * 32 + ".snapshot")
        self.assertEqual(snapshot.read_bytes(), self.original)
        journal = json.loads((self.state / ("chg-" + "a" * 32 + ".json")).read_text())
        self.assertEqual(journal["status"], "applied")
        self.assertNotIn("TOP-SECRET", json.dumps(journal))

    def test_definite_restart_failure_restores_exact_snapshot(self):
        preview = self._inspect()
        runner = FakeSystemctl(restart_codes=[1, 0])
        out = self._apply(runner, preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "rolled_back")
        self.assertTrue(out["rollback_attempted"])
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(runner.restarts, 2)

    def test_rollback_cas_mismatch_is_locking_failure_and_never_overwrites_drift(self):
        preview = self._inspect()

        def drift(n):
            if n == 1:
                self.config.write_text("[global]\nupdate every = 2\n", encoding="utf-8")

        out = self._apply(FakeSystemctl(restart_codes=[1], mutate_on_restart=drift),
                          preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "rollback_failed")
        self.assertEqual(out["reason"], "rollback_cas_mismatch")
        self.assertIn("update every = 2", self.config.read_text(encoding="utf-8"))

    def test_rollback_requires_restart_evidence_not_only_healthy_old_process(self):
        preview = self._inspect()
        runner = FakeSystemctl(restart_codes=[1, 0], show_pids=[101, 101, 101, 101, 101, 101, 101])
        out = self._apply(runner, preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "rollback_failed")
        self.assertEqual(out["reason"], "rollback_postcheck_failed")

    def test_rollback_restart_must_change_the_immediately_previous_pid(self):
        preview = self._inspect()
        runner = FakeSystemctl(restart_codes=[0, 0], show_pids=[101, 102, 102])
        with mock.patch.object(
            helper, "_postcheck",
            return_value=(False, {"id": "netdata.service", "load_state": "loaded",
                                  "active_state": "active", "sub_state": "running",
                                  "main_pid": 102}),
        ):
            out = self._apply(runner, preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "rollback_failed")
        self.assertEqual(out["reason"], "rollback_postcheck_failed")

    def test_stale_before_hash_aborts_without_snapshot_or_write(self):
        preview = self._inspect()
        out = self._apply(FakeSystemctl(), "0" * 64, preview["planned_sha256"])
        self.assertEqual(out["status"], "aborted_before_apply")
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual([p.name for p in self.state.iterdir()], [".lock"])

    def test_drift_after_snapshot_is_rechecked_before_replace(self):
        preview = self._inspect()
        real_write_snapshot = helper._write_snapshot

        def snapshot_then_drift(run_id, data):
            path = real_write_snapshot(run_id, data)
            self.config.write_text("[global]\nupdate every = 2\n", encoding="utf-8")
            return path

        runner = FakeSystemctl()
        with mock.patch.object(helper, "_write_snapshot", side_effect=snapshot_then_drift):
            out = self._apply(runner, preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "aborted_before_apply")
        self.assertEqual(out["reason"], "config_drift")
        self.assertIn("update every = 2", self.config.read_text(encoding="utf-8"))
        self.assertEqual(runner.restarts, 0)

    def test_replace_then_fsync_failure_is_rolled_back_not_freed(self):
        preview = self._inspect()
        failed_config_fsync = False

        def fail_once_after_config_replace(path):
            nonlocal failed_config_fsync
            if Path(path) == self.config.parent and not failed_config_fsync:
                failed_config_fsync = True
                raise OSError("directory fsync failed after replace")

        runner = FakeSystemctl()
        with mock.patch.object(helper, "_fsync_dir", side_effect=fail_once_after_config_replace):
            out = self._apply(runner, preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "rolled_back")
        self.assertTrue(out["rollback_attempted"])
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_final_journal_failure_never_downgrades_rollback_failed(self):
        preview = self._inspect()

        def drift(n):
            if n == 1:
                self.config.write_text("[global]\nupdate every = 2\n", encoding="utf-8")

        real_write = helper._write_journal
        calls = 0

        def fail_final(run_id, payload):
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise OSError("disk full")
            return real_write(run_id, payload)

        with mock.patch.object(helper, "_write_journal", side_effect=fail_final):
            out = self._apply(FakeSystemctl(restart_codes=[1], mutate_on_restart=drift),
                              preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "rollback_failed")
        self.assertEqual(out["reason"], "rollback_cas_mismatch")

    def test_final_journal_failure_does_not_rollback_a_verified_apply(self):
        preview = self._inspect()
        real_write = helper._write_journal

        def fail_applied(run_id, payload):
            if payload.get("status") == "applied":
                raise OSError("journal disk full")
            return real_write(run_id, payload)

        runner = FakeSystemctl()
        with mock.patch.object(helper, "_write_journal", side_effect=fail_applied):
            out = self._apply(runner, preview["before_sha256"], preview["planned_sha256"])
        self.assertEqual(out["status"], "applied")
        self.assertFalse(out["rollback_attempted"])
        self.assertIn("update every = 1", self.config.read_text(encoding="utf-8"))
        self.assertEqual(runner.restarts, 1)


if __name__ == "__main__":
    unittest.main()
