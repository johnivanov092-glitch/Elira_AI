"""Deterministic Criterion Closure + Cleanup Barrier + Final Report (Ph7.12).

The runtime is a state machine: work → CLOSE open criteria with the exact verifier
calls → cleanup → final. These pin the live failure (run d8cd3092): the agent
ssh_read the file and declared the content verified, skipped ssh_assert_contains, and
cleaned up before proving setup — so the runtime must (1) compute the missing verifier
calls, (2) block a premature cleanup, and (3) own the final status block.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.taskspec import CriteriaTracker, TaskSpec  # noqa: E402
from app.application.code_agent import criterion_closure as cc  # noqa: E402

DIR = "C:\\AgentLabGlobalCanary"
SNAP = "C:\\AgentLabGlobalCanary\\snapshot.txt"


def _live_spec():
    return TaskSpec(success_criteria=[
        f"директория `{DIR}` существует",
        f"файл `{SNAP}` существует",
        f"файл `{SNAP}` содержит строку `project=frontend-global-live`",
        f"файл `{SNAP}` содержит строку `status=ok`",
        f"файл `{SNAP}` содержит строку `dom=verified`",
        f"временный файл `{DIR}` удалён после cleanup",
    ])


class MissingActionsTest(unittest.TestCase):
    def test_ssh_read_leaves_content_and_dir_open_with_exact_calls(self):
        t = CriteriaTracker.from_spec(_live_spec())
        # the agent only read the file → file_exists(snapshot) confirmed, rest open
        t.record(tool_name="ssh_read", args={"host": "home-srv01", "path": SNAP}, ok=True, evidence="read")
        acts = cc.missing_verifier_actions(t, host="home-srv01")
        tools = sorted(a["tool"] for a in acts)
        # dir exists (ssh_exists) + 3× content (ssh_assert_contains); cleanup not-exists
        self.assertIn("ssh_exists", tools)
        self.assertEqual(tools.count("ssh_assert_contains"), 3)
        self.assertIn("ssh_not_exists", tools)
        # the snapshot file_exists is NOT re-listed (ssh_read already confirmed it)
        blob = " ".join(a["call"] for a in acts)
        for line in ("project=frontend-global-live", "status=ok", "dom=verified"):
            self.assertIn(line, blob)
        self.assertIn("home-srv01", blob)

    def test_generic_and_viewport_have_no_missing_action(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "код не ломает существующие routes",       # generic
            "страница адаптивна для desktop и mobile",  # viewport_layout, no evidence
        ]))
        self.assertEqual(cc.missing_verifier_actions(t), [])

    def test_confirmed_criteria_produce_no_actions(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[f"файл `{SNAP}` существует"]))
        t.record(tool_name="ssh_exists", args={"host": "h", "path": SNAP}, ok=True, evidence="ok")
        self.assertEqual(cc.missing_verifier_actions(t), [])

    def test_missing_set_key_stable_and_nudge_mentions_before_cleanup(self):
        t = CriteriaTracker.from_spec(_live_spec())
        acts = cc.missing_verifier_actions(t)
        self.assertEqual(cc.missing_set_key(acts), cc.missing_set_key(list(reversed(acts))))
        nudge = cc.closure_nudge_text(acts)
        self.assertIn("ДО cleanup", nudge)
        self.assertIn("ssh_read", nudge)            # explicitly warns read≠content
        self.assertIn("ssh_assert_contains", nudge)


class CleanupBarrierTest(unittest.TestCase):
    def test_delete_while_open_criteria_under_path_is_blocked(self):
        t = CriteriaTracker.from_spec(_live_spec())
        # nothing verified yet; agent tries to delete the whole canary dir
        msg = cc.cleanup_barrier_violation(
            t, "ssh_run_ps", {"script": f'Remove-Item -Recurse -Force "{DIR}"'})
        self.assertIsNotNone(msg)
        self.assertIn("не удаляй", msg.lower())
        self.assertIn("ssh_assert_contains", msg)   # lists what to verify first

    def test_delete_after_everything_confirmed_is_allowed(self):
        t = CriteriaTracker.from_spec(_live_spec())
        t.record(tool_name="ssh_exists", args={"host": "h", "path": DIR}, ok=True, evidence="dir")
        t.record(tool_name="ssh_exists", args={"host": "h", "path": SNAP}, ok=True, evidence="file")
        for pat in ("project=frontend-global-live", "status=ok", "dom=verified"):
            t.record(tool_name="ssh_assert_contains", args={"host": "h", "path": SNAP, "pattern": pat}, ok=True, evidence="x")
        # all non-cleanup criteria confirmed → deleting the dir is fine now
        self.assertIsNone(cc.cleanup_barrier_violation(
            t, "ssh_run_ps", {"script": f'Remove-Item -Recurse -Force "{DIR}"'}))

    def test_ssh_not_exists_is_not_a_deletion(self):
        t = CriteriaTracker.from_spec(_live_spec())
        self.assertIsNone(cc.cleanup_barrier_violation(t, "ssh_not_exists", {"host": "h", "path": DIR}))

    def test_non_delete_command_not_blocked(self):
        t = CriteriaTracker.from_spec(_live_spec())
        self.assertIsNone(cc.cleanup_barrier_violation(t, "ssh_run_ps", {"script": "Get-ChildItem C:\\AgentLabGlobalCanary"}))


class FinalReportTest(unittest.TestCase):
    def test_strips_model_status_sections_keeps_actions(self):
        text = (
            "## Что сделано\n"
            "- создал snapshot.txt\n"
            "- запустил typecheck\n\n"
            "## Completion status\n"
            "confirmed — всё готово\n\n"
            "### Unverified / Failed\n"
            "нет\n\n"
            "## Итог\n"
            "все критерии подтверждены verifier'ом\n"
        )
        out = cc.strip_model_status_sections(text)
        self.assertIn("создал snapshot.txt", out)          # action kept
        self.assertNotIn("Completion status", out)          # model status dropped
        self.assertNotIn("Unverified / Failed", out)
        self.assertNotIn("все критерии подтверждены", out)  # model's итог dropped

    def test_partial_report_lists_unconfirmed_not_all_passed(self):
        t = CriteriaTracker.from_spec(_live_spec())
        t.record(tool_name="ssh_read", args={"host": "h", "path": SNAP}, ok=True, evidence="read")
        rep = cc.runtime_final_report(t)
        self.assertIn("partial", rep)
        self.assertIn("Не подтверждено", rep)
        self.assertIn("project=frontend-global-live", rep)
        self.assertNotIn("Все критерии подтверждены", rep)

    def test_confirmed_report_says_all_confirmed(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[f"файл `{SNAP}` существует"]))
        t.record(tool_name="ssh_exists", args={"host": "h", "path": SNAP}, ok=True, evidence="ok")
        rep = cc.runtime_final_report(t)
        self.assertIn("confirmed", rep)
        self.assertIn("Все критерии подтверждены", rep)


class HappyPathTest(unittest.TestCase):
    def test_full_verify_then_cleanup_reaches_confirmed_no_open_actions(self):
        t = CriteriaTracker.from_spec(_live_spec())
        t.record(tool_name="ssh_exists", args={"host": "h", "path": DIR}, ok=True, evidence="dir")
        t.record(tool_name="ssh_read", args={"host": "h", "path": SNAP}, ok=True, evidence="file")
        for pat in ("project=frontend-global-live", "status=ok", "dom=verified"):
            t.record(tool_name="ssh_assert_contains", args={"host": "h", "path": SNAP, "pattern": pat}, ok=True, evidence="x")
        # cleanup allowed now, then verify absence
        self.assertIsNone(cc.cleanup_barrier_violation(t, "ssh_run_ps", {"script": f'Remove-Item -Recurse "{DIR}"'}))
        t.record(tool_name="ssh_not_exists", args={"host": "h", "path": DIR}, ok=True, evidence="gone")
        self.assertEqual(t.completion_status(), "confirmed")
        self.assertEqual(cc.missing_verifier_actions(t), [])
        self.assertIn("Все критерии подтверждены", cc.runtime_final_report(t))


if __name__ == "__main__":
    unittest.main()
