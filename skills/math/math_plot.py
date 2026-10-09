"""Render a bounded numerical plot to a new PNG, ready for resource_publish."""
import io
import math
from pathlib import Path

import sympy as sp

from math_common import fields, integer, number, numbers, outcome
from math_expression import parse


def plot(p, places=None):
    allowed = {"function": "expression variable lower upper samples", "normal": "mean stddev lower upper samples", "binomial": "n p", "data": "x y"}
    fields(p, "kind path title x_label y_label " + allowed.get(p.get("kind"), ""), "kind path")
    destination = Path(p["path"]).expanduser().resolve()
    if destination.suffix.lower() != ".png":
        raise ValueError("path: нужен новый PNG в рабочей папке")
    if destination.exists():
        raise ValueError("выходной файл уже существует")
    if not destination.parent.is_dir():
        raise ValueError("папка результата не существует")
    kind = p["kind"]
    if kind == "data":
        x, y = numbers(p.get("x"), maximum=2000), numbers(p.get("y"), maximum=2000)
        if len(x) != len(y):
            raise ValueError("x/y должны иметь одинаковую длину")
        x, y = list(map(float, x)), list(map(float, y))
    elif kind == "binomial":
        n = integer(p.get("n"), "n", 1, 500)
        q = number(p.get("p"), "p", minimum=0)
        if q > 1:
            raise ValueError("p: от 0 до 1")
        x = list(range(n+1))
        y = [float(sp.binomial(n, k)*q**k*(1-q)**(n-k)) for k in x]
    elif kind in {"function", "normal"}:
        low, high = number(p.get("lower")), number(p.get("upper"))
        if low >= high:
            raise ValueError("lower < upper обязательно")
        count = integer(p.get("samples", 200), "samples", 2, 2000)
        x = [float(low+(high-low)*i/(count-1)) for i in range(count)]
        if kind == "function":
            from math_algebra import symbol
            variable = symbol(p.get("variable", "x"))
            expression = parse(p.get("expression", ""))
            if expression.free_symbols - {variable}:
                raise ValueError("выражение содержит неподставленные параметры")
            y = []
            for value in x:
                evaluated = expression.subs(variable, value)
                y.append(float(evaluated) if evaluated.is_real and evaluated.is_finite else math.nan)
        else:
            mean = float(number(p.get("mean", 0)))
            deviation = float(number(p.get("stddev"), positive=True))
            y = [math.exp(-0.5*((v-mean)/deviation)**2)/(deviation*math.sqrt(2*math.pi)) for v in x]
    else:
        raise ValueError("kind: function, data, normal или binomial")
    finite = sum(math.isfinite(value) for value in y)
    if finite < 2 or any(not math.isfinite(value) for value in x):
        raise ValueError("недостаточно конечных точек для графика")
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
    except ImportError as exc:
        raise ValueError("графики требуют matplotlib из requirements.txt в .venv навыка") from exc
    figure, axis = plt.subplots(figsize=(8, 5), layout="constrained")
    try:
        if kind == "binomial":
            axis.bar(x, y)
        else:
            axis.plot(x, y, linewidth=1.5)
        axis.set(title=str(p.get("title", "MATH"))[:150], xlabel=str(p.get("x_label", "x"))[:80], ylabel=str(p.get("y_label", "y"))[:80])
        axis.grid(True, alpha=0.25)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=150)
        with destination.open("xb") as handle:
            handle.write(buffer.getvalue())
    finally:
        plt.close(figure)
    return outcome({"path": str(destination), "points": len(x), "finite_points": finite,
                    "publication": "resource_publish required", "sampling": "numerical; values between samples not certified"},
                   p.get("expression", kind), p, approximate=True)
