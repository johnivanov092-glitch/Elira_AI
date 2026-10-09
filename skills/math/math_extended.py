"""Domain dispatch inside the single MATH CLI (no application tool registration)."""
from math_common import fields


def calculate_extended(operation, params, places=None):
    if places is not None and (type(places) is not int or not 0 <= places <= 20):
        raise ValueError("places: целое от 0 до 20")
    if operation in {"geometry", "trigonometry"}:
        from math_geometry import geometry, trigonometry
        return {"geometry": geometry, "trigonometry": trigonometry}[operation](params, places)
    if operation in {"statistics", "probability"}:
        from math_statistics import statistics, probability
        return {"statistics": statistics, "probability": probability}[operation](params, places)
    if operation in {"linear_algebra", "inequality", "optimize"}:
        from math_algebra import linear, inequalities, optimize
        return {"linear_algebra": linear, "inequality": inequalities, "optimize": optimize}[operation](params, places)
    if operation in {"limit", "summation", "series", "sequence", "nsolve"}:
        from math_algebra import calculus
        return calculus(operation, params, places)
    if operation in {"discrete", "bits"}:
        from math_discrete import discrete, bits
        return {"discrete": discrete, "bits": bits}[operation](params, places)
    if operation == "dates":
        from math_dates import dates
        return dates(params, places)
    if operation == "technical":
        from math_technical import technical
        return technical(params, places)
    if operation in {"compound_interest", "depreciation", "break_even"}:
        from math_technical import finance_extended
        return finance_extended(operation, params, places)
    if operation == "dimensions":
        from math_dimensions import dimensions
        return dimensions(params, places)
    if operation == "plot":
        from math_plot import plot
        return plot(params, places)
    if operation == "unit_convert":
        from math_units import convert
        fields(params, "value from_unit to_unit", "value from_unit to_unit")
        return convert(**params, places=places)
    raise ValueError("неизвестная операция MATH")
