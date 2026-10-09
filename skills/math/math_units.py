"""Unit conversion with exact decimal factors (RU and EN unit names).

Each unit maps to (dimension, factor to the dimension's base unit). Data sizes
distinguish decimal (KB, MB, GB: 1000) from binary (KiB, MiB, GiB: 1024);
temperature is affine and handled separately.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from elira_common.numbers import parse_decimal, plain

D = Fraction
_UNITS: dict[str, tuple[str, Fraction]] = {}
_FOLDED: dict[str, tuple[str, Fraction] | None] = {}


def _add(dimension: str, factor: Fraction | str | int, *names: str) -> None:
    """Exact names are case-sensitive (mW ≠ MW, b ≠ B); a case-insensitive
    alias exists only while it stays unambiguous."""
    for name in names:
        entry = (dimension, D(factor))
        _UNITS[name] = entry
        folded = name.casefold()
        if folded in _FOLDED and _FOLDED[folded] != entry:
            _FOLDED[folded] = None
        else:
            _FOLDED.setdefault(folded, entry)


# Length (base: metre)
_add("length", "0.001", "mm", "мм", "millimeter", "миллиметр")
_add("length", "0.01", "cm", "см", "centimeter", "сантиметр")
_add("length", "0.1", "dm", "дм")
_add("length", 1, "m", "м", "meter", "metre", "метр")
_add("length", 1000, "km", "км", "kilometer", "километр")
_add("length", "0.0254", "in", "inch", "дюйм", "\"")
_add("length", "0.3048", "ft", "foot", "feet", "фут")
_add("length", "0.9144", "yd", "yard", "ярд")
_add("length", "1609.344", "mi", "mile", "миля")
# Mass (base: kilogram)
_add("mass", "0.000001", "mg", "мг")
_add("mass", "0.001", "g", "г", "gram", "грамм")
_add("mass", 1, "kg", "кг", "kilogram", "килограмм")
_add("mass", 1000, "t", "т", "tonne", "тонна")
_add("mass", "0.45359237", "lb", "pound", "фунт")
_add("mass", "0.028349523125", "oz", "ounce", "унция")
# Volume (base: litre)
_add("volume", "0.001", "ml", "мл")
_add("volume", 1, "l", "л", "liter", "litre", "литр")
_add("volume", 1000, "m3", "м3", "м³", "m³")
_add("volume", "0.001", "cm3", "см3", "см³", "cc")
_add("volume", "3.785411784", "gal", "gallon", "галлон")
# Area (base: square metre)
_add("area", "0.000001", "mm2", "мм2", "мм²")
_add("area", "0.0001", "cm2", "см2", "см²")
_add("area", 1, "m2", "м2", "м²", "m²")
_add("area", 10000, "ha", "га", "hectare", "гектар")
_add("area", 100, "сотка", "ar", "ар")
_add("area", 1000000, "km2", "км2", "км²")
_add("area", "0.09290304", "ft2", "sqft")
# Time (base: second)
_add("time", "0.001", "ms", "мс")
_add("time", 1, "s", "sec", "с", "сек", "second", "секунда")
_add("time", 60, "min", "мин", "minute", "минута")
_add("time", 3600, "h", "hr", "ч", "час", "hour")
_add("time", 86400, "day", "d", "сут", "сутки", "день", "дн")
_add("time", 604800, "week", "нед", "неделя")
# Data (base: byte)
_add("data", "0.125", "bit", "бит", "b")
_add("data", 1, "byte", "B", "Б", "байт")
for _prefix_en, _prefix_ru, _power in (("K", "К", 1), ("M", "М", 2), ("G", "Г", 3), ("T", "Т", 4), ("P", "П", 5)):
    _add("data", D(1000) ** _power, f"{_prefix_en}B", f"{_prefix_ru}Б")
    _add("data", D(1024) ** _power, f"{_prefix_en}iB", f"{_prefix_ru}иБ")
    _add("data", D(1000) ** _power / 8, f"{_prefix_en}bit", f"{_prefix_ru}бит")
# Data rate (base: byte per second)
_add("rate", "0.125", "bit/s", "bps", "бит/с")
_add("rate", 1, "B/s", "Б/с")
for _prefix_en, _prefix_ru, _power in (("K", "К", 1), ("M", "М", 2), ("G", "Г", 3)):
    _add("rate", D(1000) ** _power / 8, f"{_prefix_en}bit/s", f"{_prefix_en}bps", f"{_prefix_ru}бит/с")
    _add("rate", D(1000) ** _power, f"{_prefix_en}B/s", f"{_prefix_ru}Б/с")
    _add("rate", D(1024) ** _power, f"{_prefix_en}iB/s", f"{_prefix_ru}иБ/с")
# Power (base: watt)
_add("power", "0.001", "mW", "мВт")
_add("power", 1, "W", "Вт", "watt", "ватт")
_add("power", 1000, "kW", "кВт")
_add("power", 1000000, "MW", "МВт")
_add("power", "735.49875", "hp", "л.с.", "лс")
# Energy (base: joule)
_add("energy", 1, "J", "Дж")
_add("energy", 1000, "kJ", "кДж")
_add("energy", 3600, "Wh", "Вт·ч", "Втч", "Вт*ч")
_add("energy", 3600000, "kWh", "кВт·ч", "кВтч", "кВт*ч")
_add("energy", 1000000, "MJ", "МДж")
_add("energy", "4.184", "cal", "кал")
_add("energy", "4184", "kcal", "ккал")
# Frequency (base: hertz)
_add("frequency", 1, "Hz", "Гц")
_add("frequency", 1000, "kHz", "кГц")
_add("frequency", 1000000, "MHz", "МГц")
_add("frequency", 1000000000, "GHz", "ГГц")
# Speed (base: metre per second)
_add("speed", 1, "m/s", "м/с")
_add("speed", D(1000) / D(3600), "km/h", "км/ч", "kph")
_add("speed", D("1609.344") / D(3600), "mph")
# Pressure (base: pascal)
_add("pressure", 1, "Pa", "Па")
_add("pressure", 1000, "kPa", "кПа")
_add("pressure", 1000000, "MPa", "МПа")
_add("pressure", 100000, "bar", "бар")
_add("pressure", 101325, "atm", "атм")
_add("pressure", "6894.757293168", "psi")
# Electricity
_add("current", 1, "A", "А", "ампер")
_add("current", "0.001", "mA", "мА")
_add("voltage", 1, "V", "В", "вольт")
_add("voltage", "0.001", "mV", "мВ")
_add("voltage", 1000, "kV", "кВ")
_add("charge", 1, "Ah", "А·ч", "Ач")
_add("charge", "0.001", "mAh", "мА·ч", "мАч")

_TEMPERATURE = {"c": "C", "°c": "C", "с°": "C", "°с": "C", "celsius": "C", "цельсий": "C",
                "f": "F", "°f": "F", "fahrenheit": "F", "k": "K", "kelvin": "K", "кельвин": "K"}


class UnitError(ValueError):
    """Unknown unit or incompatible dimensions."""


def _lookup(unit: str) -> tuple[str, Fraction]:
    key = str(unit or "").strip().replace(" ", "")
    if key in _UNITS:
        return _UNITS[key]
    folded = _FOLDED.get(key.casefold())
    if folded is not None:
        return folded
    if key.casefold() in _FOLDED:
        raise UnitError(f"единица {unit} неоднозначна без учёта регистра (например mW/MW, b/B) — укажите точно")
    raise UnitError(f"неизвестная единица: {unit}")


def _temperature(value: Fraction, source: str, target: str) -> Fraction:
    kelvin = {"C": value + D("273.15"), "F": (value - 32) * 5 / 9 + D("273.15"), "K": value}[source]
    if kelvin < 0:
        raise UnitError("температура ниже абсолютного нуля")
    return {"C": kelvin - D("273.15"), "F": (kelvin - D("273.15")) * 9 / 5 + 32, "K": kelvin}[target]


def convert(value: Any, from_unit: str, to_unit: str, places: int | None = None) -> dict[str, Any]:
    if len(str(value)) > 2000:
        raise UnitError("число слишком длинное")
    decimal = parse_decimal(value)
    if abs(decimal.adjusted()) > 1000:
        raise UnitError("число вне поддерживаемого диапазона")
    number = Fraction(decimal)
    source = _TEMPERATURE.get(str(from_unit or "").strip().casefold())
    target = _TEMPERATURE.get(str(to_unit or "").strip().casefold())
    if source or target:
        if not (source and target):
            raise UnitError("температуру можно переводить только в температуру (C, F, K)")
        result = _temperature(number, source, target)
        dimension = "temperature"
    else:
        dimension, factor_from = _lookup(from_unit)
        target_dimension, factor_to = _lookup(to_unit)
        if dimension != target_dimension:
            raise UnitError(f"несовместимые величины: {from_unit} ({dimension}) → {to_unit} ({target_dimension})")
        result = number * factor_from / factor_to
    from math_expression import describe_number
    import sympy as sp
    numeric = describe_number(sp.Rational(result.numerator, result.denominator), places)
    return {"ok": True, "value": plain(decimal), "from": from_unit, "to": to_unit, "dimension": dimension,
            "result": numeric["decimal"], "exact": numeric["exact"], "approximate": numeric["approximate"],
            "formula": "target=(source*factor+offset)/target_factor",
            "substitution": {"value": plain(decimal), "from_unit": from_unit, "to_unit": to_unit},
            "units": {"result": to_unit}, "precision": {"places": places, "rounding": "ROUND_HALF_UP"}}
