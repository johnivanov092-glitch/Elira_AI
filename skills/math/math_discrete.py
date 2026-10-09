"""Finite sets, truth operations, integer bit operations and bounded graphs."""
import graphlib
import heapq

import sympy as sp

from math_common import fields, integer, number, outcome


def discrete(p, places=None):
    allowed = {name: "left right" for name in ("union", "intersection", "difference", "symmetric_difference", "subset")}
    allowed.update({name: "a b" for name in ("and", "or", "xor", "implies")})
    allowed.update({"not": "a", "shortest_path": "nodes edges source target directed", "topological": "nodes edges directed"})
    fields(p, "action " + allowed.get(p.get("action"), ""), "action")
    action = p["action"]
    if action in {"union", "intersection", "difference", "symmetric_difference", "subset"}:
        def items(key):
            raw = p.get(key)
            if not isinstance(raw, list) or len(raw) > 10000 or any(not isinstance(x, str) for x in raw):
                raise ValueError("множества: списки строк до 10000 элементов")
            return set(raw)
        a, b = items("left"), items("right")
        value = a <= b if action == "subset" else sorted(getattr(a, action)(b))
    elif action in {"and", "or", "xor", "not", "implies"}:
        a, b = p.get("a"), p.get("b")
        if type(a) is not bool or action != "not" and type(b) is not bool:
            raise ValueError("логические операнды: JSON boolean")
        value = not a if action == "not" else (a and b) if action == "and" else (a or b) if action == "or" else (a != b) if action == "xor" else (not a or b)
    elif action in {"shortest_path", "topological"}:
        nodes, edges = p.get("nodes"), p.get("edges")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 200 or any(not isinstance(n, str) or not n for n in nodes) or len(set(nodes)) != len(nodes):
            raise ValueError("nodes: 1–200 уникальных непустых строк")
        if not isinstance(edges, list) or len(edges) > 2000:
            raise ValueError("edges: до 2000 рёбер [from,to,weight]")
        directed = p.get("directed", True)
        if type(directed) is not bool or action == "topological" and not directed:
            raise ValueError("directed: boolean; зависимости всегда направленные")
        graph = {n: [] for n in nodes}
        parents = {n: set() for n in nodes}
        for edge in edges:
            if not isinstance(edge, list) or len(edge) not in {2, 3} or any(not isinstance(v, str) or v not in graph for v in edge[:2]):
                raise ValueError("ребро должно ссылаться на nodes")
            a, b = edge[:2]
            weight = number(edge[2] if len(edge) == 3 else 1, "weight", minimum=0)
            graph[a].append((b, weight))
            parents[b].add(a)
            if not directed:
                graph[b].append((a, weight))
        if action == "topological":
            try:
                value = list(graphlib.TopologicalSorter(parents).static_order())
            except graphlib.CycleError as exc:
                raise ValueError("в зависимостях найден цикл") from exc
        else:
            source, target = p.get("source"), p.get("target")
            if source not in graph or target not in graph:
                raise ValueError("source/target должны находиться в nodes")
            distances, previous, queue = {source: sp.Integer(0)}, {}, [(sp.Integer(0), source)]
            while queue:
                distance, node = heapq.heappop(queue)
                if distance != distances[node]:
                    continue
                if node == target:
                    break
                for neighbor, weight in graph[node]:
                    candidate = distance+weight
                    if neighbor not in distances or candidate < distances[neighbor]:
                        distances[neighbor], previous[neighbor] = candidate, node
                        heapq.heappush(queue, (candidate, neighbor))
            if target not in distances:
                value = {"reachable": False, "path": [], "distance": None}
            else:
                path, current = [target], target
                while current != source:
                    current = previous[current]
                    path.append(current)
                value = {"reachable": True, "path": path[::-1], "distance": distances[target]}
    else:
        raise ValueError("неизвестная дискретная операция")
    return outcome({"result": value}, action, p, places=places)


def bits(p, places=None):
    allowed = {"base": "base target_base", "and": "other width", "or": "other width", "xor": "other width", "not": "width", "left_shift": "shift width", "right_shift": "shift width"}
    fields(p, "action value " + allowed.get(p.get("action"), ""), "action value")
    action = p["action"]
    if action == "base":
        base, target = integer(p.get("base", 10), "base", 2, 36), integer(p.get("target_base", 16), "target_base", 2, 36)
        raw = p["value"]
        if not isinstance(raw, str) or not 1 <= len(raw) <= 1024:
            raise ValueError("value: строка до 1024 цифр")
        value = int(raw, base)
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        digits, remainder = "", abs(value)
        while remainder:
            remainder, digit = divmod(remainder, target)
            digits = alphabet[digit]+digits
        result = ("-" if value < 0 else "")+(digits or "0")
        return outcome({"decimal": str(value), "representation": result, "base": target}, "positional base conversion", p)
    width = integer(p.get("width", 32), "width", 1, 1024)
    mask = (1 << width)-1
    a = integer(p["value"], "value", 0, mask)
    if action in {"and", "or", "xor"}:
        b = integer(p.get("other"), "other", 0, mask)
        value = a&b if action == "and" else a|b if action == "or" else a^b
    elif action == "not":
        value = (~a)&mask
    elif action in {"left_shift", "right_shift"}:
        shift = integer(p.get("shift"), "shift", 0, width)
        value = (a << shift)&mask if action == "left_shift" else a >> shift
    else:
        raise ValueError("неизвестная побитовая операция")
    return outcome({"decimal": str(value), "binary": format(value, f"0{width}b"), "hex": format(value, "X"), "width": width}, f"{action}, unsigned {width}-bit mask", p)
