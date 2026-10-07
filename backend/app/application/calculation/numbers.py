"""Decimal parsing and rounding shared by money and table calculations."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

_CURRENCY_TOKENS = ("₸", "тг.", "тг", "тенге", "KZT", "kzt", "₽", "руб.", "руб", "RUB", "$", "USD", "€", "EUR")


def parse_decimal(value: Any) -> Decimal:
    """Parse a number as written in price lists: spaces, NBSP, currency, comma decimals.

    Raises ``InvalidOperation`` for anything that is not a finite number.
    """
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        raw = str(value).strip().replace(" ", "").replace(" ", "").replace(" ", "")
        for token in _CURRENCY_TOKENS:
            raw = raw.replace(token, "")
        if "," in raw and "." not in raw:
            raw = raw.replace(",", ".")
        elif "," in raw and "." in raw:
            raw = raw.replace(",", "")
        number = Decimal(raw)
    if not number.is_finite():
        raise InvalidOperation
    return number


def money(value: Decimal, places: int = 2) -> Decimal:
    """Round half up to ``places`` decimal places (2 = tiyn/kopecks)."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def plain(value: Decimal) -> str:
    """Decimal without exponent or trailing zeros after the point."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text
