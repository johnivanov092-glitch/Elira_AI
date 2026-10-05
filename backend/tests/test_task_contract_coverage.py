"""Executed content checks must cover the current task, including amendments."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.task_outcomes import TaskOutcome, file_digest, result_verify
from app.application.code_agent.taskspec import derive_task_spec, task_requirements, taskspec_report, task_spec_from_report
from app.application.code_agent.turn_context import TurnContext
from app.application.context.compaction import maybe_compact, TASK_CONTRACT_MARKER_VALUE, TASK_STATE_MARKER_KEY


def run_check(root: Path, outcome: TaskOutcome, checks: list[dict], *, report="report.json", body="") -> dict:
    script = root / "checker.py"
    script.write_text("import json\nfrom pathlib import Path\n" + body + "\n" +
        f"Path({report!r}).write_text(json.dumps({{'checks': {checks!r}}}), encoding='utf-8')\n",
        encoding="utf-8", newline="\n")
    argv = [sys.executable, str(script)]
    before = outcome.verification_context(0)
    result = result_verify(root, {"command": subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv),
        "targets": ["result.json"], "report_path": report})
    wrapped = outcome.bind_verification({"ok": result["ok"], "result": result}, before, 0)
    outcome.observe("runtime_control", {"operation": "result_verify"}, wrapped)
    return wrapped


@pytest.fixture
def outcome(tmp_path):
    (tmp_path / "result.json").write_text('{"total": 9}', encoding="utf-8")
    state = TaskOutcome({"decision": {"disposition": "reuse", "skill_name": "data-check",
        "targets": [str(tmp_path / "result.json")], "inputs": []},
        "loaded": {"data-check": {"sha256": "a" * 64}}})
    state.set_contract("Проверить полный результат", [
        {"id": "language", "text": "Русский язык"}, {"id": "total", "text": "Правильная сумма"}])
    return state


def test_partial_pass_never_accepts_done_and_lists_exact_gap(tmp_path, outcome):
    run_check(tmp_path, outcome, [{"name": "Язык", "requirement_id": "language", "passed": True}])
    assert outcome.missing_requirements(0) == [{"id": "total", "text": "Правильная сумма",
        "mandatory": True, "status": "unconfirmed"}]
    acceptance = AnswerAcceptance()
    kwargs = dict(final_text="Готово, все условия выполнены", raw_user_message="",
        pending_redirected_jobs=[], active_capability_groups=[], task_outcome=outcome,
        run_evidence=RunEvidence(), code_input_epoch=0, quote_word_limit=None, step=2, run_id="coverage")
    first = acceptance.evaluate(**kwargs)
    assert first.action == "retry" and "Правильная сумма" in first.correction
    outcome.correction = first.outcome_correction
    second = acceptance.evaluate(**kwargs)
    assert second.answer_status == "degraded" and "все условия выполнены" not in second.text
    assert "Правильная сумма" in second.text
    assert outcome.learning_evidence(0) is None


def test_independent_requirement_checks_can_cover_same_current_artifact(tmp_path, outcome):
    for identifier in ("language", "total"):
        run_check(tmp_path, outcome, [{"name": identifier, "requirement_id": identifier, "passed": True}],
                  report=identifier + ".json")
    assert outcome.missing_requirements(0) == []
    restored = TaskOutcome(json.loads(json.dumps(outcome.snapshot())))
    assert restored.missing_requirements(0) == []
    (tmp_path / "result.json").write_text('{"total": 10}', encoding="utf-8")
    assert len(restored.missing_requirements(0)) == 2
    (tmp_path / "result.json").write_text('{"total": 9}', encoding="utf-8")
    assert len(restored.missing_requirements(0)) == 2  # observed drift cannot revive proof


def test_pending_contract_declaration_never_accepts_model_completion_after_all_checks(tmp_path, outcome):
    outcome.contract["declaration_pending"] = True
    run_check(tmp_path, outcome, [{"name": identifier, "requirement_id": identifier, "passed": True}
                               for identifier in ("language", "total")])
    assert outcome.missing_requirements(0) == []
    assert outcome.current_verifications(0)[0]["input_version"] == outcome.version(0)
    assert "контракт" in outcome.pending()
    acceptance = AnswerAcceptance()
    kwargs = dict(final_text="Готово, все условия выполнены", raw_user_message="",
        pending_redirected_jobs=[], active_capability_groups=[], task_outcome=outcome,
        run_evidence=RunEvidence(), code_input_epoch=0, quote_word_limit=None, step=2, run_id="declaration")
    first = acceptance.evaluate(**kwargs)
    assert first.action == "retry" and first.reason == "outcome"
    outcome.correction = first.outcome_correction
    second = acceptance.evaluate(**kwargs)
    assert second.action == "accept" and second.answer_status == "degraded"
    assert "все условия выполнены" not in second.text
    assert "контракт" in second.text
    assert outcome.learning_evidence(0) is None


def test_failure_repair_is_bound_to_selected_skill_and_one_case(tmp_path, outcome):
    run_check(tmp_path, outcome, [{"name": "Сумма", "requirement_id": "total", "passed": False}])
    failure = outcome.learning_observations(0)[0]
    assert failure["outcome"] == "requirement_failure" and failure["requirement_ids"] == ["total"]
    assert failure["execution_ok"] is True
    run_check(tmp_path, outcome, [{"name": identifier, "requirement_id": identifier, "passed": True}
                               for identifier in ("language", "total")])
    observations = TaskOutcome(outcome.snapshot()).learning_observations(0)
    assert [row["outcome"] for row in observations] == ["requirement_failure", "verified_success"]
    assert observations[-1]["case_id"] == failure["case_id"]
    assert observations[-1]["recovery_of"] == [failure["case_id"]]


@pytest.mark.parametrize("body", ["raise SystemExit(7)", "Path('result.json').write_text('changed')"])
def test_execution_error_or_mutation_never_penalizes_skill(tmp_path, outcome, body):
    run_check(tmp_path, outcome, [{"name": "Сумма", "requirement_id": "total", "passed": False}], body=body)
    assert outcome.learning_observations(0) == []


def test_live_clarification_preserves_original_contract_and_revokes_prior_pass(tmp_path):
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    spec = derive_task_spec("Цель: Проверить документ\nКритерии готовности:\n- Русский язык\n- Правильная сумма", tmp_path)
    observed = RunObservations(task_spec=spec, durable_state={}, resume=False)
    observed.outcome.decision = {"targets": [str(tmp_path / "result.json")]}
    run_check(tmp_path, observed.outcome, [{"name": item["text"], "requirement_id": item["id"], "passed": True}
                                         for item in task_requirements(spec)])
    assert not observed.outcome.missing_requirements(0)
    observed.apply_user_clarification("Критерии готовности:\n- Сортировка по убыванию", root=tmp_path)
    assert observed.task_spec.goal == spec.goal
    assert len(observed.outcome.missing_requirements(0)) == 3
    restored_spec = task_spec_from_report(taskspec_report(observed.task_spec))
    restored = RunObservations(task_spec=restored_spec, durable_state={"task_outcome": observed.outcome.snapshot()}, resume=True)
    assert len(restored.outcome.missing_requirements(0)) == 3


def test_compaction_keeps_full_goal_and_all_requirements_out_of_lossy_summary(tmp_path):
    context = TurnContext(messages=[{"role": "system", "content": "System"}],
        raw_user_message="Исходная цель с неизменным CSV", root=tmp_path, working_dir=tmp_path, run_id="compact")
    rows = [{"requirement_id": str(i), "text": "Требование " + str(i), "status": "unconfirmed"} for i in range(14)]
    context.refresh_task_state = True
    context.update_task_state(task_spec=None, criteria_rows=rows, checklist_items=[], mutated_files=[],
                              verifications=[], failed_attempts=[])
    original = next(row for row in context.messages if row.get(TASK_STATE_MARKER_KEY) == TASK_CONTRACT_MARKER_VALUE)
    context.messages.extend({"role": "user", "content": "history " + str(i)} for i in range(12))
    summarized = []
    def summary(**kwargs):
        summarized.extend(kwargs["messages"])
        return {"ok": True, "summary": "lossy"}
    packed, compacted = maybe_compact(context.messages, num_ctx=100, model="fixture", chat_fn=None,
                                      summarize_fn=summary, threshold=0, keep_pairs=1)
    assert compacted and original in packed and original not in summarized
    assert "Требование 13" in original["content"] and "Исходная цель" in original["content"]


def test_model_cannot_replace_original_requirement_or_use_unrelated_clarification(outcome):
    def decision(text, **extra):
        return {"ok": True, "operation": "task_decide", "result": {"task_decision": {
            "requirements": [{"id": "language", "text": text, **extra}]}}}
    assert outcome.bind_decision(decision("Файл существует"))["ok"] is False
    outcome.apply_user_clarification("Сортируй cities по revenue вместо имени")
    assert outcome.bind_decision(decision("Сортируй cities по revenue вместо имени", source_clarification=1))["ok"] is False
    assert outcome.contract["requirements"][0]["text"] == "Русский язык"


def test_direct_replacement_clause_amends_one_condition_and_revokes_its_proof(outcome):
    outcome.set_contract("Отчёт", [{"id": "sort", "text": "Сортируй cities по имени"},
                                   {"id": "language", "text": "Русский язык"}])
    clarification = "Сортируй cities по revenue вместо имени"
    outcome.apply_user_clarification(clarification)
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {
        "disposition": "one_off", "targets": [], "requirements": [
            {"id": "sort", "text": clarification, "source_clarification": 1}]}}}
    bound = outcome.bind_decision(output)
    amended = "Сортируй cities по имени\n\nУточнение пользователя 1: " + clarification
    assert bound["ok"] is True
    assert bound["result"]["task_decision"]["requirements"][0]["text"] == amended
    assert output["result"]["task_decision"]["requirements"][0]["text"] == clarification
    assert outcome.bind_decision(bound) == bound
    outcome.observe("runtime_control", {"operation": "task_decide"}, output)
    assert [item["text"] for item in outcome.contract["requirements"]] == [amended, "Русский язык"]
    outcome.observe("runtime_control", {"operation": "task_decide"}, bound)
    outcome.observe("runtime_control", {"operation": "task_decide"}, output)
    assert outcome.contract["requirements"][0]["text"] == amended
    assert not outcome.contract["declaration_pending"]


@pytest.mark.parametrize(("clarification", "incoming"), [
    ("Не заменяй сортировку по сумме.", "Не заменяй сортировку по сумме."),
    ("Не заменяй сортировку по сумме.", "заменяй сортировку по сумме."),
    ("Не нужно изменять сортировку по сумме.", "Не нужно изменять сортировку по сумме."),
    ("Do not replace revenue sorting.", "Do not replace revenue sorting."),
])
def test_negated_direct_clarification_does_not_authorize_replacement(outcome, clarification, incoming):
    old = "Сортировка по сумме revenue sorting"
    outcome.set_contract("Отчёт", [{"id": "sort", "text": old}])
    outcome.apply_user_clarification(clarification)
    requirement = {"id": "sort", "text": incoming, "source_clarification": 1}
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {
        "disposition": "one_off", "requirements": [requirement]}}}
    assert not outcome._authorized_amendment(outcome.contract["requirements"][0], requirement)
    bound = outcome.bind_decision(output)
    assert bound["ok"] is False
    assert "source_clarification=<1-based accepted input>" in bound["text"]
    assert clarification in bound["text"]
    outcome.observe("runtime_control", {"operation": "task_decide"}, output)
    assert outcome.contract["requirements"][0]["text"] == old
    assert outcome.contract["declaration_pending"] is True


def test_sort_amendment_keeps_every_constraint_in_composite_json_requirement(outcome):
    original = ("sales.json: audit_code=BASE_A_47, currency=KZT, paid_orders, total_quantity, "
                "total_revenue и cities; cities сортируй по имени")
    clause = "cities сортируй по revenue по убыванию вместо имени."
    outcome.set_contract("Сверенный отчёт", [{"id": "json-contract", "text": original}])
    outcome.apply_user_clarification(clause + " Остальные требования сохраняются.")
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {
        "disposition": "one_off", "requirements": [
            {"id": "json-contract", "text": clause, "source_clarification": 1}]}}}
    bound = outcome.bind_decision(output)
    expected = original + "\n\nУточнение пользователя 1: " + clause
    assert bound["ok"] is True
    assert bound["result"]["task_decision"]["requirements"][0]["text"] == expected
    outcome.observe("runtime_control", {"operation": "task_decide"}, output)
    assert outcome.contract["requirements"][0]["text"] == expected
    assert outcome.contract["goal"] == "Сверенный отчёт"
    assert outcome.missing_requirements(0, [{"requirement_id": "json-contract", "text": original,
                                            "status": "confirmed"}])[0]["text"] == expected
    assert outcome.bind_decision(bound) == bound
    assert outcome.bind_decision(output)["result"]["task_decision"]["requirements"][0]["text"] == expected


@pytest.mark.parametrize("preamble", ["Ещё поправка:", "Уточнение к текущей задаче:"])
def test_neutral_preamble_can_be_omitted_from_exact_sort_clause(outcome, preamble):
    original = "sales.json: audit_code=BASE_A_47, currency=KZT; cities сортируй по имени"
    clause = "cities отсортируй по revenue по убыванию вместо имени."
    outcome.set_contract("Отчёт", [{"id": "json-contract", "text": original}])
    outcome.apply_user_clarification(preamble + " " + clause + " Остальные требования сохраняются.")
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {
        "requirements": [{"id": "json-contract", "text": clause, "source_clarification": 1}]}}}
    bound = outcome.bind_decision(output)
    assert bound["ok"] is True
    assert bound["result"]["task_decision"]["requirements"][0]["text"] == (
        original + "\n\nУточнение пользователя 1: " + clause)
    assert outcome.bind_decision(bound) == bound


def test_addition_to_exact_filename_preserves_summary_requirement_and_is_idempotent(outcome):
    original = "summary.md: короткий русский отчёт с итогами и списком исключённых id"
    clause = "В самый конец summary.md добавь точную строку CHECK_B_82."
    outcome.set_contract("Отчёт", [{"id": "summary-contract", "text": original}])
    outcome.apply_user_clarification("Ещё поправка: cities отсортируй по revenue вместо имени. " + clause)
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {
        "requirements": [{"id": "summary-contract", "text": clause, "source_clarification": 1}]}}}
    bound = outcome.bind_decision(output)
    expected = original + "\n\nУточнение пользователя 1: " + clause
    assert bound["ok"] is True
    assert bound["result"]["task_decision"]["requirements"][0]["text"] == expected
    assert outcome.bind_decision(bound) == bound
    outcome.observe("runtime_control", {"operation": "task_decide"}, output)
    assert outcome.contract["requirements"][0]["text"] == expected
    assert outcome.bind_decision(output)["result"]["task_decision"]["requirements"][0]["text"] == expected


@pytest.mark.parametrize(("clarification", "incoming"), [
    ("Ещё поправка: Не добавляй точную строку CHECK_B_82 в summary.md.",
     "Не добавляй точную строку CHECK_B_82 в summary.md."),
    ("Ещё поправка: Не добавляй точную строку CHECK_B_82 в summary.md.",
     "добавляй точную строку CHECK_B_82 в summary.md."),
    ("Не делай поправку: добавь CHECK_B_82 в summary.md.", "добавь CHECK_B_82 в summary.md."),
    ("В самый конец summary2.md добавь точную строку CHECK_B_82.",
     "В самый конец summary2.md добавь точную строку CHECK_B_82."),
    ("В самый конец summary.unknown добавь точную строку CHECK_B_82.",
     "В самый конец summary.unknown добавь точную строку CHECK_B_82."),
])
def test_addition_cannot_drop_negative_prefix_or_bind_to_another_filename(outcome, clarification, incoming):
    original = "summary.md: короткий русский отчёт с итогами"
    outcome.set_contract("Отчёт", [{"id": "summary-contract", "text": original}])
    outcome.apply_user_clarification(clarification)
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {
        "requirements": [{"id": "summary-contract", "text": incoming, "source_clarification": 1}]}}}
    assert outcome.bind_decision(output)["ok"] is False
    outcome.observe("runtime_control", {"operation": "task_decide"}, output)
    assert outcome.contract["requirements"][0]["text"] == original
    assert outcome.contract["declaration_pending"] is True
