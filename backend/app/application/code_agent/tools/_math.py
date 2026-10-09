"""Read-only arithmetic and unit conversion; financial formulas live in skills."""
from __future__ import annotations

import json
from decimal import InvalidOperation
from typing import Any

from app.application.calculation import expression as calc_expression
from app.application.calculation import units
from app.application.calculation.calendar_ops import date_info


def _ok(label: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "text": f"{label}:\n{json.dumps(result, ensure_ascii=False, indent=2)}", "result": result}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "text": f"ERROR: {message}"}


def tool_calc(*, expression: str = "", operation: str = "evaluate", variable: str = "",
              lower: str = "", upper: str = "", order: int = 1, places: int | None = None) -> dict[str, Any]:
    if not str(expression or "").strip():
        return _error("missing_expression", "нужно выражение (expression)")
    try:
        if operation == "date_info":
            return _ok("Календарь", date_info(str(expression)))
        result = calc_expression.calculate(
            str(expression), operation, variable=str(variable or ""), lower=str(lower or ""),
            upper=str(upper or ""), order=int(order or 1),
            places=None if places in (None, "") else int(places),
        )
    except (calc_expression.CalcError, ValueError, TypeError) as exc:
        return _error("calc_error", str(exc))
    return _ok("Расчёт", result)


def tool_unit_convert(*, value: Any, from_unit: str, to_unit: str) -> dict[str, Any]:
    try:
        result = units.convert(value, from_unit, to_unit)
    except units.UnitError as exc:
        return _error("unit_error", str(exc))
    except (InvalidOperation, ValueError):
        return _error("invalid_value", f"value: не число ({value!r})")
    return _ok("Перевод единиц", result)
