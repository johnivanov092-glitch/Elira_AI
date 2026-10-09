"""Math contour (owner's decision 2026-10-06): exact, read-only calculation tools."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.skill_modules import load_skill_module

expression = load_skill_module("math", "math_expression.py")
units = load_skill_module("math", "math_units.py")
from app.application.skill_services.tables import tool_csv
from app.application.skill_services.table_query import TableError, aggregate_csv
from app.application.code_agent.tools._dispatch import build_tool_dispatch


def _value(text, **kwargs):
    return expression.calculate(text, **kwargs)["result"]


@pytest.mark.parametrize(("text", "decimal", "approximate"), [
    ("0.1 + 0.2", "0.3", False),
    ("125 000 * 16%", "20000", False),
    ("3500 * 7 / 1000", "24.5", False),
    ("(189900 + 2*45500.5) * 1.1", "308991.1", False),
    ("2^10 + 3²", "1033", False),
    ("1/3", "0.33333333333333333333", True),
])
def test_calc_is_exact(text, decimal, approximate):
    result = _value(text)
    assert result["decimal"] == decimal and result["approximate"] is approximate


def test_calc_rounds_half_up_and_keeps_floor_available():
    assert _value("round(2.675, 2)")["decimal"] == "2.68"
    assert _value("626 + floor(3500*7/1000)")["decimal"] == "650"
    assert _value("sqrt(2)", places=4)["decimal"] == "1.4142"


def test_calc_accepts_handwritten_multiplication():
    assert expression.calculate("2x + y = 10; x - y = 2", "solve")["solutions"][0]["x"]["decimal"] == "4"
    assert _value("3(2+1)")["decimal"] == "9"
    assert _value("log2(8)")["decimal"] == "3" and _value("1e5*2")["decimal"] == "200000"


def test_calc_algebra():
    solved = expression.calculate("2*x + y = 10; x - y = 2", "solve")["solutions"]
    assert solved == [{"x": {"exact": "4", "decimal": "4", "approximate": False},
                       "y": {"exact": "2", "decimal": "2", "approximate": False}}]
    assert expression.calculate("x**3", "diff")["result"] == "3*x**2"
    assert expression.calculate("x**2", "integrate", lower="0", upper="3")["result"]["decimal"] == "9"
    assert expression.calculate("(x+1)**2", "expand")["result"] == "x**2 + 2*x + 1"


@pytest.mark.parametrize("text", [
    '__import__("os").system("calc")', "x.__class__", "().__class__.__bases__", "[1][0]",
    "lambda: 1", 'open("f")', "10**10**10", "factorial(100000)", "exec('1')", "a if b else c",
])
def test_calc_never_executes_code(text):
    with pytest.raises(expression.CalcError):
        expression.calculate(text)


def test_calc_reports_free_variables_instead_of_guessing():
    with pytest.raises(expression.CalcError, match="переменные"):
        expression.calculate("x + 1")


@pytest.mark.parametrize(("value", "source", "target", "result"), [
    (1, "Gbit/s", "MB/s", "125"), (850, "Вт", "кВт", "0.85"), (100, "C", "F", "212"),
    (27, "дюйм", "см", "68.58"), (1, "MW", "kW", "1000"), (1, "mW", "W", "0.001"),
    (8, "b", "B", "1"), (1, "кВт·ч", "МДж", "3.6"), (500, "GB", "ТБ", "0.5"), ("1 024", "MiB", "GiB", "1"),
])
def test_units_are_exact_and_case_sensitive(value, source, target, result):
    assert units.convert(value, source, target)["result"] == result


def test_units_reject_incompatible_dimensions():
    with pytest.raises(units.UnitError, match="несовместимые"):
        units.convert(1, "кг", "м")


def _orders(path: Path, text: str, encoding: str = "utf-8") -> Path:
    target = path / "orders.csv"
    target.write_text(text, encoding=encoding)
    return target


def test_csv_filter_and_sum_are_exact(tmp_path):
    path = _orders(tmp_path, "order_id;customer;status;amount\n1001;Айгерим;paid;15 000,50\n"
                             "1002;Ерлан;pending;8200\n1003;Дина;paid;4300\n", encoding="cp1251")
    result = aggregate_csv(path, filters=[{"column": "status", "op": "==", "value": "paid"}],
                           aggregates=[{"fn": "count"}, {"fn": "sum", "column": "amount"}])
    assert result["rows_matched"] == 2
    assert result["results"] == [{"count": 2, "sum(amount)": "19300.5"}]
    grouped = aggregate_csv(path, group_by=["status"], aggregates=[{"fn": "sum", "column": "amount"}])
    assert {row["status"]: row["sum(amount)"] for row in grouped["results"]} == {"paid": "19300.5", "pending": "8200"}


def test_csv_rejects_unknown_columns(tmp_path):
    path = _orders(tmp_path, "status,amount\npaid,1\n")
    with pytest.raises(TableError, match="нет столбца"):
        aggregate_csv(path, filters=[{"column": "state", "op": "==", "value": "paid"}])


def test_csv_question_is_never_executed_as_code(tmp_path):
    marker = tmp_path / "pwned.txt"
    _orders(tmp_path, "status,amount\npaid,1\n")
    dispatch = build_tool_dispatch(tmp_path)
    query = f"df.pipe(lambda d: open(r'{marker}', 'w').write('x'))"
    result = tool_csv(tmp_path, file_path="orders.csv", query=query)
    assert result["ok"] and not marker.exists()
    assert "filters" in result["text"]


def test_dispatch_and_tools_through_agent_entrypoints(tmp_path):
    _orders(tmp_path, "status,amount\npaid,15000\npaid,4300\npending,8200\n")
    dispatch = build_tool_dispatch(tmp_path)
    assert "unit_convert" not in dispatch
    assert units.convert("2", "TB", "GB")["result"] == "2000"
    queried = tool_csv(tmp_path, file_path="orders.csv", filters=[{"column": "status", "op": "==", "value": "paid"}],
                              aggregate=[{"fn": "sum", "column": "amount"}])
    assert '"sum(amount)": "19300"' in queried["text"]


def test_math_tools_have_one_skill_entrypoint():
    from app.application.code_agent.capabilities import CAPABILITY_GROUPS
    from app.application.code_agent.tool_policy import BASE_TOOLS
    from app.application.tool_registry.builtins import _build_native_code_agent_tools

    specs = {spec["name"] for spec in _build_native_code_agent_tools()}
    assert {"csv", "calc", "unit_convert"}.isdisjoint(specs)
    assert "calc" not in BASE_TOOLS
    assert "math" not in CAPABILITY_GROUPS
