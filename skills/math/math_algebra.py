"""Bounded algebra, linear algebra, calculus and constrained optimization."""
import re

import sympy as sp

from math_common import fields, integer, number, numbers, outcome
from math_expression import parse, _with_timeout


def symbol(value="x"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,15}", value):
        raise ValueError("variable: неверное имя")
    return sp.Symbol(value)


def matrix(value, name="matrix"):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError(f"{name}: от 1 до 20 строк")
    rows = [numbers(row, name, maximum=20) for row in value]
    if len({len(row) for row in rows}) != 1:
        raise ValueError(f"{name}: строки должны иметь одинаковую длину")
    return sp.Matrix(rows)


def linear(p, places=None):
    allowed = {name: "matrix" for name in ("determinant", "inverse", "rank", "transpose")}
    allowed.update(add="matrix other", multiply="matrix other", solve="matrix rhs", dot="vector other", cross="vector other", norm="vector")
    fields(p, "action " + allowed.get(p.get("action"), ""), "action")
    action = p["action"]
    if action in {"dot", "cross", "norm"}:
        a = sp.Matrix(numbers(p.get("vector"), "vector", maximum=100))
        b = sp.Matrix(numbers(p.get("other"), "other", maximum=100)) if action != "norm" else None
        if b is not None and a.shape != b.shape:
            raise ValueError("размерности векторов различаются")
        if action == "cross" and a.rows != 3:
            raise ValueError("cross: нужны трёхмерные векторы")
        value = a.dot(b) if action == "dot" else a.cross(b) if action == "cross" else a.norm()
    else:
        a = matrix(p.get("matrix"))
        if action in {"determinant", "inverse"} and a.rows != a.cols:
            raise ValueError("требуется квадратная матрица")
        if action == "determinant":
            value = a.det()
        elif action == "rank":
            value = sp.Integer(a.rank())
        elif action == "inverse":
            if a.det() == 0:
                raise ValueError("матрица вырождена")
            value = a.inv()
        elif action == "transpose":
            value = a.T
        elif action in {"add", "multiply"}:
            b = matrix(p.get("other"), "other")
            if action == "add" and a.shape != b.shape or action == "multiply" and a.cols != b.rows:
                raise ValueError("несовместимые размерности матриц")
            value = a+b if action == "add" else a*b
        elif action == "solve":
            b = sp.Matrix(numbers(p.get("rhs"), "rhs", maximum=20))
            if a.rows != b.rows:
                raise ValueError("rhs не соответствует числу уравнений")
            solutions = sp.linsolve((a, b))
            value = {"solutions": [list(v) for v in solutions], "consistent": solutions is not sp.EmptySet,
                     "unique": a.rank() == a.cols and solutions is not sp.EmptySet}
        else:
            raise ValueError("неизвестная операция линейной алгебры")
    if isinstance(value, sp.MatrixBase):
        value = value.tolist()
    return outcome({"result": value}, f"{action}(inputs)", p, places=places)


def calculus(operation, p, places=None):
    allowed = {"limit": "point direction", "summation": "lower upper", "series": "point order", "sequence": "values", "nsolve": "bracket tolerance"}
    fields(p, "expression variable " + allowed[operation], "expression")
    expression = parse(p["expression"])
    variable = symbol(p.get("variable", "x"))
    if expression.free_symbols - {variable}:
        raise ValueError("выражение содержит неподставленные параметры")
    if operation == "limit":
        point = parse(str(p.get("point", "0")))
        if point.free_symbols:
            raise ValueError("point: число или inf/-inf")
        direction = p.get("direction", "+-")
        if direction not in {"+", "-", "+-"}:
            raise ValueError("direction: +, - или +-")
        value = _with_timeout(lambda: sp.limit(expression, variable, point, dir=direction))
    elif operation == "summation":
        low = integer(p.get("lower", 0), "lower", -10000, 10000)
        high = parse(str(p.get("upper", "inf")))
        if high != sp.oo and (high.is_integer is not True or not low <= high <= 10000):
            raise ValueError("upper: целое >= lower до 10000 или inf")
        value = _with_timeout(lambda: sp.summation(expression, (variable, low, high)))
    elif operation == "series":
        point = number(p.get("point", 0), "point")
        order = integer(p.get("order", 6), "order", 1, 30)
        value = _with_timeout(lambda: sp.series(expression, variable, point, order))
    elif operation == "sequence":
        indexes = numbers(p.get("values"), "values", maximum=1000)
        value = [expression.subs(variable, v) for v in indexes]
        if any(v.is_finite is not True for v in value):
            raise ValueError("последовательность не определена на всех указанных значениях")
    elif operation == "nsolve":
        bracket = numbers(p.get("bracket"), "bracket", maximum=2)
        if len(bracket) != 2 or bracket[0] >= bracket[1]:
            raise ValueError("bracket: две возрастающие границы для поиска действительного корня")
        tolerance = number(p.get("tolerance", "1e-20"), "tolerance", positive=True)
        if not sp.Rational(1, 10**40) <= tolerance <= sp.Rational(1, 1000):
            raise ValueError("tolerance: от 1e-40 до 1e-3")
        root = _with_timeout(lambda: sp.nsolve(expression, variable, tuple(bracket), solver="bisect", prec=50, tol=tolerance))
        residual = abs(sp.N(expression.subs(variable, root), 45))
        if residual > sp.sqrt(tolerance) or not bracket[0] <= root <= bracket[1]:
            raise ValueError("численный корень не прошёл проверку невязки/границ")
        from math_expression import describe_number
        root_value = describe_number(sp.Rational(str(root)), places)
        root_value.update(exact=None, approximate=True)
        value = {"root": root_value, "residual": str(residual), "tolerance": str(tolerance)}
    else:
        raise ValueError("неизвестная операция анализа")
    return outcome({"result": value}, f"{operation}({expression}, {variable})", p, places=places, approximate=operation == "nsolve")


def inequalities(p, places=None):
    fields(p, "relations variable", "relations")
    raw = p["relations"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
        raise ValueError("relations: список из 1–20 неравенств")
    variable = symbol(p.get("variable", "x"))
    operators = {"<=": sp.Le, ">=": sp.Ge, "<": sp.Lt, ">": sp.Gt, "==": sp.Eq, "!=": sp.Ne}
    relations = []
    for text in raw:
        parts = re.split(r"(<=|>=|==|!=|<|>)", str(text))
        if len(parts) != 3:
            raise ValueError("каждая строка содержит одно сравнение")
        left, right = parse(parts[0].strip()), parse(parts[2].strip())
        if (left.free_symbols | right.free_symbols) - {variable}:
            raise ValueError("неравенства поддерживают одну переменную")
        relations.append(operators[parts[1]](left, right))
    value = _with_timeout(lambda: sp.reduce_inequalities(relations, variable))
    return outcome({"solution": str(value), "domain": "real"}, "intersection(relations)", p, places=places)


def optimize(p, places=None):
    allowed = {"linear": "goal objective matrix bounds rhs equal_matrix equal_rhs", "polynomial": "expression variable lower upper"}
    fields(p, "action " + allowed.get(p.get("action"), ""), "action")
    action = p["action"]
    if action == "linear":
        from sympy.solvers.simplex import lpmin, lpmax
        goal = p.get("goal", "min")
        if goal not in {"min", "max"}:
            raise ValueError("goal: min или max")
        c = numbers(p.get("objective"), "objective", maximum=20)
        a = matrix(p["matrix"]) if "matrix" in p else None
        b = numbers(p["rhs"], "rhs", maximum=20) if "rhs" in p else None
        ae = matrix(p["equal_matrix"]) if "equal_matrix" in p else None
        be = numbers(p["equal_rhs"], "equal_rhs", maximum=20) if "equal_rhs" in p else None
        for mat, rhs in ((a, b), (ae, be)):
            if (mat is None) != (rhs is None) or mat is not None and (mat.cols != len(c) or mat.rows != len(rhs)):
                raise ValueError("размерности ограничений не соответствуют целевой функции")
        bounds = p.get("bounds")
        if bounds is not None:
            if not isinstance(bounds, list) or len(bounds) != len(c):
                raise ValueError("bounds: пара границ на каждую переменную")
            validated = []
            for item in bounds:
                if not isinstance(item, list) or len(item) != 2:
                    raise ValueError("bounds: [lower,upper], null означает отсутствие границы")
                low, high = [None if v is None else number(v) for v in item]
                if low is not None and high is not None and low > high:
                    raise ValueError("bounds: lower > upper")
                validated.append((low, high))
            bounds = validated
        variables = sp.symbols(f"x0:{len(c)}")
        vector = sp.Matrix(variables)
        constraints = []
        if a is not None:
            constraints.extend(v <= r for v, r in zip(a*vector, b))
        if ae is not None:
            constraints.extend(sp.Eq(v, r) for v, r in zip(ae*vector, be))
        for variable, (low, high) in zip(variables, bounds or [(0, None)]*len(c)):
            if low is not None:
                constraints.append(variable >= low)
            if high is not None:
                constraints.append(variable <= high)
        if any(item is sp.false for item in constraints):
            raise ValueError("несовместимые ограничения")
        constraints = [item for item in constraints if item is not sp.true]
        objective = sum(coefficient*variable for coefficient, variable in zip(c, variables))
        solve = lpmin if goal == "min" else lpmax
        value, assignment = _with_timeout(lambda: solve(objective, constraints))
        solution = [assignment.get(variable, sp.Integer(0)) for variable in variables]
        assigned = dict(zip(variables, solution))
        if any(item.subs(assigned) is not sp.true for item in constraints) or objective.subs(assigned) != value:
            raise ValueError("решение оптимизатора не прошло проверку ограничений/целевой функции")
        return outcome({"minimum" if goal == "min" else "maximum": value, "solution": solution}, f"{goal}(c*x), A*x<=b, Aeq*x=beq; default x>=0", p, places=places)
    if action == "polynomial":
        variable = symbol(p.get("variable", "x"))
        expression = parse(p.get("expression", ""))
        if expression.free_symbols - {variable} or not expression.is_polynomial(variable) or sp.degree(expression, variable) > 10:
            raise ValueError("нужен полином одной переменной степени <=10")
        low, high = number(p.get("lower")), number(p.get("upper"))
        if low >= high:
            raise ValueError("lower < upper обязательно")
        critical = sp.solveset(sp.diff(expression, variable), variable, domain=sp.Interval(low, high))
        if expression.diff(variable) == 0:
            candidates = [low, high]
        elif isinstance(critical, sp.FiniteSet):
            candidates = [low, high, *critical]
        else:
            raise ValueError("не удалось перечислить все стационарные точки")
        pairs = [(v, expression.subs(variable, v)) for v in candidates]
        minimum, maximum = min(pairs, key=lambda item: item[1]), max(pairs, key=lambda item: item[1])
        return outcome({"minimum": {"x": minimum[0], "value": minimum[1]}, "maximum": {"x": maximum[0], "value": maximum[1]}}, "f(endpoints and real critical points)", p, places=places)
    raise ValueError("optimize action: linear или polynomial")
