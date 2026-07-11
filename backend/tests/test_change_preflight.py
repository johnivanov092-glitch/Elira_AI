"""Executor isolation preflight — verifies (not just documents) that required config is
present/valid and refuses to run otherwise."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.change_executor import preflight  # noqa: E402


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def test_missing_config_reports_and_refuses(self):
        with unittest.mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            problems = preflight.verify(registry_path=str(Path(self._tmp) / "nope.json"))
        self.assertTrue(any("ELIRA_CHANGE_STORE_PATH" in p for p in problems))
        self.assertTrue(any("ELIRA_CHANGE_BOT_TOKEN" in p for p in problems))
        self.assertTrue(any("APPROVER_USER_IDS" in p for p in problems))
        self.assertTrue(any("registry invalid" in p for p in problems))
        with unittest.mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            with self.assertRaises(preflight.PreflightError):
                preflight.require_isolated(registry_path=str(Path(self._tmp) / "nope.json"))

    def test_missing_target_files_reported(self):
        reg = str(Path(self._tmp) / "registry.json")
        Path(reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "h", "port": 22, "remote_user": "elira-change",
            "known_hosts": str(Path(self._tmp) / "missing_known_hosts"),
            "identity_file": str(Path(self._tmp) / "missing_id"),
            "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "ELIRA_CHANGE_STORE_PATH": str(Path(self._tmp) / "s.db"),
               "ELIRA_CHANGE_REGISTRY_PATH": reg, "ELIRA_CHANGE_BOT_TOKEN": "tok",
               "ITOPS_CHANGE_APPROVER_USER_IDS": "42", "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            problems = preflight.verify(registry_path=reg)
        self.assertTrue(any("known_hosts missing" in p for p in problems))
        self.assertTrue(any("identity_file missing" in p for p in problems))

    def test_running_from_repo_is_not_a_false_green(self):
        # THE regression for the reported false-green: the executor package currently lives
        # inside the dev git tree (D:\...\backend\app\change_executor). Even with an
        # otherwise-valid config and an EXECUTOR_ROOT that does not contain it, preflight
        # MUST flag it (package not inside root AND/OR running from a git working tree).
        root = str(Path(self._tmp) / "exec_root")
        Path(root).mkdir()
        kh, idf = str(Path(self._tmp) / "kh"), str(Path(self._tmp) / "id")
        Path(kh).write_text("srv key\n", encoding="utf-8")
        Path(idf).write_text("KEY\n", encoding="utf-8")
        reg = str(Path(self._tmp) / "registry.json")
        Path(reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "h", "port": 22, "remote_user": "elira-change", "known_hosts": kh,
            "identity_file": idf, "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "ELIRA_CHANGE_STORE_PATH": str(Path(self._tmp) / "s.db"),
               "ELIRA_CHANGE_REGISTRY_PATH": reg, "ELIRA_CHANGE_BOT_TOKEN": "tok",
               "ELIRA_CHANGE_EXECUTOR_ROOT": root, "ITOPS_CHANGE_APPROVER_USER_IDS": "42",
               "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            problems = preflight.verify(registry_path=reg)
        self.assertNotEqual(problems, [])                                        # NOT a false-green
        self.assertTrue(any("NOT inside" in p or "git working tree" in p for p in problems), problems)

    def test_checks_interpreter_and_package_files(self):
        # regression for the reported gap (checked_executable=False, checked_package_file=False):
        # verify() must ACL-check sys.executable AND the individual package .py files.
        checked: list[str] = []

        def spy(path):
            checked.append(os.path.normpath(str(path)))
            return False
        env = {"PATH": os.environ.get("PATH", ""), "ELIRA_CHANGE_STORE_PATH": "/s",
               "ELIRA_CHANGE_REGISTRY_PATH": "/r", "ELIRA_CHANGE_BOT_TOKEN": "t",
               "ELIRA_CHANGE_EXECUTOR_ROOT": self._tmp, "ELIRA_CHANGE_IPC_TOKEN_FILE": "/tok",
               "ITOPS_CHANGE_APPROVER_USER_IDS": "42", "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}
        with unittest.mock.patch.object(preflight, "_writable_by_others", side_effect=spy), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            preflight.verify(registry_path="/r")
        self.assertIn(os.path.normpath(sys.executable), checked)            # interpreter checked
        pkg = os.path.normpath(str(Path(preflight.__file__).resolve().parent))
        self.assertTrue(any(c.startswith(pkg) and c.endswith(".py") for c in checked),  # package files
                        [c for c in checked if c.startswith(pkg)])

    def test_ace_parser_write_masks_and_full_name_matching(self):
        # safe writers are FULL domain\name (resolved from SIDs), never a bare leaf.
        safe = {"computername\\root", "nt authority\\system", "builtin\\administrators"}
        # write grants to a NON-safe principal → detected (incl. combined masks + the
        # previously-missed space-name / foreign-leaf cases):
        for line in ("Everyone:(RX,W)", "Everyone:(R,W)", "Everyone:(I)(RX,WD)",
                     "Everyone:(F)", "Everyone:(RX,WDAC)",
                     "BUILTIN\\Hyper-V Administrators:(M)",      # space-name no longer mis-parsed as Admins
                     "OTHERDOMAIN\\Root:(M)"):                   # foreign same-leaf not trusted
            self.assertTrue(preflight._ace_grants_write_to_nonowner(line, safe), line)
        # icacls first-line format ("<path> <account>:(perms)") — path prefix stripped:
        self.assertTrue(preflight._ace_grants_write_to_nonowner(
            "C:\\exe\\engine.py BUILTIN\\Hyper-V Administrators:(M)", safe, "C:\\exe\\engine.py"))
        # safe full names + read-only + deny → not a write grant:
        for line in ("BUILTIN\\Administrators:(F)", "NT AUTHORITY\\SYSTEM:(F)",
                     "COMPUTERNAME\\Root:(F)", "Everyone:(RX)", "Everyone:(R)", "Everyone:(DENY)(W)"):
            self.assertFalse(preflight._ace_grants_write_to_nonowner(line, safe), line)

    def test_malformed_allowlist_reported(self):
        env = {"PATH": os.environ.get("PATH", ""), "ELIRA_CHANGE_STORE_PATH": "/s",
               "ELIRA_CHANGE_REGISTRY_PATH": "/r", "ELIRA_CHANGE_BOT_TOKEN": "tok",
               "ITOPS_CHANGE_APPROVER_USER_IDS": "42,abc", "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            problems = preflight.verify(registry_path="/r")
        self.assertTrue(any("APPROVER_USER_IDS empty or malformed" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
