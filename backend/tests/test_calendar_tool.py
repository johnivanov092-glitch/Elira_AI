"""Calendar boundaries shared by MATH and the runtime date context."""
from datetime import datetime, timedelta, timezone

import pytest

from app.application.calculation.calendar_ops import date_info, runtime_date_context
from app.core.skill_modules import load_skill_module

math_skill = load_skill_module("math", "calculate.py")


@pytest.mark.parametrize('value,weekday,name', [
    ('2026-10-09', 5, 'пятница'), ('2026-10-12', 1, 'понедельник'),
    ('2024-02-29', 4, 'четверг'), ('2000-02-29', 2, 'вторник'),
    ('2026-12-31', 4, 'четверг'), ('2027-01-01', 5, 'пятница'),
])
def test_exact_calendar_facts(value, weekday, name):
    result = math_skill.run_request({'expression': value, 'operation': 'date_info'})
    assert result['ok']
    assert result['result']['dates'] == [{'date': value, 'weekday_iso': weekday, 'weekday_ru': name}]


@pytest.mark.parametrize('value', [
    '2026-02-29', '1900-02-29', '2026-13-01', '0000-01-01', '20261009',
    '09.10.2026', '2026-W41-5', '2026-10-09T00:00:00', '2026-10-09,',
    '__import__("os")', ','.join(['2026-10-09'] * 32),
])
def test_invalid_input_returns_tool_error(value):
    result = math_skill.run_request({'expression': value, 'operation': 'date_info'})
    assert result['ok'] is False
    assert result['error'] == 'finance_error'


def test_batch_keeps_order_and_calculates_each_date():
    result = date_info('2026-10-15, 2026-10-09,2026-10-10')
    assert [row['weekday_iso'] for row in result['dates']] == [4, 5, 6]


def test_current_context_uses_same_local_day_and_offset():
    local = datetime(2026, 10, 9, 0, 5, tzinfo=timezone(timedelta(hours=5)))
    assert runtime_date_context(local) == '2026-10-09 (пятница) UTC+0500'
    assert runtime_date_context(local.astimezone(timezone.utc)) == '2026-10-08 (четверг) UTC+0000'


def test_context_rejects_ambiguous_naive_time():
    with pytest.raises(ValueError, match='timezone-aware'):
        runtime_date_context(datetime(2026, 10, 9))
