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
        self.assertIn("Все обязательные критерии подтверждены", rep)


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
        self.assertIn("Все обязательные критерии подтверждены", cc.runtime_final_report(t))


class ReportCountsTest(unittest.TestCase):
    def test_counts_come_from_report_and_sum_to_total(self):
        t = CriteriaTracker.from_spec(_live_spec())  # 6 criteria
        t.record(tool_name="ssh_exists", args={"host": "h", "path": DIR}, ok=True, evidence="dir")
        c = cc.report_counts(t.report())
        self.assertEqual(c["total"], 6)
        self.assertEqual(c["confirmed"], 1)
        self.assertEqual(c["confirmed"] + c["failed"] + c["unconfirmed"], c["total"])


class ScrubManualCountsTest(unittest.TestCase):
    def test_drops_verifier_criteria_count_keeps_noun_and_claim(self):
        out = cc.scrub_manual_criteria_counts("Прогнал 17 verifier criteria checks — all passed.")
        self.assertNotIn("17", out)
        self.assertIn("verifier criteria", out.lower())
        self.assertIn("all passed", out.lower())

    def test_drops_slashed_before_and_after_forms(self):
        self.assertNotIn("17", cc.scrub_manual_criteria_counts("17/17 criteria met"))
        self.assertNotIn("17", cc.scrub_manual_criteria_counts("Verifier checks: 17/17 passed"))

    def test_drops_russian_of_form(self):
        out = cc.scrub_manual_criteria_counts("проверено 17 из 19 критериев")
        self.assertNotIn("17", out)
        self.assertNotIn("19", out)
        self.assertIn("критери", out)

    def test_leaves_non_criteria_numbers_alone(self):
        self.assertIn("17", cc.scrub_manual_criteria_counts("изменил 17 файлов"))
        self.assertIn("17", cc.scrub_manual_criteria_counts("17 tests passed"))


class FinalAssemblyCountTest(unittest.TestCase):
    """Live bug: model prints 17 while runtime has 19 confirmed → final must say 19/19."""

    def _confirmed_tracker(self, n):
        spec = TaskSpec(success_criteria=[f"файл `C:\\X\\f{i}.txt` существует" for i in range(n)])
        t = CriteriaTracker.from_spec(spec)
        for it in t.items:
            it["status"] = "confirmed"
        return t

    def test_model_17_becomes_runtime_19_of_19(self):
        t = self._confirmed_tracker(19)
        self.assertEqual(t.completion_status(), "confirmed")
        # model prose (in a non-status section, so it survives stripping) claims 17
        model = "## Что сделано\n- собрал фронт\nПрогнал 17 verifier criteria checks — all passed.\n"
        # mirror the agent_loop finalization order: strip → scrub → append runtime block
        final = cc.strip_model_status_sections(model)
        final = cc.scrub_manual_criteria_counts(final)
        rep = cc.runtime_final_report(t)
        if rep:
            final = final.rstrip() + "\n\n" + rep
        self.assertIn("подтверждено 19/19", final)
        self.assertNotIn("17", final)


class MinimalPlanTest(unittest.TestCase):
    """Tool-economy: a browser render proves page_open too, so the missing-verifier
    plan must not also demand http_api for the same page."""

    def test_browser_dom_subsumes_page_open_no_http_api(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "первая страница открывается без ошибок",
            "на первом экране явно видно название `VaultDesk`",
        ]))
        acts = cc.missing_verifier_actions(t)
        tools = [a["tool"] for a in acts]
        self.assertIn("browser", tools)
        self.assertNotIn("http_api", tools)   # page_open subsumed by the browser render
        self.assertEqual(len(acts), 1)         # one call closes both criteria

    def test_page_open_alone_still_offers_http_api(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["первая страница открывается без ошибок"]))
        acts = cc.missing_verifier_actions(t)
        self.assertEqual(len(acts), 1)
        self.assertIn("http_api", acts[0]["call"])   # unchanged when there is no DOM criterion


class InteractionGroupingTest(unittest.TestCase):
    def _three(self):
        return CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.88.0`",
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Mask: 255.255.255.0`",
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Hosts: 254`",
        ]))

    def test_three_interactions_group_into_one_concrete_browser_call(self):
        acts = cc.missing_verifier_actions(self._three(), url="http://localhost:5173")
        self.assertEqual(len(acts), 1)                       # ONE grouped call, not three
        call = acts[0]["call"].lower()
        self.assertIn("192.168.88.0/24", call)               # exact fill value
        self.assertIn("calculate", call)                     # exact click target
        for tok in ("network: 192.168.88.0", "mask: 255.255.255.0", "hosts: 254"):
            self.assertIn(tok, call)                          # all expected result tokens
        self.assertIn("actions", call)
        self.assertIn("не перезапускай сервер", call)

    def test_redirect_points_to_browser_not_run_server(self):
        msg = cc.browser_interaction_redirect(self._three(), "http://localhost:5173")
        self.assertIsNotNone(msg)
        self.assertIn("НЕ перезапускай", msg)
        self.assertIn("192.168.88.0/24", msg)
        self.assertIn("Calculate", msg)

    def test_redirect_none_when_no_open_interactions(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["`npm run build` проходит без ошибок"]))
        self.assertIsNone(cc.browser_interaction_redirect(t, "http://localhost:5173"))

    def test_confirmed_interaction_not_re_listed(self):
        t = self._three()
        dom = "Network\n192.168.88.0\nMask\n255.255.255.0\nHosts\n254"
        t.record(tool_name="browser", args={"url": "http://localhost:5173"}, ok=True, evidence=dom, meta={"interacted": True})
        self.assertEqual(cc.missing_verifier_actions(t, url="http://localhost:5173"), [])


class SubnetOpenStateClosureTest(unittest.TestCase):
    def test_missing_actions_cover_local_typecheck_and_interaction(self):
        # The exact 6-open Subnet state: closure must name path_exists (local dir),
        # run_bash (conditional typecheck), and browser-with-actions (interaction).
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "создана новая папка `subnet-helper`",
            "если в проекте есть `npm run typecheck`, он проходит без ошибок",
            "browser interaction: после ввода `192.168.88.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.88.0`",
        ]))
        acts = cc.missing_verifier_actions(t, url="http://localhost:5173")
        tools = {a["tool"] for a in acts}
        self.assertIn("path_exists", tools)
        self.assertIn("run_bash", tools)      # conditional typecheck still offered (run it if present)
        self.assertIn("browser", tools)
        browser_act = next(a for a in acts if a["tool"] == "browser")
        self.assertIn("actions", browser_act["call"])


class LocalAndInteractionActionTest(unittest.TestCase):
    def test_local_file_criterion_suggests_path_exists_not_ssh(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["создана новая папка `subnet-helper`"]))
        acts = cc.missing_verifier_actions(t)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["tool"], "path_exists")
        self.assertIn("subnet-helper", acts[0]["call"])

    def test_remote_file_criterion_still_suggests_ssh_exists(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=["файл `C:\\AgentLab\\snapshot.txt` существует"]))
        acts = cc.missing_verifier_actions(t, host="home-srv01")
        self.assertEqual(acts[0]["tool"], "ssh_exists")

    def test_plan_never_asks_to_re_search_a_tool(self):
        # tool-economy: the planned closure actions name the verifier tool directly
        # (browser / path_exists) — they never tell the model to tool_search again.
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "rendered DOM содержит текст `Subnet Helper`",
            "первая страница открывается без ошибок",
            "создана новая папка `subnet-helper`",
        ]))
        acts = cc.missing_verifier_actions(t, url="http://localhost:5173")
        calls = " ".join(a["call"] for a in acts)
        self.assertNotIn("tool_search", calls)
        tools = [a["tool"] for a in acts]
        self.assertNotIn("http_api", tools)     # page_open subsumed by the browser render
        self.assertIn("path_exists", tools)     # local folder via local verifier

    def test_interaction_dom_action_asks_for_browser_actions(self):
        t = CriteriaTracker.from_spec(TaskSpec(success_criteria=[
            "browser interaction: после ввода `192.168.1.0/24` и нажатия `Calculate` rendered DOM содержит `Network: 192.168.1.0`",
        ]))
        acts = cc.missing_verifier_actions(t, url="http://localhost:5173")
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["tool"], "browser")
        self.assertIn("actions", acts[0]["call"])          # steers to fill/click, not a static render
        self.assertIn("network: 192.168.1.0", acts[0]["call"].lower())


if __name__ == "__main__":
    unittest.main()
