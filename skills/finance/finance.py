"""Deterministic money formulas: invoices, VAT, markup/margin, discounts, loans.

All amounts are Decimal; money is rounded half up to ``places`` (2 = tiyn).
Rates are percentages (16 means 16 %). Markup is on cost, margin is on price:
a 25 % markup is a 20 % margin.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, DecimalException, InvalidOperation
from typing import Any, Literal, overload

import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from shared_path import configure
configure()
from elira_common.numbers import parse_decimal, money, plain





OPERATIONS = (
    "invoice", "vat_add", "vat_extract", "markup", "margin", "price_from_margin",
    "discount", "percent_change", "percent_of", "loan_payment", "split",
)
_HUNDRED = Decimal(100)
_MAX_ITEMS = 500


class FinanceError(ValueError):
    """Missing or invalid inputs for a finance formula."""


@overload
def _number(params: dict[str, Any], name: str, *, required: Literal[True] = True, default: Any = None,
            minimum: Decimal | None = None) -> Decimal: ...
@overload
def _number(params: dict[str, Any], name: str, *, required: bool, default: Any = None,
            minimum: Decimal | None = None) -> Decimal | None: ...
def _number(params: dict[str, Any], name: str, *, required: bool = True, default: Any = None,
            minimum: Decimal | None = None) -> Decimal | None:
    value = params.get(name)
    if value in (None, ""):
        if required:
            raise FinanceError(f"нужен параметр {name}")
        return None if default is None else Decimal(str(default))
    try:
        number = parse_decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise FinanceError(f"{name}: не число ({value!r})") from exc
    if minimum is not None and number < minimum:
        raise FinanceError(f"{name}: не меньше {plain(minimum)}")
    return number


def _m(value: Decimal, places: int) -> str:
    return format(money(value, places), "f")


def _invoice(params: dict[str, Any], places: int) -> dict[str, Any]:
    items = params.get("items")
    if not isinstance(items, list) or not items:
        raise FinanceError("items: нужен список позиций [{name, qty, price}]")
    if len(items) > _MAX_ITEMS:
        raise FinanceError(f"items: не больше {_MAX_ITEMS} позиций")
    markup = _number(params, "markup_percent", required=False, default=0, minimum=Decimal(0))
    discount = _number(params, "discount_percent", required=False, default=0, minimum=Decimal(0))
    vat = _number(params, "vat_percent", required=False, default=0, minimum=Decimal(0))
    if discount > _HUNDRED or vat > _HUNDRED:
        raise FinanceError("discount_percent и vat_percent: от 0 до 100")
    included = bool(params.get("prices_include_vat", False))
    lines, subtotal = [], Decimal(0)
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise FinanceError(f"позиция {index}: нужен объект {{name, qty, price}}")
        qty = _number(item, "qty", required=False, default=1, minimum=Decimal(0))
        price = _number(item, "price", minimum=Decimal(0))
        unit = money(price * (1 + markup / _HUNDRED), places)
        total = money(unit * qty, places)
        subtotal += total
        lines.append({"n": index, "name": str(item.get("name") or f"позиция {index}"), "qty": plain(qty),
                      "price": plain(price), "unit_price": _m(unit, places), "line_total": _m(total, places)})
    discount_amount = money(subtotal * discount / _HUNDRED, places)
    after_discount = subtotal - discount_amount
    if included:
        vat_amount = money(after_discount * vat / (_HUNDRED + vat), places) if vat else Decimal(0)
        net, total = after_discount - vat_amount, after_discount
    else:
        vat_amount = money(after_discount * vat / _HUNDRED, places)
        net, total = after_discount, after_discount + vat_amount
    return {"lines": lines, "subtotal": _m(subtotal, places), "markup_percent": plain(markup),
            "discount_percent": plain(discount), "discount_amount": _m(discount_amount, places),
            "net_without_vat": _m(net, places), "vat_percent": plain(vat),
            "prices_include_vat": included, "vat_amount": _m(vat_amount, places), "total": _m(total, places)}


def _loan(params: dict[str, Any], places: int) -> dict[str, Any]:
    principal = _number(params, "amount", minimum=Decimal(0))
    rate = _number(params, "rate_percent", minimum=Decimal(0))
    months = _number(params, "months", minimum=Decimal(1))
    if months != months.to_integral_value() or months > 1200:
        raise FinanceError("months: целое число от 1 до 1200")
    n, monthly = int(months), rate / _HUNDRED / 12
    payment = principal / n if monthly == 0 else principal * monthly / (1 - (1 + monthly) ** -n)
    payment = money(payment, places)
    return {"monthly_payment": _m(payment, places), "months": n, "total_paid": _m(payment * n, places),
            "overpayment": _m(payment * n - principal, places), "rate_percent_annual": plain(rate),
            "method": "аннуитет"}


def _split(params: dict[str, Any], places: int) -> dict[str, Any]:
    amount = _number(params, "amount")
    weights = params.get("weights")
    if not isinstance(weights, list) or not weights or len(weights) > _MAX_ITEMS:
        raise FinanceError("weights: список долей (например [1, 1, 2])")
    parsed = [_number({"w": w}, "w", minimum=Decimal(0)) for w in weights]
    total_weight = sum(parsed, Decimal(0))
    if total_weight == 0:
        raise FinanceError("weights: сумма долей больше нуля")
    shares = [money(amount * w / total_weight, places) for w in parsed]
    remainder = money(amount, places) - sum(shares, Decimal(0))
    shares[max(range(len(shares)), key=parsed.__getitem__)] += remainder
    return {"amount": plain(amount), "shares": [_m(share, places) for share in shares],
            "note": "остаток округления отнесён на самую крупную долю" if remainder else ""}


def calculate(operation: str, params: dict[str, Any] | None = None, *, places: int = 2) -> dict[str, Any]:
    operation = str(operation or "").strip().lower()
    params = dict(params or {})
    if operation not in OPERATIONS:
        raise FinanceError(f"неизвестная операция: {operation}; доступны: {', '.join(OPERATIONS)}")
    if not 0 <= int(places) <= 6:
        raise FinanceError("places: от 0 до 6")
    places = int(places)
    if operation == "invoice":
        result = _invoice(params, places)
    elif operation == "vat_add":
        amount, rate = _number(params, "amount"), _number(params, "vat_percent", minimum=Decimal(0))
        vat = money(amount * rate / _HUNDRED, places)
        result = {"net": _m(amount, places), "vat_amount": _m(vat, places), "total": _m(amount + vat, places)}
    elif operation == "vat_extract":
        amount, rate = _number(params, "amount"), _number(params, "vat_percent", minimum=Decimal(0))
        vat = money(amount * rate / (_HUNDRED + rate), places)
        result = {"total": _m(amount, places), "vat_amount": _m(vat, places), "net": _m(amount - vat, places)}
    elif operation == "markup":
        cost, rate = _number(params, "cost"), _number(params, "markup_percent")
        price = money(cost * (1 + rate / _HUNDRED), places)
        margin = (price - cost) / price * _HUNDRED if price else Decimal(0)
        result = {"cost": _m(cost, places), "price": _m(price, places), "profit": _m(price - cost, places),
                  "markup_percent": plain(rate), "margin_percent": plain(margin.quantize(Decimal("0.01")))}
    elif operation == "margin":
        cost, price = _number(params, "cost"), _number(params, "price")
        if price == 0 or cost == 0:
            raise FinanceError("cost и price не равны нулю")
        result = {"profit": _m(price - cost, places),
                  "margin_percent": plain(((price - cost) / price * _HUNDRED).quantize(Decimal("0.01"))),
                  "markup_percent": plain(((price - cost) / cost * _HUNDRED).quantize(Decimal("0.01")))}
    elif operation == "price_from_margin":
        cost, rate = _number(params, "cost"), _number(params, "margin_percent")
        if rate >= _HUNDRED:
            raise FinanceError("margin_percent меньше 100")
        price = money(cost / (1 - rate / _HUNDRED), places)
        result = {"cost": _m(cost, places), "price": _m(price, places), "profit": _m(price - cost, places),
                  "markup_percent": plain(((price - cost) / cost * _HUNDRED).quantize(Decimal("0.01")))
                  if cost else "0"}
    elif operation == "discount":
        price, rate = _number(params, "amount"), _number(params, "discount_percent", minimum=Decimal(0))
        cut = money(price * rate / _HUNDRED, places)
        result = {"amount": _m(price, places), "discount": _m(cut, places), "final": _m(price - cut, places)}
    elif operation == "percent_change":
        old, new = _number(params, "old"), _number(params, "new")
        if old == 0:
            raise FinanceError("old не равен нулю")
        result = {"change": plain(new - old),
                  "percent": plain(((new - old) / abs(old) * _HUNDRED).quantize(Decimal("0.01")))}
    elif operation == "percent_of":
        part, whole = _number(params, "part"), _number(params, "whole")
        if whole == 0:
            raise FinanceError("whole не равен нулю")
        result = {"percent": plain((part / whole * _HUNDRED).quantize(Decimal("0.0001")))}
    elif operation == "loan_payment":
        result = _loan(params, places)
    else:
        result = _split(params, places)
    return {"ok": True, "operation": operation, "places": places, **result}


def run_request(request: dict[str, Any]) -> dict[str, Any]:
    """Preserve the former finance tool result/error envelope for a JSON request."""
    if not isinstance(request, dict):
        return {"ok": False, "error": "finance_error", "text": "ERROR: нужен JSON-объект расчёта"}
    params = dict(request)
    operation = params.pop("operation", "")
    places = params.pop("places", 2)
    try:
        result = calculate(operation, params, places=int(places if places not in (None, "") else 2))
    except (FinanceError, ValueError, TypeError) as exc:
        return {"ok": False, "error": "finance_error", "text": f"ERROR: {exc}"}
    return {"ok": True, "text": f"Финансовый расчёт:\n{json.dumps(result, ensure_ascii=False, indent=2)}", "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone Decimal finance formulas; input is a JSON object.")
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 JSON request (operation, places, parameters)")
    parser.add_argument("--output", type=Path, help="Optional UTF-8 JSON result; existing files are protected")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly allow replacing --output")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.input.stat().st_size > 2_000_000:
            raise ValueError("входной JSON: не больше 2 МБ")
        if args.output and args.input.resolve() == args.output.resolve():
            raise ValueError("входной файл и результат должны иметь разные пути")
        request = json.loads(args.input.read_text(encoding="utf-8"))
        result = run_request(request)
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("w" if args.overwrite else "x", encoding="utf-8", newline="\n") as target:
                target.write(serialized)
    except (OSError, ValueError, TypeError, DecimalException, OverflowError) as exc:
        result = {"ok": False, "error": "finance_error", "text": f"ERROR: {exc}"}
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.write(serialized)
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
