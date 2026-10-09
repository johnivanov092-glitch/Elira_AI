"""Exact arithmetic and algebra without executing input as code.

The expression is parsed with ``ast`` and every node is converted to a SymPy
object through an allowlist: numbers, named symbols, arithmetic operators and
a fixed set of functions. Attribute access, subscripts, imports, lambdas,
keywords and any other syntax are rejected, so model- or web-supplied text can
never reach ``eval`` (SymPy's own ``sympify``/``parse_expr`` would).
"""
from __future__ import annotations

import ast
import re
import threading
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Callable

import sympy as sp

from elira_common.numbers import plain

OPERATIONS = ("evaluate", "simplify", "expand", "factor", "solve", "diff", "integrate")
_MAX_CHARS = 2000
_MAX_NODES = 400
_MAX_EXPONENT = 10_000
_MAX_FACTORIAL = 1_000
_SYMBOLIC_TIMEOUT_SECONDS = 15
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,15}$")


class CalcError(ValueError):
    """Input is not an allowed expression, or the operation cannot finish."""


def _log(value: Any, base: Any = None) -> Any:
    return sp.log(value) if base is None else sp.log(value, base)


def _factorial(value: Any) -> Any:
    if value.is_number and (not value.is_integer or abs(value) > _MAX_FACTORIAL):
        raise CalcError(f"factorial: нужно целое число не больше {_MAX_FACTORIAL}")
    return sp.factorial(value)


def _binomial(n: Any, k: Any) -> Any:
    if n.is_number and (n.is_integer is not True or not 0 <= n <= 1000):
        raise CalcError("binomial: n целое от 0 до 1000")
    if k.is_number and (k.is_integer is not True or not 0 <= k <= 1000):
        raise CalcError("binomial: k целое от 0 до 1000")
    return sp.binomial(n, k)


def _round(value: Any, places: Any = sp.Integer(0)) -> Any:
    if not value.is_number or places.is_integer is not True or not -20 <= places <= 20:
        raise CalcError("round: нужны число и целое количество знаков")
    quantum = Decimal(1).scaleb(-int(places))
    decimal = _to_decimal(value)
    with localcontext() as context:
        context.prec = max(50, decimal.adjusted() + abs(int(places)) + 10)
        return sp.Rational(str(decimal.quantize(quantum, rounding=ROUND_HALF_UP)))


_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sqrt": sp.sqrt, "root": sp.root, "abs": sp.Abs, "exp": sp.exp, "ln": sp.log, "log": _log,
    "log10": lambda value: sp.log(value, 10), "log2": lambda value: sp.log(value, 2),
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
    "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
    "floor": sp.floor, "ceil": sp.ceiling, "min": sp.Min, "max": sp.Max, "mod": sp.Mod,
    "gcd": sp.gcd, "lcm": sp.lcm, "factorial": _factorial, "binomial": _binomial, "round": _round,
}
_CONSTANTS = {"pi": sp.pi, "e": sp.E, "inf": sp.oo}


def _normalize(text: str) -> str:
    """Typographic and everyday notation → Python operator syntax."""
    text = str(text or "").strip()
    for old, new in (("×", "*"), ("·", "*"), ("÷", "/"), ("−", "-"), ("–", "-"), ("^", "**"),
                     ("²", "**2"), ("³", "**3"), (" ", " "), (" ", " ")):
        text = text.replace(old, new)
    if re.search(r"\b0[xXoObB][0-9A-Fa-f_]+", text):
        raise CalcError("системы счисления: используйте operation=bits, action=base")
    # Thousands separators inside numbers: 125 000 → 125000.
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", text)
    # 16% → (16/100) when % ends the operand; modulo is written mod(a, b).
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%(?=\s*(?:$|[-+*/),;=]))", r"(\1/100)", text)
    # Implicit multiplication as written by hand: 2x, 3(x+1), (a)(b), (x+1)y.
    # Digits inside names (log2) and exponents (1e5) are left alone.
    text = re.sub(r"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)\s*(?=[A-Za-z(])(?![eE][+-]?\d)", r"\1*", text)
    return re.sub(r"\)\s*(?=[A-Za-z0-9(])", ")*", text)


def _equations(text: str) -> list[str]:
    parts = [part.strip() for part in _normalize(text).split(";") if part.strip()]
    out = []
    for part in parts:
        if re.search(r"(?<![<>=!])=(?!=)", part):
            left, right = re.split(r"(?<![<>=!])=(?!=)", part, maxsplit=1)
            part = f"({left}) - ({right})"
        out.append(part)
    return out


class _Builder:
    def __init__(self) -> None:
        self.nodes = 0
        self.source = ""

    def build(self, text: str) -> Any:
        if len(text) > _MAX_CHARS:
            raise CalcError(f"выражение длиннее {_MAX_CHARS} символов")
        self.source = text
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError as exc:
            raise CalcError(f"не разобрать выражение: {exc.msg}") from exc
        return self._node(tree.body)

    def _node(self, node: ast.AST) -> Any:
        self.nodes += 1
        if self.nodes > _MAX_NODES:
            raise CalcError("выражение слишком большое")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            literal = ast.get_source_segment(self.source, node).replace("_", "")
            exponent = re.search(r"[eE]([+-]?\d+)$", literal)
            if exponent and abs(int(exponent[1])) > 1000:
                raise CalcError("числовой литерал: показатель не больше 1000")
            return sp.Rational(literal)
        if isinstance(node, ast.Name):
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            if not _NAME.match(node.id) or node.id in _FUNCTIONS:
                raise CalcError(f"недопустимое имя: {node.id}")
            return sp.Symbol(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self._node(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = self._node(node.left), self._node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise CalcError("деление на ноль")
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                if right == 0:
                    raise CalcError("деление на ноль")
                return sp.floor(left / right)
            if isinstance(node.op, ast.Mod):
                return sp.Mod(left, right)
            if isinstance(node.op, ast.Pow):
                if right.is_number and abs(right) > _MAX_EXPONENT:
                    raise CalcError(f"показатель степени больше {_MAX_EXPONENT}")
                if left.is_Rational and right.is_Rational and right.is_integer:
                    bits = max(int(left.p).bit_length(), int(left.q).bit_length())
                    if bits * abs(int(right)) > 13000:
                        raise CalcError("степень создаёт слишком большое число")
                return left ** right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            function = _FUNCTIONS.get(node.func.id)
            if function is None:
                raise CalcError(f"неизвестная функция: {node.func.id}")
            if len(node.args) > 8:
                raise CalcError("слишком много аргументов")
            return function(*(self._node(arg) for arg in node.args))
        raise CalcError(f"недопустимая конструкция: {type(node).__name__}")


def parse(text: str) -> Any:
    return _Builder().build(_normalize(text))


def _to_decimal(value: Any, digits: int = 34) -> Decimal:
    with localcontext() as context:
        if value.is_Rational:
            # Avoid both Decimal's default 28-digit rounding and enormous output.
            magnitude = max(int(value.p).bit_length(), int(value.q).bit_length())
            if magnitude > 13000:
                raise CalcError("число слишком велико: максимум около 3900 цифр")
            context.prec = max(digits, int(magnitude * 0.302) + 40)
            return Decimal(int(value.p)) / Decimal(int(value.q))
        decimal = Decimal(str(sp.N(value, digits)))
        if not decimal.is_finite() or abs(decimal.adjusted()) > 3900:
            raise CalcError("десятичный результат вне поддерживаемого диапазона")
        return Decimal(str(sp.N(value, max(digits, decimal.adjusted() + 40))))


def _terminates(value: Any) -> bool:
    if not value.is_Rational:
        return False
    q = int(value.q)
    for prime in (2, 5):
        while q % prime == 0:
            q //= prime
    return q == 1


def describe_number(value: Any, places: int | None = None) -> dict[str, Any]:
    """Exact form and a decimal; ``approximate`` is True when the decimal is not exact."""
    value = sp.nsimplify(value) if value.is_Float else value
    if value.is_real is False or not value.is_finite:
        return {"exact": str(value), "decimal": str(sp.N(value, 20)), "approximate": True}
    decimal = _to_decimal(value)
    exact = _terminates(value)
    with localcontext() as context:
        context.prec = max(50, len(decimal.as_tuple().digits) + 30, decimal.adjusted() + 50)
        if places is not None:
            decimal = decimal.quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
        else:
            decimal = decimal.normalize() if exact else decimal.quantize(Decimal("1e-20"), rounding=ROUND_HALF_UP)
    result = {"exact": str(value), "decimal": plain(decimal) if places is None else format(decimal, "f"),
              "approximate": not exact or sp.Rational(str(decimal)) != value}
    if places is not None:
        result["rounded_to"] = int(places)
    return result


def _with_timeout(function: Callable[[], Any]) -> Any:
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = function()
        except Exception as exc:  # noqa: BLE001 - reported to the model as a calculation error
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True, name="calc-symbolic")
    worker.start()
    worker.join(_SYMBOLIC_TIMEOUT_SECONDS)
    if worker.is_alive():
        raise CalcError(f"расчёт не уложился в {_SYMBOLIC_TIMEOUT_SECONDS} с — упростите задачу")
    if "error" in outcome:
        error = outcome["error"]
        raise error if isinstance(error, CalcError) else CalcError(str(error))
    return outcome["value"]


def _variables(text: str, expressions: list[Any]) -> list[Any]:
    names = [name.strip() for name in str(text or "").split(",") if name.strip()]
    for name in names:
        if not _NAME.match(name):
            raise CalcError(f"недопустимое имя переменной: {name}")
    if names:
        return [sp.Symbol(name) for name in names]
    free = sorted(set().union(*(expression.free_symbols for expression in expressions)), key=str)
    if not free:
        raise CalcError("в выражении нет переменных")
    return free


def calculate(expression: str, operation: str = "evaluate", *, variable: str = "",
              lower: str = "", upper: str = "", order: int = 1, places: int | None = None) -> dict[str, Any]:
    """Run one calculation; returns a JSON-ready dict with ``ok`` and the result."""
    operation = str(operation or "evaluate").strip().lower()
    if operation not in OPERATIONS:
        raise CalcError(f"неизвестная операция: {operation}; доступны: {', '.join(OPERATIONS)}")
    if places is not None and not 0 <= int(places) <= 20:
        raise CalcError("places: от 0 до 20")
    if operation == "solve":
        equations = [_Builder().build(part) for part in _equations(expression)]
        if not variable:
            # Preserve names canceled by simplification, e.g. x=x.
            names = {node.id for part in _equations(expression) for node in ast.walk(ast.parse(part, mode="eval"))
                     if isinstance(node, ast.Name) and node.id not in _FUNCTIONS and node.id not in _CONSTANTS}
            variable = ",".join(sorted(names))
        variables = _variables(variable, equations)
        if len(variables) == 1:
            sets = _with_timeout(lambda: [sp.solveset(equation, variables[0], domain=sp.S.Complexes) for equation in equations])
            solution_set = sp.Intersection(*sets)
            rows = [{str(variables[0]): describe_number(v, places) if v.is_number else {"exact": str(v)}}
                    for v in sorted(solution_set, key=sp.default_sort_key)] if isinstance(solution_set, sp.FiniteSet) else []
            note = "" if rows else "решений нет" if solution_set is sp.EmptySet else "точное множество решений; параметры целые согласно ImageSet; ConditionSet означает неразрешённое условие"
            return {"ok": True, "operation": operation, "equations": [f"{e} = 0" for e in map(str, equations)],
                    "variables": [str(variables[0])], "solutions": rows, "solution_set": str(solution_set), "note": note}
        if all(equation == 0 for equation in equations):
            return {"ok": True, "operation": operation, "equations": ["0 = 0" for _ in equations],
                    "variables": [str(v) for v in variables], "solutions": [{str(v): {"exact": str(v)} for v in variables}],
                    "solution_set": "all complex values", "note": "тождество: любые значения указанных переменных"}
        solutions = _with_timeout(lambda: sp.solve(equations, variables, dict=True))
        rows = []
        for solution in solutions:
            row = {}
            for symbol, value in solution.items():
                row[str(symbol)] = describe_number(value, places) if value.is_number else {"exact": str(value)}
            rows.append(row)
        return {"ok": True, "operation": operation, "equations": [f"{e} = 0" for e in map(str, equations)],
                "variables": [str(v) for v in variables], "solutions": rows,
                "note": "" if rows else "решений нет (в области комплексных чисел SymPy)"}
    value = parse(expression)
    if operation == "evaluate":
        if value.has(sp.zoo, sp.nan) or value.is_finite is False:
            raise CalcError("результат не является конечным числом")
        if value.free_symbols:
            names = ", ".join(sorted(map(str, value.free_symbols)))
            raise CalcError(f"есть переменные ({names}): используйте simplify/solve/diff/integrate")
        parsed = str(value)
        value = _with_timeout(lambda: sp.simplify(value) if not value.is_Rational else value)
        return {"ok": True, "operation": operation, "expression": parsed, "result": describe_number(value, places)}
    if operation in {"simplify", "expand", "factor"}:
        function = {"simplify": sp.simplify, "expand": sp.expand, "factor": sp.factor}[operation]
        result = _with_timeout(lambda: function(value))
        return {"ok": True, "operation": operation, "result": str(result)}
    symbol = _variables(variable, [value])[0]
    if operation == "diff":
        if not 1 <= int(order) <= 10:
            raise CalcError("order: от 1 до 10")
        result = _with_timeout(lambda: sp.diff(value, symbol, int(order)))
        return {"ok": True, "operation": operation, "variable": str(symbol), "result": str(sp.simplify(result))}
    if (lower == "") != (upper == ""):
        raise CalcError("для определённого интеграла нужны обе границы lower и upper")
    if lower != "":
        low, high = parse(lower), parse(upper)
        result = _with_timeout(lambda: sp.integrate(value, (symbol, low, high)))
        payload: dict[str, Any] = {"exact": str(result)}
        if result.is_number:
            payload = describe_number(result, places)
        return {"ok": True, "operation": operation, "variable": str(symbol), "bounds": [lower, upper],
                "result": payload}
    result = _with_timeout(lambda: sp.integrate(value, symbol))
    return {"ok": True, "operation": operation, "variable": str(symbol), "result": f"{result} + C"}
