"""Prose steering preserves the original user contract and model declarations."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.taskspec import TaskSpec, derive_task_spec, taskspec_report, task_spec_from_report


# Exact user text from .scratch/live-steering-retention-20260930. Keep fixtures
# local to the test so the ignored live evidence directory is not a CI input.
ORIGINAL_PROMPT = (
    "Проверка продолжения задачи QA-STEER-RETENTION-930. Работай только в "
    "D:/AIWork/Elira_AI/.scratch/live-steering-retention-20260930. Прочитай orders.csv "
    "и подготовь сверенный отчёт продаж. Учитывай только строки status=paid; cancelled "
    "и refunded полностью исключи. Выручка строки = quantity * unit_price. Сгруппируй "
    "по городу. Создай sales.json с полями audit_code=\"BASE_A_47\", currency=\"KZT\", "
    "paid_orders, total_quantity, total_revenue и cities (для каждого города city, "
    "paid_orders, quantity, revenue). Изначально города сортируй по имени. Также создай "
    "summary.md: короткий русский отчёт с итогами и списком исключённых id. Обязательно "
    "перечитай и проверь оба файла, исходный CSV не изменяй. В финале перечисли два "
    "созданных файла и итоговую выручку. Не изменяй код приложения, настройки или другие "
    "каталоги; не сохраняй этот тест в долговременную память."
)
CLARIFICATIONS = [
    "Уточнение к текущей задаче: добавь скидку 10% после расчёта выручки. Исходные суммы "
    "оставь, а рядом добавь discount_percent=10 и net_revenue для каждого города, а на "
    "верхнем уровне discount_percent=10 и total_net_revenue. В summary.md тоже укажи "
    "сумму после скидки. Продолжай тот же отчёт.",
    "Ещё поправка: cities отсортируй по revenue по убыванию вместо имени. В самый конец "
    "summary.md добавь точную строку CHECK_B_82. Остальные требования первого сообщения "
    "и предыдущего уточнения сохраняются.",
]


def test_original_live_prose_goal_and_declared_requirements_survive_both_clarifications():
    assert derive_task_spec(ORIGINAL_PROMPT) is None
    observations = RunObservations(task_spec=None, durable_state={}, resume=False)
    requirements = [{"id": "language", "text": "Русский отчёт", "mandatory": True},
                    {"id": "csv", "text": "Исходный CSV не изменён", "mandatory": True}]
    observations.outcome.set_contract(ORIGINAL_PROMPT, requirements)
    version = observations.outcome.verification_context(0)
    for clarification in CLARIFICATIONS:
        observations.apply_user_clarification(clarification)
        assert observations.task_spec is None
        assert observations.outcome.contract["goal"] == ORIGINAL_PROMPT
        assert observations.outcome.contract["requirements"] == requirements
        assert observations.outcome.contract["declaration_pending"] is True
    assert observations.outcome.contract["clarifications"] == CLARIFICATIONS
    assert observations.outcome.verification_context(0) != version


def test_structured_clarification_retains_extra_model_requirements_and_original_prose_goal():
    observations = RunObservations(task_spec=None, durable_state={}, resume=False)
    observations.outcome.set_contract(ORIGINAL_PROMPT, [
        {"id": "csv", "text": "Исходный CSV не изменён", "mandatory": True}])
    observations.apply_user_clarification("Критерии готовности:\n- Скидка вложена в каждый город")
    assert observations.outcome.contract["goal"] == ORIGINAL_PROMPT
    assert [row["text"] for row in observations.outcome.contract["requirements"]] == [
        "Исходный CSV не изменён", "Скидка вложена в каждый город"]


def test_explicit_user_requirement_replacement_retains_unmentioned_model_requirement():
    old = "Города сортируются по имени"
    observations = RunObservations(task_spec=TaskSpec(goal="Проверить отчёт", success_criteria=[old],
        criterion_contracts={old: {"requirement_id": "city-sort"}}), durable_state={}, resume=False)
    observations.outcome.set_contract("Проверить отчёт", [
        {"id": "city-sort", "text": old, "mandatory": True},
        {"id": "csv", "text": "Исходный CSV не изменён", "mandatory": True}])
    observations.apply_user_clarification("замени критерий [city-sort]: Города сортируются по revenue по убыванию")
    assert observations.outcome.contract["requirements"] == [
        {"id": "city-sort", "text": "Города сортируются по revenue по убыванию", "mandatory": True},
        {"id": "csv", "text": "Исходный CSV не изменён", "mandatory": True}]


def test_task_decision_is_bound_even_without_a_result_verification_context(monkeypatch):
    observations = RunObservations(task_spec=None, durable_state={}, resume=False)
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {}}}
    bound = {"ok": False, "operation": "task_decide", "error": "missing_requirements"}
    calls = []
    def bind_decision(value):
        calls.append(value)
        return bound
    monkeypatch.setattr(observations.outcome, "bind_decision", bind_decision, raising=False)
    assert observations.bind_verification(output, None) is bound
    assert calls == [output]


def test_resume_retains_accepted_amendment_and_only_seeds_missing_task_spec_requirements():
    old = "Города cities сортируй по имени"
    spec = TaskSpec(goal="Исходный отчёт", success_criteria=[old, "Русский язык"],
        criterion_contracts={old: {"requirement_id": "city-sort"},
                             "Русский язык": {"requirement_id": "language"}})
    observations = RunObservations(task_spec=spec, durable_state={}, resume=False)
    # The durable contract can also contain model-declared conditions absent
    # from the structured TaskSpec, which remains the original saved version.
    observations.outcome.set_contract("Исходный отчёт", [
        {"id": "city-sort", "text": old}, {"id": "csv", "text": "Исходный CSV не изменён"}])
    clause = "Города cities сортируй по revenue по убыванию вместо имени"
    observations.apply_user_clarification(clause)
    output = {"ok": True, "operation": "task_decide", "result": {"task_decision": {
        "disposition": "one_off", "requirements": [
            {"id": "city-sort", "text": clause, "source_clarification": 1}]}}}
    observations.outcome.observe("runtime_control", {"operation": "task_decide"}, output)
    amended = observations.outcome.contract["requirements"][0]["text"]
    assert amended == old + "\n\nУточнение пользователя 1: " + clause
    assert observations.task_spec.success_criteria == [old, "Русский язык"]
    restored_spec = task_spec_from_report(taskspec_report(observations.task_spec))
    # Test seeding a missing ID as well as retaining IDs from both sources.
    saved = observations.outcome.snapshot()
    saved["contract"]["requirements"] = [row for row in saved["contract"]["requirements"]
                                           if row["id"] != "language"]
    restored = RunObservations(task_spec=restored_spec, durable_state={"task_outcome": saved}, resume=True)
    by_id = {row["id"]: row for row in restored.outcome.contract["requirements"]}
    assert by_id["city-sort"]["text"] == amended
    assert by_id["csv"]["text"] == "Исходный CSV не изменён"
    assert by_id["language"]["text"] == "Русский язык"
    assert restored.outcome.contract["goal"] == "Исходный отчёт"
    assert restored.outcome.contract["clarifications"] == [clause]
    assert restored.outcome.contract["declaration_pending"] is False
    restored.apply_user_clarification("В summary.md добавь CHECK_B_82")
    by_id = {row["id"]: row for row in restored.outcome.contract["requirements"]}
    assert by_id["city-sort"]["text"] == amended
    assert by_id["csv"]["text"] == "Исходный CSV не изменён"
    restored.apply_user_clarification(
        "замени критерий [city-sort]: Города сортируются по revenue по возрастанию")
    by_id = {row["id"]: row for row in restored.outcome.contract["requirements"]}
    assert by_id["city-sort"]["text"] == "Города сортируются по revenue по возрастанию"
    assert by_id["csv"]["text"] == "Исходный CSV не изменён"
    assert restored.outcome.contract["goal"] == "Исходный отчёт"
