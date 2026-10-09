"""Exact filter / group / aggregate over a CSV file (no expression evaluation).

Values are compared as decimals when both sides parse as numbers, otherwise
as case-insensitive text. Sums use Decimal, so money columns stay exact.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from app.application.calculation.numbers import parse_decimal, plain

OPS = ("==", "!=", ">", ">=", "<", "<=", "contains", "not_contains", "in", "empty", "not_empty")
FUNCTIONS = ("count", "sum", "avg", "min", "max", "count_distinct")
_MAX_BYTES = 50 * 1024 * 1024
_MAX_GROUPS = 100


class TableError(ValueError):
    """Invalid filter/aggregate request or unreadable file."""


def _read(path: Path) -> tuple[list[str], Iterable[dict[str, str]]]:
    if path.stat().st_size > _MAX_BYTES:
        raise TableError("файл больше 50 МБ")
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise TableError("не удалось определить кодировку (ожидается UTF-8 или Windows-1251)")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = [str(name or "").strip() for name in (reader.fieldnames or [])]
    if not columns:
        raise TableError("в файле нет строки заголовков")
    reader.fieldnames = columns
    return columns, reader


def _number(value: Any) -> Decimal | None:
    try:
        return parse_decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _matches(row: dict[str, str], condition: dict[str, Any]) -> bool:
    cell = str(row.get(condition["column"]) or "").strip()
    op, expected = condition["op"], condition.get("value")
    if op == "empty":
        return cell == ""
    if op == "not_empty":
        return cell != ""
    if op == "in":
        options = expected if isinstance(expected, list) else str(expected or "").split(",")
        return any(_equal(cell, option) for option in options)
    if op in ("contains", "not_contains"):
        found = str(expected or "").casefold() in cell.casefold()
        return found if op == "contains" else not found
    if op in ("==", "!="):
        equal = _equal(cell, expected)
        return equal if op == "==" else not equal
    left, right = _number(cell), _number(expected)
    if left is None or right is None:
        left_text, right_text = cell.casefold(), str(expected or "").strip().casefold()
        return {">": left_text > right_text, ">=": left_text >= right_text,
                "<": left_text < right_text, "<=": left_text <= right_text}[op]
    return {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right}[op]


def _equal(cell: str, expected: Any) -> bool:
    left, right = _number(cell), _number(expected)
    if left is not None and right is not None:
        return left == right
    return cell.strip().casefold() == str(expected if expected is not None else "").strip().casefold()


def _validate(columns: list[str], filters: list[Any], group_by: list[Any], aggregates: list[Any]) -> None:
    known = set(columns)
    for condition in filters:
        if not isinstance(condition, dict) or condition.get("op") not in OPS:
            raise TableError(f"фильтр: нужен {{column, op, value}}, op из {', '.join(OPS)}")
        if condition.get("column") not in known:
            raise TableError(f"нет столбца {condition.get('column')!r}; столбцы: {', '.join(columns)}")
    for column in group_by:
        if column not in known:
            raise TableError(f"group_by: нет столбца {column!r}; столбцы: {', '.join(columns)}")
    for aggregate in aggregates:
        if not isinstance(aggregate, dict) or aggregate.get("fn") not in FUNCTIONS:
            raise TableError(f"aggregate: нужен {{fn, column}}, fn из {', '.join(FUNCTIONS)}")
        if aggregate["fn"] != "count" and aggregate.get("column") not in known:
            raise TableError(f"aggregate {aggregate['fn']}: нет столбца {aggregate.get('column')!r}")


def aggregate_csv(path: Path, *, filters: list[Any] | None = None, group_by: list[Any] | None = None,
                  aggregates: list[Any] | None = None) -> dict[str, Any]:
    filters, group_by = list(filters or []), [str(column) for column in (group_by or [])]
    aggregates = list(aggregates or [{"fn": "count"}])
    columns, rows = _read(path)
    _validate(columns, filters, group_by, aggregates)
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    total = matched = 0
    skipped: dict[str, int] = {}
    for row in rows:
        total += 1
        if not all(_matches(row, condition) for condition in filters):
            continue
        matched += 1
        key = tuple(str(row.get(column) or "").strip() for column in group_by)
        state = groups.setdefault(key, {"count": 0, "values": {}, "distinct": {}})
        state["count"] += 1
        for aggregate in aggregates:
            column = aggregate.get("column")
            if aggregate["fn"] == "count" or not column:
                continue
            cell = str(row.get(column) or "").strip()
            if aggregate["fn"] == "count_distinct":
                state["distinct"].setdefault(column, set()).add(cell.casefold())
                continue
            number = _number(cell)
            if number is None:
                skipped[column] = skipped.get(column, 0) + 1
                continue
            state["values"].setdefault(column, []).append(number)
    results = []
    for key, state in list(groups.items())[:_MAX_GROUPS]:
        entry: dict[str, Any] = dict(zip(group_by, key)) if group_by else {}
        for aggregate in aggregates:
            fn, column = aggregate["fn"], aggregate.get("column")
            label = fn if fn == "count" and not column else f"{fn}({column})"
            values = state["values"].get(column, [])
            if fn == "count":
                entry[label] = state["count"]
            elif fn == "count_distinct":
                entry[label] = len(state["distinct"].get(column, set()))
            elif not values:
                entry[label] = None
            elif fn == "sum":
                entry[label] = plain(sum(values, Decimal(0)))
            elif fn == "avg":
                entry[label] = plain((sum(values, Decimal(0)) / len(values)).quantize(Decimal("0.0001")))
            elif fn == "min":
                entry[label] = plain(min(values))
            else:
                entry[label] = plain(max(values))
        results.append(entry)
    out: dict[str, Any] = {"ok": True, "file": path.name, "columns": columns, "rows_total": total,
                           "rows_matched": matched, "filters": filters, "group_by": group_by, "results": results}
    if len(groups) > _MAX_GROUPS:
        out["note"] = f"показаны первые {_MAX_GROUPS} групп из {len(groups)}"
    if skipped:
        out["skipped_non_numeric"] = skipped
    return out
