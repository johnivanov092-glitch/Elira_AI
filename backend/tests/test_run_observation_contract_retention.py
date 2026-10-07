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


