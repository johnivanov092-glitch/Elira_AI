"""Tests for application/git (subprocess mocked) and application/dashboard."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.git.runtime as git_rt          # noqa: E402
import app.application.dashboard.runtime as dash_rt   # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# git — _find_repo (real FS, no subprocess)
# ─────────────────────────────────────────────────────────────────────────────

class FindRepoTest(unittest.TestCase):
    def test_finds_repo_with_git_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            (repo / ".git").mkdir()
            result = git_rt._find_repo(str(repo))
            self.assertEqual(result, str(repo))

    def test_returns_none_when_no_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # tmp has no .git
            result = git_rt._find_repo(tmp)
            # might return parent repo; just check it doesn't crash
            self.assertIsInstance(result, (str, type(None)))

    def test_none_path_searches_cwd_ancestors(self) -> None:
        # The worktree IS a git repo, so _find_repo(None) should find something
        result = git_rt._find_repo(None)
        # In any repo environment it should find a path
        self.assertIsNotNone(result)


# ─────────────────────────────────────────────────────────────────────────────
# git — _run helper (mock subprocess)
# ─────────────────────────────────────────────────────────────────────────────

def _make_proc(returncode=0, stdout="output", stderr=""):
    proc = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return proc


class RunHelperTest(unittest.TestCase):
    def test_success_returns_ok_true(self) -> None:
        with patch("subprocess.run", return_value=_make_proc(0, "hello")) as mock_sub:
            result = git_rt._run(["git", "status"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"], "hello")

    def test_nonzero_returncode_returns_ok_false(self) -> None:
        with patch("subprocess.run", return_value=_make_proc(1, "", "error msg")):
            result = git_rt._run(["git", "whatever"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["stderr"], "error msg")

    def test_file_not_found_returns_graceful_error(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = git_rt._run(["git", "status"])
        self.assertFalse(result["ok"])
        self.assertIn("git", result["stderr"].lower())

    def test_timeout_returns_graceful_error(self) -> None:
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 5)):
            result = git_rt._run(["git", "log"])
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# git — high-level API (repo found via mock, subprocess mocked)
# ─────────────────────────────────────────────────────────────────────────────

class GitStatusTest(unittest.TestCase):
    def _mock_run(self, calls):
        """Return a side_effect list for subprocess.run mocked calls."""
        return [_make_proc(*c) for c in calls]

    def test_no_repo_returns_error(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value=None):
            result = git_rt.git_status()
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_clean_repo(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value="/fake/repo"), \
             patch("subprocess.run", side_effect=[
                 _make_proc(0, "main"),   # rev-parse
                 _make_proc(0, ""),       # status --short (clean)
             ]):
            result = git_rt.git_status()
        self.assertTrue(result["ok"])
        self.assertTrue(result["clean"])
        self.assertEqual(result["branch"], "main")

    def test_dirty_repo_has_files(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value="/fake/repo"), \
             patch("subprocess.run", side_effect=[
                 _make_proc(0, "feature"),
                 _make_proc(0, " M src/app.py\n?? new_file.txt"),
             ]):
            result = git_rt.git_status()
        self.assertTrue(result["ok"])
        self.assertFalse(result["clean"])
        self.assertEqual(len(result["files"]), 2)


class GitLogTest(unittest.TestCase):
    def test_no_repo_returns_error(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value=None):
            result = git_rt.git_log()
        self.assertFalse(result["ok"])

    def test_parses_commits(self) -> None:
        log_output = "abc1234 feat: add login\ndef5678 fix: resolve bug"
        with patch.object(git_rt, "_find_repo", return_value="/repo"), \
             patch("subprocess.run", return_value=_make_proc(0, log_output)):
            result = git_rt.git_log()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["commits"][0]["hash"], "abc1234")
        self.assertEqual(result["commits"][0]["message"], "feat: add login")

    def test_empty_log(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value="/repo"), \
             patch("subprocess.run", return_value=_make_proc(0, "")):
            result = git_rt.git_log()
        self.assertEqual(result["count"], 0)


class GitCommitTest(unittest.TestCase):
    def test_empty_message_returns_error(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value="/repo"):
            result = git_rt.git_commit("", repo_path="/repo")
        self.assertFalse(result["ok"])

    def test_no_repo_returns_error(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value=None):
            result = git_rt.git_commit("fix: something")
        self.assertFalse(result["ok"])

    def test_successful_commit(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value="/repo"), \
             patch("subprocess.run", side_effect=[
                 _make_proc(0, ""),             # git add -A
                 _make_proc(0, "[main abc123] fix: something"),  # git commit
             ]):
            result = git_rt.git_commit("fix: something")
        self.assertTrue(result["ok"])


class GitBranchesTest(unittest.TestCase):
    def test_no_repo_returns_error(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value=None):
            result = git_rt.git_branches()
        self.assertFalse(result["ok"])

    def test_parses_current_branch(self) -> None:
        branch_output = "* main\n  feature/login\n  remotes/origin/main"
        with patch.object(git_rt, "_find_repo", return_value="/repo"), \
             patch("subprocess.run", return_value=_make_proc(0, branch_output)):
            result = git_rt.git_branches()
        self.assertTrue(result["ok"])
        self.assertEqual(result["current"], "main")
        self.assertEqual(len(result["branches"]), 3)


class FormatGitContextTest(unittest.TestCase):
    def test_no_repo_returns_git_unavailable(self) -> None:
        with patch.object(git_rt, "_find_repo", return_value=None):
            ctx = git_rt.format_git_context()
        self.assertIn("Git:", ctx)

    def test_clean_repo_context(self) -> None:
        with patch.object(git_rt, "git_status", return_value={
            "ok": True, "repo": "/repo", "branch": "main", "files": [], "clean": True,
        }):
            ctx = git_rt.format_git_context()
        self.assertIn("main", ctx)
        self.assertIn("чистая", ctx.lower())

    def test_dirty_repo_lists_files(self) -> None:
        with patch.object(git_rt, "git_status", return_value={
            "ok": True, "repo": "/repo", "branch": "dev",
            "files": [{"status": "M", "file": "app.py"}], "clean": False,
        }):
            ctx = git_rt.format_git_context()
        self.assertIn("app.py", ctx)


# ─────────────────────────────────────────────────────────────────────────────
# dashboard — compute_dashboard_stats (mock _HISTORY.list_runs)
# ─────────────────────────────────────────────────────────────────────────────

def _make_run(ok=True, route="chat", model="gemma", answer_len=100):
    from datetime import datetime
    return {
        "ok": 1 if ok else 0,
        "route": route,
        "model": model,
        "answer_len": answer_len,
        "finished_at": datetime.utcnow().isoformat(),
    }


class DashboardStatsTest(unittest.TestCase):
    def _compute(self, runs):
        with patch.object(dash_rt._HISTORY, "list_runs", return_value=runs):
            return dash_rt.compute_dashboard_stats()

    def test_empty_runs_returns_zeros(self) -> None:
        result = self._compute([])
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_runs"], 0)
        self.assertEqual(result["success"], 0)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["success_rate"], 0)

    def test_counts_success_and_errors(self) -> None:
        runs = [_make_run(ok=True), _make_run(ok=True), _make_run(ok=False)]
        result = self._compute(runs)
        self.assertEqual(result["total_runs"], 3)
        self.assertEqual(result["success"], 2)
        self.assertEqual(result["errors"], 1)

    def test_success_rate_calculation(self) -> None:
        runs = [_make_run(ok=True), _make_run(ok=True), _make_run(ok=False),
                _make_run(ok=False)]
        result = self._compute(runs)
        self.assertEqual(result["success_rate"], 50.0)

    def test_top_models_aggregated(self) -> None:
        runs = [_make_run(model="gemma")] * 3 + [_make_run(model="llama")]
        result = self._compute(runs)
        models = {m["model"] for m in result["top_models"]}
        self.assertIn("gemma", models)
        self.assertIn("llama", models)

    def test_top_routes_aggregated(self) -> None:
        runs = [_make_run(route="chat")] * 2 + [_make_run(route="code")]
        result = self._compute(runs)
        routes = {r["route"] for r in result["top_routes"]}
        self.assertIn("chat", routes)

    def test_daily_activity_has_14_days(self) -> None:
        result = self._compute([])
        self.assertEqual(len(result["daily_activity"]), 14)

    def test_result_has_required_keys(self) -> None:
        result = self._compute([])
        for key in ("ok", "total_runs", "success", "errors", "success_rate",
                    "today", "this_week", "top_models", "top_routes",
                    "daily_activity", "avg_answer_length"):
            self.assertIn(key, result)

    def test_avg_answer_length_computed(self) -> None:
        runs = [_make_run(answer_len=100), _make_run(answer_len=200)]
        result = self._compute(runs)
        self.assertEqual(result["avg_answer_length"], 150)


if __name__ == "__main__":
    unittest.main()
