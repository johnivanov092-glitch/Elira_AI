"""Geometry and trigonometry with explicit dimensions and angle conventions."""
import sympy as sp

from math_common import fields, number, numbers, outcome


def geometry(p, places=None):
    allowed = {"rectangle": "length width", "circle": "radius", "triangle": "a b c", "box": "length width height",
               "sphere": "radius", "cylinder": "radius height", "cone": "radius height", "distance": "point1 point2",
               "polygon": "points", "material": "area piece_area waste_percent"}
    fields(p, "shape unit " + allowed.get(p.get("shape"), ""), "shape")
    from math_units import _lookup
    unit = p.get("unit", "m")
    if _lookup(unit)[0] != "length":
        raise ValueError("unit: геометрия требует единицу длины")
    def n(key):
        return number(p.get(key), key, positive=True)
    shape = p["shape"]
    if shape == "rectangle":
        a, b = n("length"), n("width")
        values, formula = {"area": a*b, "perimeter": 2*(a+b)}, "S=a*b; P=2*(a+b)"
    elif shape == "circle":
        r = n("radius")
        values, formula = {"area": sp.pi*r*r, "perimeter": 2*sp.pi*r}, "S=pi*r^2; P=2*pi*r"
    elif shape == "triangle":
        a, b, c = n("a"), n("b"), n("c")
        if 2*max(a, b, c) >= a+b+c:
            raise ValueError("стороны не образуют невырожденный треугольник")
        s = (a+b+c)/2
        values, formula = {"area": sp.sqrt(s*(s-a)*(s-b)*(s-c)), "perimeter": 2*s}, "s=(a+b+c)/2; S=sqrt(s*(s-a)*(s-b)*(s-c))"
    elif shape == "box":
        a, b, h = n("length"), n("width"), n("height")
        values, formula = {"volume": a*b*h, "surface_area": 2*(a*b+a*h+b*h)}, "V=a*b*h; S=2*(a*b+a*h+b*h)"
    elif shape in {"sphere", "cylinder", "cone"}:
        r = n("radius")
        if shape == "sphere":
            values, formula = {"volume": sp.Rational(4, 3)*sp.pi*r**3, "surface_area": 4*sp.pi*r*r}, "V=4*pi*r^3/3; S=4*pi*r^2"
        else:
            h = n("height")
            values = {"volume": sp.pi*r*r*h/(3 if shape == "cone" else 1)}
            values["surface_area"] = sp.pi*r*(r+sp.sqrt(r*r+h*h)) if shape == "cone" else 2*sp.pi*r*(r+h)
            formula = "V=pi*r^2*h/3; S=pi*r*(r+sqrt(r^2+h^2))" if shape == "cone" else "V=pi*r^2*h; S=2*pi*r*(r+h)"
    elif shape == "distance":
        a, b = numbers(p.get("point1"), maximum=10), numbers(p.get("point2"), maximum=10)
        if len(a) != len(b):
            raise ValueError("размерности точек различаются")
        values, formula = {"distance": sp.sqrt(sum((x-y)**2 for x, y in zip(a, b)))}, "d=sqrt(sum((x_i-y_i)^2))"
    elif shape == "polygon":
        raw = p.get("points")
        if not isinstance(raw, list) or not 3 <= len(raw) <= 100:
            raise ValueError("points: от 3 до 100 двумерных вершин в порядке обхода")
        pts = [numbers(v, maximum=2) for v in raw]
        if any(len(v) != 2 for v in pts):
            raise ValueError("нужны двумерные вершины")
        if len({tuple(v) for v in pts}) != len(pts):
            raise ValueError("вершины не должны повторяться; замыкающее ребро добавляется автоматически")
        def cross(a, b, c):
            return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        def on_segment(a, b, c):
            return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
        sides = list(zip(pts, pts[1:]+pts[:1]))
        for i, (a, b) in enumerate(sides):
            prev = pts[i-1]
            if cross(prev, a, b) == 0 and sum((u-v)*(w-v) for u, v, w in zip(prev, a, b)) > 0:
                raise ValueError("перекрывающиеся соседние рёбра")
            for j in range(i+2, len(sides)):
                if i == 0 and j == len(sides)-1:
                    continue
                c, d = sides[j]
                ab_c, ab_d, cd_a, cd_b = cross(a,b,c), cross(a,b,d), cross(c,d,a), cross(c,d,b)
                if (ab_c*ab_d < 0 and cd_a*cd_b < 0 or
                    ab_c == 0 and on_segment(a,b,c) or ab_d == 0 and on_segment(a,b,d) or
                    cd_a == 0 and on_segment(c,d,a) or cd_b == 0 and on_segment(c,d,b)):
                    raise ValueError("самопересечение многоугольника")
        area = abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in sides))/2
        if area == 0:
            raise ValueError("вырожденный многоугольник")
        values = {"area": area, "perimeter": sum(sp.sqrt(sum((x-y)**2 for x,y in zip(a,b))) for a,b in sides)}
        formula = "S=abs(sum(x_i*y_next-y_i*x_next))/2"
    elif shape == "material":
        waste = number(p.get("waste_percent", 0), "waste_percent", minimum=0)
        values, formula = {"pieces": sp.ceiling(n("area")*(1+waste/100)/n("piece_area"))}, "N=ceil(S*(1+waste/100)/piece_area)"
    else:
        raise ValueError("неизвестная shape")
    units = {k: "count" if k == "pieces" else unit+"^3" if k == "volume" else unit+"^2" if "area" in k else unit for k in values}
    return outcome(values, formula, p, units=units, places=places)


def trigonometry(p, places=None):
    allowed = {name: "value" for name in ("sin", "cos", "tan", "asin", "acos", "atan", "degrees", "radians")}
    allowed.update(cosine_side="a b angle", triangle_angles="a b c")
    fields(p, "action angle_unit " + allowed.get(p.get("action"), ""), "action")
    action = p["action"]
    angle_unit = p.get("angle_unit", "rad")
    if angle_unit not in {"deg", "rad"}:
        raise ValueError("angle_unit: deg или rad")
    scale = sp.pi/180 if angle_unit == "deg" else 1
    if action in {"sin", "cos", "tan", "asin", "acos", "atan", "degrees", "radians"}:
        value = number(p.get("value"))
        if action in {"asin", "acos"} and abs(value) > 1:
            raise ValueError("область asin/acos: [-1,1]")
        if action in {"degrees", "radians"}:
            result = value*(180/sp.pi if action == "degrees" else sp.pi/180)
        elif action in {"asin", "acos", "atan"}:
            result = getattr(sp, action)(value)/scale
        else:
            result = getattr(sp, action)(value*scale)
        if result.is_finite is not True:
            raise ValueError("функция не определена в этой точке")
        return outcome({"result": result}, f"{action}(value)", p,
                       units={"result": angle_unit if action.startswith("a") else "deg" if action == "degrees" else "rad" if action == "radians" else "1"}, places=places)
    if action == "cosine_side":
        a, b = number(p.get("a"), positive=True), number(p.get("b"), positive=True)
        angle = number(p.get("angle"))*scale
        if not 0 < angle < sp.pi:
            raise ValueError("угол треугольника должен быть между 0 и pi")
        return outcome({"c": sp.sqrt(a*a+b*b-2*a*b*sp.cos(angle))}, "c=sqrt(a^2+b^2-2ab*cos(angle))", p, places=places)
    if action == "triangle_angles":
        a, b, c = [number(p.get(k), k, positive=True) for k in ("a", "b", "c")]
        if 2*max(a, b, c) >= a+b+c:
            raise ValueError("неверные стороны треугольника")
        values = {"A": sp.acos((b*b+c*c-a*a)/(2*b*c))/scale,
                  "B": sp.acos((a*a+c*c-b*b)/(2*a*c))/scale,
                  "C": sp.acos((a*a+b*b-c*c)/(2*a*b))/scale}
        return outcome(values, "A=acos((b^2+c^2-a^2)/(2bc))", p, units={k: angle_unit for k in values}, places=places)
    raise ValueError("неизвестная тригонометрическая операция")
