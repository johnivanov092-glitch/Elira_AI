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

    def _valid_env(self, root, reg, extra=None):
        env = {"PATH": os.environ.get("PATH", ""),
               "ELIRA_CHANGE_STORE_PATH": str(Path(root) / "change.sqlite3"),
               "ELIRA_CHANGE_REGISTRY_PATH": reg, "ELIRA_CHANGE_BOT_TOKEN": "tok",
               "ELIRA_CHANGE_EXECUTOR_ROOT": root, "ELIRA_CHANGE_IPC_TOKEN_FILE": str(Path(root) / "ipc.token"),
               "ITOPS_CHANGE_APPROVER_USER_IDS": "42", "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}
        if extra:
            env.update(extra)
        return env

    def _reg_inside(self, root):
        kh, idf = str(Path(root) / "kh"), str(Path(root) / "id")
        Path(kh).write_text("srv key\n", encoding="utf-8")
        Path(idf).write_text("KEY\n", encoding="utf-8")
        reg = str(Path(root) / "registry.json")
        Path(reg).write_text(json.dumps({"targets": {"ai-server-netdata": {
            "host": "h", "port": 22, "remote_user": "elira-change", "known_hosts": kh,
            "identity_file": idf, "unit": "netdata.service", "operation": "restart"}}}), encoding="utf-8")
        return reg

    def test_sensitive_path_outside_root_flagged(self):
        # P1-C: store/registry/token/known_hosts/key OUTSIDE the executor root leave a writable
        # ancestor unchecked → must be flagged even if the file's immediate parent is safe.
        root = str(Path(self._tmp) / "exec_root")
        Path(root).mkdir()
        outside = str(Path(self._tmp) / "outside")
        Path(outside).mkdir()
        reg = self._reg_inside(root)
        env = self._valid_env(root, reg, {"ELIRA_CHANGE_STORE_PATH": str(Path(outside) / "change.sqlite3")})
        with unittest.mock.patch.object(preflight, "_writable_by_others", return_value=False), \
                unittest.mock.patch.object(preflight, "_recursive_writable", return_value=None), \
                unittest.mock.patch.object(preflight, "_has_git_ancestor", return_value=False), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            problems = preflight.verify(registry_path=reg)
        self.assertTrue(any("NOT inside ELIRA_CHANGE_EXECUTOR_ROOT" in p and "change.sqlite3" in p
                            for p in problems), problems)

    def test_syspath_importable_zip_file_checked(self):
        # P1-B: an importable archive (zip/egg) is a FILE on sys.path — skipping non-directories
        # would let a writable archive shadow code. It must be checked per-file.
        zip_path = str(Path(self._tmp) / "shadow.zip")
        Path(zip_path).write_bytes(b"PK\x03\x04")
        checked: list[str] = []

        def spy(path):
            checked.append(os.path.normpath(str(path)))
            return False
        env = self._valid_env(self._tmp, "/r")
        with unittest.mock.patch.object(sys, "path", list(sys.path) + [zip_path]), \
                unittest.mock.patch.object(preflight, "_recursive_writable", return_value=None), \
                unittest.mock.patch.object(preflight, "_writable_by_others", side_effect=spy), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            preflight.verify(registry_path="/r")
        self.assertIn(os.path.normpath(zip_path), checked)   # the archive file was ACL-checked

    def test_syspath_base_prefix_confusion_walks_per_file(self):
        # P1-B: `<base>_evil` shares a string prefix with base_prefix but is NOT contained in
        # it. The old startswith() treated it as base stdlib (dir-level, effectively skipped);
        # a path-aware check classifies it as non-base and walks it PER-FILE (recursive).
        fake_base = Path(self._tmp) / "pybase"
        fake_base.mkdir()
        evil = Path(self._tmp) / "pybase_evil"          # startswith(fake_base) but not within it
        evil.mkdir()
        recursed: list[str] = []

        def rec_spy(root_dir, owner_check=None):
            recursed.append(os.path.normpath(str(root_dir)))
            return None
        env = self._valid_env(self._tmp, "/r")
        with unittest.mock.patch.object(sys, "path", list(sys.path) + [str(evil)]), \
                unittest.mock.patch.object(sys, "base_prefix", str(fake_base)), \
                unittest.mock.patch.object(preflight, "_recursive_writable", side_effect=rec_spy), \
                unittest.mock.patch.object(preflight, "_writable_by_others", return_value=False), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            preflight.verify(registry_path="/r")
        self.assertIn(os.path.normpath(str(evil)), recursed)   # per-file walk, not skipped as base

    def test_recursive_writable_flags_escaping_dir_symlink(self):
        # P3 (adversarial review): os.walk does not descend into a directory SYMLINK, so a
        # symlinked subdir whose target escapes the walked root must be flagged rather than
        # silently skipped. Mocked so it needs no OS symlink-create privilege.
        root = str(Path(self._tmp) / "tree")
        Path(root).mkdir()
        link = os.path.join(root, "shadow")
        outside = str(Path(self._tmp) / "evil_target")   # target OUTSIDE root

        with unittest.mock.patch("os.walk", side_effect=lambda top: iter([(root, ["shadow"], [])])), \
                unittest.mock.patch("os.path.islink",
                                    side_effect=lambda p: os.path.normpath(str(p)) == os.path.normpath(link)), \
                unittest.mock.patch("os.path.realpath",
                                    side_effect=lambda p: outside if os.path.normpath(str(p)) == os.path.normpath(link)
                                    else os.path.normpath(str(p))), \
                unittest.mock.patch.object(preflight, "_writable_by_others", return_value=False):
            hit = preflight._recursive_writable(root)
        self.assertEqual(os.path.normpath(hit or ""), os.path.normpath(link))   # escaping symlink flagged

    def test_recursive_writable_ignores_in_root_safe_symlink(self):
        # A symlink whose target stays inside root and is not writable is NOT a problem
        # (the real subtree is walked directly by os.walk).
        root = str(Path(self._tmp) / "tree2")
        Path(root).mkdir()
        link = os.path.join(root, "inside")
        target = os.path.join(root, "real")              # target INSIDE root

        with unittest.mock.patch("os.walk", side_effect=lambda top: iter([(root, ["inside"], [])])), \
                unittest.mock.patch("os.path.islink",
                                    side_effect=lambda p: os.path.normpath(str(p)) == os.path.normpath(link)), \
                unittest.mock.patch("os.path.realpath",
                                    side_effect=lambda p: target if os.path.normpath(str(p)) == os.path.normpath(link)
                                    else os.path.normpath(str(p))), \
                unittest.mock.patch.object(preflight, "_writable_by_others", return_value=False):
            self.assertIsNone(preflight._recursive_writable(root))

    # --- ownership / running-as identity (P1-b) ------------------------------------------
    def test_identity_running_as_mismatch_flagged(self):
        root = str(Path(self._tmp) / "r"); Path(root).mkdir()
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": "M\\exec"}), \
                unittest.mock.patch.object(preflight.os, "name", "nt"), \
                unittest.mock.patch.object(preflight, "_account_sid", return_value="S-1-EXPECTED"), \
                unittest.mock.patch.object(preflight, "_current_sid", return_value="S-1-OTHER"), \
                unittest.mock.patch.object(preflight, "_owner_sid", return_value="S-1-EXPECTED"):
            probs = preflight._identity_problems(root, [])
        self.assertTrue(any("NOT running as" in p for p in probs), probs)

    def test_identity_owner_mismatch_flagged(self):
        root = str(Path(self._tmp) / "r2"); Path(root).mkdir()

        def owner(p):
            same = os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(root))
            return "S-1-MAIN" if same else "S-1-EXPECTED"     # root owned by the main user
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": "M\\exec"}), \
                unittest.mock.patch.object(preflight.os, "name", "nt"), \
                unittest.mock.patch.object(preflight, "_account_sid", return_value="S-1-EXPECTED"), \
                unittest.mock.patch.object(preflight, "_current_sid", return_value="S-1-EXPECTED"), \
                unittest.mock.patch.object(preflight, "_owner_sid", side_effect=owner):
            probs = preflight._identity_problems(root, [])
        self.assertTrue(any("OWNED by a non-executor" in p and "r2" in p for p in probs), probs)

    def test_identity_all_match_clean(self):
        root = str(Path(self._tmp) / "r3"); Path(root).mkdir()
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": "M\\exec"}), \
                unittest.mock.patch.object(preflight.os, "name", "nt"), \
                unittest.mock.patch.object(preflight, "_account_sid", return_value="S-1-EXPECTED"), \
                unittest.mock.patch.object(preflight, "_current_sid", return_value="S-1-EXPECTED"), \
                unittest.mock.patch.object(preflight, "_owner_sid", return_value="S-1-EXPECTED"):
            self.assertEqual(preflight._identity_problems(root, []), [])

    def test_identity_unresolvable_account_flagged(self):
        root = str(Path(self._tmp) / "r4"); Path(root).mkdir()
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": "NOSUCH\\a"}), \
                unittest.mock.patch.object(preflight.os, "name", "nt"), \
                unittest.mock.patch.object(preflight, "_account_sid", return_value=None):
            probs = preflight._identity_problems(root, [])
        self.assertTrue(any("could not be resolved to a SID" in p for p in probs), probs)

    def test_identity_account_unset_is_noop(self):
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": ""}):
            self.assertEqual(preflight._identity_problems(str(self._tmp), []), [])

    def test_safe_writers_do_not_trust_current_runner(self):
        # The spoofable getpass/USERNAME path is gone: with NO account configured, the current
        # runner is NOT a trusted writer (only the SID-resolved system principals are).
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": "",
                                                   "USERNAME": "attacker", "USERDOMAIN": "EVIL"}):
            preflight._safe_names_by_account.pop("", None)
            names = preflight._safe_writer_names()
        self.assertNotIn("evil\\attacker", names)

    @unittest.skipUnless(os.name == "nt", "Windows SID resolution")
    def test_identity_real_ctypes_agree(self):
        import subprocess
        who = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
        cur = preflight._current_sid()
        self.assertTrue(cur and cur.startswith("S-1-"), cur)
        self.assertEqual(preflight._account_sid(who), cur)      # name -> same SID
        self.assertEqual(preflight._account_sid(cur), cur)      # raw SID string roundtrips
        f = Path(self._tmp) / "owned.txt"; f.write_text("x", encoding="utf-8")
        self.assertEqual(preflight._owner_sid(str(f)), cur)     # creator owns the file

    def test_recursive_writable_flags_misowned_file(self):
        # P1 (adversarial review): a file OWNED by a non-executor principal (Windows implicit
        # WRITE_DAC) must be caught per-file by the walk even when its DACL is clean.
        root = str(Path(self._tmp) / "own"); Path(root).mkdir()
        (Path(root) / "ok.py").write_text("x", encoding="utf-8")
        bad = Path(root) / "engine.py"; bad.write_text("x", encoding="utf-8")

        def oc(p):     # engine.py is misowned; everything else is fine
            return os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(str(bad)))
        with unittest.mock.patch.object(preflight, "_writable_by_others", return_value=False):
            hit = preflight._recursive_writable(root, oc)
        self.assertEqual(os.path.normcase(os.path.abspath(hit or "")),
                         os.path.normcase(os.path.abspath(str(bad))))

    def test_recursive_writable_owner_check_none_is_noop(self):
        root = str(Path(self._tmp) / "own2"); Path(root).mkdir()
        (Path(root) / "a.py").write_text("x", encoding="utf-8")
        with unittest.mock.patch.object(preflight, "_writable_by_others", return_value=False):
            self.assertIsNone(preflight._recursive_writable(root, None))

    def test_make_owner_check_trusts_only_account_and_system(self):
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": "M\\exec"}), \
                unittest.mock.patch.object(preflight.os, "name", "nt"), \
                unittest.mock.patch.object(preflight, "_account_sid", return_value="S-1-EXPECTED"):
            with unittest.mock.patch.object(preflight, "_owner_sid",
                                            side_effect=lambda p: "S-1-MAIN" if "bad" in p else "S-1-EXPECTED"):
                chk = preflight._make_owner_check()
                self.assertTrue(chk("C:/x/bad.py"))          # non-executor owner -> untrusted
                self.assertFalse(chk("C:/x/good.py"))        # executor owner -> trusted
            with unittest.mock.patch.object(preflight, "_owner_sid", return_value="S-1-5-18"):
                self.assertFalse(preflight._make_owner_check()("C:/x/any"))   # SYSTEM -> trusted
            with unittest.mock.patch.object(preflight, "_owner_sid", return_value=None):
                self.assertTrue(preflight._make_owner_check()("C:/x/any"))    # unreadable -> fail-closed

    def test_make_owner_check_unset_account_is_none(self):
        with unittest.mock.patch.dict(os.environ, {"ELIRA_CHANGE_EXECUTOR_ACCOUNT": ""}):
            self.assertIsNone(preflight._make_owner_check())

    def test_malformed_allowlist_reported(self):
        env = {"PATH": os.environ.get("PATH", ""), "ELIRA_CHANGE_STORE_PATH": "/s",
               "ELIRA_CHANGE_REGISTRY_PATH": "/r", "ELIRA_CHANGE_BOT_TOKEN": "tok",
               "ITOPS_CHANGE_APPROVER_USER_IDS": "42,abc", "ITOPS_CHANGE_APPROVER_CHAT_IDS": "100"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            problems = preflight.verify(registry_path="/r")
        self.assertTrue(any("APPROVER_USER_IDS empty or malformed" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
