"""Dimension checking on the original AST, before algebraic cancellation."""
import ast

from math_common import fields, outcome
from math_expression import _normalize
from math_units import _lookup, _TEMPERATURE

# SI exponents L, M, T, I, temperature, information. Angle is dimensionless.
_DIMENSIONS = {
    "length": (1, 0, 0, 0, 0, 0), "area": (2, 0, 0, 0, 0, 0),
    "volume": (3, 0, 0, 0, 0, 0), "mass": (0, 1, 0, 0, 0, 0),
    "time": (0, 0, 1, 0, 0, 0), "speed": (1, 0, -1, 0, 0, 0),
    "frequency": (0, 0, -1, 0, 0, 0), "power": (2, 1, -3, 0, 0, 0),
    "energy": (2, 1, -2, 0, 0, 0), "pressure": (-1, 1, -2, 0, 0, 0),
    "current": (0, 0, 0, 1, 0, 0), "voltage": (2, 1, -3, -1, 0, 0),
    "charge": (0, 0, 1, 1, 0, 0), "data": (0, 0, 0, 0, 0, 1),
    "rate": (0, 0, -1, 0, 0, 1), "temperature": (0, 0, 0, 0, 1, 0),
}
_ZERO = (0,)*6


def _dimension(unit):
    if unit in ("1", "rad", "deg"):
        return _ZERO
    if not isinstance(unit, str):
        raise ValueError("единица должна быть строкой")
    if unit.strip().casefold() in _TEMPERATURE:
        if unit != "K":
            raise ValueError("в формулах температура задаётся в K; C/F сначала конвертируй")
        return _DIMENSIONS["temperature"]
    return _DIMENSIONS[_lookup(unit)[0]]


def dimensions(p, places=None):
    fields(p, "expression variables expected_unit", "expression variables expected_unit")
    variables = p["variables"]
    if not isinstance(variables, dict) or len(variables) > 50:
        raise ValueError("variables: объект имя -> единица, не более 50")
    text = _normalize(p["expression"])
    if len(text) > 2000:
        raise ValueError("слишком длинная формула")
    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 400:
        raise ValueError("слишком сложная формула")
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return _ZERO
        if isinstance(node, ast.Name):
            if node.id in ("pi", "e"):
                return _ZERO
            if node.id not in variables:
                raise ValueError(f"нет единицы для {node.id}")
            return _dimension(variables[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if left != right:
                    raise ValueError("складываются/вычитаются несовместимые размерности")
                return left
            if isinstance(node.op, (ast.Mult, ast.Div)):
                sign = 1 if isinstance(node.op, ast.Mult) else -1
                return tuple(a+sign*b for a, b in zip(left, right))
            if isinstance(node.op, ast.Pow):
                exponent_node = node.right
                sign = 1
                if isinstance(exponent_node, ast.UnaryOp) and isinstance(exponent_node.op, ast.USub):
                    exponent_node, sign = exponent_node.operand, -1
                if not isinstance(exponent_node, ast.Constant) or type(exponent_node.value) is not int or abs(exponent_node.value) > 20:
                    raise ValueError("степень в проверке размерностей: целое от -20 до 20")
                return tuple(a*sign*exponent_node.value for a in left)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if len(node.args) != 1:
                raise ValueError("функция размерностей требует один аргумент")
            dimension = visit(node.args[0])
            if name == "abs":
                return dimension
            if name == "sqrt":
                if any(v%2 for v in dimension):
                    raise ValueError("sqrt даёт дробную размерность")
                return tuple(v//2 for v in dimension)
            if name in {"sin", "cos", "tan", "exp", "ln", "log"} and dimension == _ZERO:
                return _ZERO
        raise ValueError("неподдерживаемая конструкция или размерный аргумент функции")
    actual, expected = visit(tree.body), _dimension(p["expected_unit"])
    return outcome({"compatible": actual == expected, "dimensions": dict(zip(("length", "mass", "time", "current", "temperature", "data"), actual)),
                    "expected_unit": p["expected_unit"]}, p["expression"], p)
