"""Exact descriptive statistics and probability, without inferred observations."""
import sympy as sp

from math_common import fields, integer, number, numbers, outcome


def statistics(p, places=None):
    fields(p, "values weights y percentile sample unit", "values")
    values = numbers(p["values"])
    n = len(values)
    sample = p.get("sample", False)
    if type(sample) is not bool or sample and n < 2:
        raise ValueError("sample: boolean; для выборочной дисперсии нужно хотя бы 2 значения")
    mean = sum(values)/n
    ordered = sorted(values)
    median = ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])/2
    variance = sum((v-mean)**2 for v in values)/(n-1 if sample else n)
    result = {"count": n, "sum": sum(values), "mean": mean, "median": median,
              "min": ordered[0], "max": ordered[-1], "range": ordered[-1]-ordered[0],
              "variance": variance, "stddev": sp.sqrt(variance)}
    if "percentile" in p:
        q = number(p["percentile"], "percentile", minimum=0)
        if q > 100:
            raise ValueError("percentile: 0–100")
        position = (n-1)*q/100
        low, high = int(sp.floor(position)), int(sp.ceiling(position))
        result["percentile"] = ordered[low]+(ordered[high]-ordered[low])*(position-low)
    if "weights" in p:
        weights = numbers(p["weights"], "weights")
        if len(weights) != n or min(weights) < 0 or sum(weights) <= 0:
            raise ValueError("weights: столько же неотрицательных весов, сумма > 0")
        result["weighted_mean"] = sum(v*w for v, w in zip(values, weights))/sum(weights)
    if "y" in p:
        y = numbers(p["y"], "y")
        if len(y) != n or n < 2:
            raise ValueError("корреляция: одинаковые длины >= 2")
        ym = sum(y)/n
        xx, yy = sum((x-mean)**2 for x in values), sum((v-ym)**2 for v in y)
        if xx == 0 or yy == 0:
            raise ValueError("корреляция не определена для постоянного ряда")
        xy = sum((x-mean)*(v-ym) for x, v in zip(values, y))
        result.update(correlation=xy/sp.sqrt(xx*yy), slope=xy/xx, intercept=ym-xy/xx*mean)
    return outcome(result, "mean=sum(x)/n; variance=sum((x-mean)^2)/(n-ddof); percentile: linear (n-1)*q; slope=Sxy/Sxx", p,
                   units={"observations": p.get("unit", "1"), "variance": p.get("unit", "1")+"^2"}, places=places)


def probability(p, places=None):
    allowed = {"factorial": "n", "combinations": "n k", "permutations": "n k", "binomial": "n k p",
               "union": "a b intersection independent", "intersection": "a b intersection independent",
               "conditional": "a b intersection independent", "expectation": "values probabilities", "poisson": "mean k"}
    fields(p, "action " + allowed.get(p.get("action"), ""), "action")
    action = p["action"]
    def prob(key):
        value = number(p.get(key), key, minimum=0)
        if value > 1:
            raise ValueError(f"{key}: вероятность от 0 до 1")
        return value
    if action in {"factorial", "combinations", "permutations", "binomial"}:
        n = integer(p.get("n"), "n", high=1000)
        k = integer(p.get("k", n), "k", high=n)
        if action == "factorial":
            value, formula = sp.factorial(n), "n!"
        elif action == "combinations":
            value, formula = sp.binomial(n, k), "n!/(k!*(n-k)!)"
        elif action == "permutations":
            value, formula = sp.factorial(n)/sp.factorial(n-k), "n!/(n-k)!"
        else:
            q = prob("p")
            value, formula = sp.binomial(n, k)*q**k*(1-q)**(n-k), "C(n,k)*p^k*(1-p)^(n-k)"
    elif action in {"union", "intersection", "conditional"}:
        a, b = prob("a"), prob("b")
        independent = p.get("independent", False)
        if type(independent) is not bool:
            raise ValueError("independent: boolean")
        if independent and "intersection" in p and prob("intersection") != a*b:
            raise ValueError("intersection противоречит независимости событий")
        joint = a*b if independent else prob("intersection")
        if not max(0, a+b-1) <= joint <= min(a, b):
            raise ValueError("несовместимые вероятности событий и пересечения")
        if action == "conditional" and b == 0:
            raise ValueError("P(B)=0: условная вероятность не определена")
        value = a+b-joint if action == "union" else joint/b if action == "conditional" else joint
        formula = "P(A)+P(B)-P(A∩B)" if action == "union" else "P(A∩B)/P(B)" if action == "conditional" else "P(A∩B)"
    elif action == "expectation":
        values, probs = numbers(p.get("values")), numbers(p.get("probabilities"))
        if len(values) != len(probs) or min(probs) < 0 or sum(probs) != 1:
            raise ValueError("вероятности должны соответствовать значениям и давать сумму 1")
        value, formula = sum(v*q for v, q in zip(values, probs)), "E[X]=sum(x*p)"
    elif action == "poisson":
        mean = number(p.get("mean"), "mean", minimum=0)
        k = integer(p.get("k"), "k", high=1000)
        value, formula = sp.exp(-mean)*mean**k/sp.factorial(k), "exp(-mean)*mean^k/k!"
    else:
        raise ValueError("неизвестная вероятностная операция")
    return outcome({"result": value}, formula, p, units={"result": "1"}, places=places)
