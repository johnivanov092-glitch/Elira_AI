"""Validation and exact result formatting shared by the MATH scenarios."""
from __future__ import annotations

from typing import Any

import sympy as sp

from math_expression import describe_number, parse


def number(value: Any, name: str = "value", *, minimum=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name}: требуется конечное действительное число")
    result = parse(str(value))
    if result.free_symbols or result.is_real is not True or result.is_finite is not True:
        raise ValueError(f"{name}: требуется конечное действительное число")
    if positive and result <= 0 or minimum is not None and result < minimum:
        raise ValueError(f"{name}: значение вне допустимого диапазона")
    return result


def integer(value: Any, name: str, low: int = 0, high: int = 10000) -> int:
    result = number(value, name)
    if result.is_integer is not True or not low <= result <= high:
        raise ValueError(f"{name}: требуется целое число от {low} до {high}")
    return int(result)


def numbers(value: Any, name: str = "values", *, maximum: int = 10000):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name}: нужен непустой список, не более {maximum} чисел")
    return [number(item, f"{name}[{index}]") for index, item in enumerate(value)]


def fields(params: dict, allowed: str, required: str = "") -> None:
    extra = set(params) - set(allowed.split())
    missing = set(required.split()) - set(params)
    if extra or missing:
        raise ValueError(f"параметры: лишние={sorted(extra)}, отсутствуют={sorted(missing)}")


def encode(value: Any, places: int | None = None):
    if isinstance(value, dict):
        return {str(k): encode(v, places) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v, places) for v in value]
    if isinstance(value, sp.Expr) and value.is_number:
        return describe_number(value, places)
    if isinstance(value, sp.Basic):
        return str(value)
    return value


def outcome(values: Any, formula: str, params: dict, *, units=None, places=None, approximate=False):
    return {"values": encode(values, places), "formula": formula, "substitution": params,
            "units": units or {}, "precision": {"places": places, "rounding": "ROUND_HALF_UP",
                                                  "method": "numerical" if approximate else "exact/symbolic"}}
