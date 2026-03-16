from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


class CodeDependencyGraphService:
    """
    Stage 11: lightweight code dependency graph for the project.

    Safe:
    - reads python files
    - builds import graph
    - finds reverse dependencies
    - detects simple cycles
    - does NOT modify files
    """

    def __init__(self, project_root: str | None = None) -> None:
        base_dir = Path(__file__).resolve().parents[3]
        self.project_root = Path(project_root) if project_root else base_dir
        self.backend_root = self.project_root / "backend"
        self.app_root = self.backend_root / "app"

    def build_graph(self) -> dict[str, Any]:
        py_files = self._python_files()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        imports_by_file: dict[str, list[str]] = {}

        for file_path in py_files:
            rel = self._rel(file_path)
            module_name = self._module_name(file_path)
            imports = self._extract_imports(file_path)
            normalized = [self._normalize_import(x) for x in imports]
            normalized = [x for x in normalized if x]

            nodes.append(
                {
                    "path": rel,
                    "module": module_name,
                }
            )
            imports_by_file[rel] = normalized

            for dep in normalized:
                edges.append(
                    {
                        "from": rel,
                        "to": dep,
                    }
                )

        cycles = self._find_simple_cycles(imports_by_file)

        return {
            "ok": True,
            "type": "code_dependency_graph",
            "project_root": str(self.project_root),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "cycles": cycles,
        }

    def file_dependencies(self, path: str) -> dict[str, Any]:
        target = self.project_root / path
        if not target.exists():
            return {"ok": False, "path": path, "error": "File not found"}

        imports = self._extract_imports(target)
        normalized = [self._normalize_import(x) for x in imports]
        normalized = [x for x in normalized if x]

        return {
            "ok": True,
            "path": path,
            "module": self._module_name(target),
            "dependencies": normalized,
            "count": len(normalized),
        }

    def reverse_dependencies(self, module_or_path: str) -> dict[str, Any]:
        graph = self.build_graph()
        if not graph.get("ok"):
            return graph

        target_candidates = {module_or_path}
        if module_or_path.endswith(".py"):
            target_path = module_or_path
            try:
                target_module = self._module_name(self.project_root / module_or_path)
                target_candidates.add(target_module)
            except Exception:
                pass

        hits: list[str] = []
        for edge in graph.get("edges", []):
            dep = edge.get("to")
            if dep in target_candidates:
                hits.append(edge.get("from"))

        return {
            "ok": True,
            "target": module_or_path,
            "used_by": sorted(set(x for x in hits if x)),
            "count": len(set(x for x in hits if x)),
        }

    def hotspots(self, top_n: int = 10) -> dict[str, Any]:
        graph = self.build_graph()
        if not graph.get("ok"):
            return graph

        out_degree: dict[str, int] = {}
        in_degree: dict[str, int] = {}

        for node in graph.get("nodes", []):
            path = node.get("path")
            out_degree[path] = 0
            in_degree[path] = 0

        for edge in graph.get("edges", []):
            src = edge.get("from")
            dst = edge.get("to")
            if src:
                out_degree[src] = out_degree.get(src, 0) + 1

            if isinstance(dst, str):
                matched = self._match_module_to_path(dst, graph.get("nodes", []))
                if matched:
                    in_degree[matched] = in_degree.get(matched, 0) + 1

        most_connected = sorted(
            (
                {
                    "path": path,
                    "imports": out_degree.get(path, 0),
                    "imported_by": in_degree.get(path, 0),
                    "score": out_degree.get(path, 0) + in_degree.get(path, 0),
                }
                for path in out_degree.keys()
            ),
            key=lambda x: (-x["score"], -x["imported_by"], -x["imports"], x["path"]),
        )[:top_n]

        return {
            "ok": True,
            "type": "dependency_hotspots",
            "items": most_connected,
            "count": len(most_connected),
        }

    def _python_files(self) -> list[Path]:
        if not self.app_root.exists():
            return []
        return sorted(self.app_root.rglob("*.py"))

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.project_root).as_posix()

    def _module_name(self, path: Path) -> str:
        rel = path.relative_to(self.project_root).with_suffix("")
        return ".".join(rel.parts)

    def _extract_imports(self, path: Path) -> list[str]:
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return []

        try:
            tree = ast.parse(source)
        except Exception:
            return []

        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name:
                        found.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                level = getattr(node, "level", 0) or 0
                if level > 0:
                    base = self._resolve_relative_import(path, module, level)
                    if base:
                        found.append(base)
                elif module:
                    found.append(module)
        return list(dict.fromkeys(found))

    def _resolve_relative_import(self, file_path: Path, module: str, level: int) -> str:
        rel_parts = list(file_path.relative_to(self.project_root).with_suffix("").parts)
        if rel_parts:
            rel_parts.pop()  # current file

        for _ in range(max(level - 1, 0)):
            if rel_parts:
                rel_parts.pop()

        if module:
            rel_parts.extend(module.split("."))

        return ".".join(rel_parts)

    def _normalize_import(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        if value.startswith("backend.app."):
            return value
        if value.startswith("app."):
            return "backend." + value
        if value == "app":
            return "backend.app"
        return value

    def _match_module_to_path(self, module_name: str, nodes: list[dict[str, Any]]) -> str | None:
        for node in nodes:
            if node.get("module") == module_name:
                return node.get("path")
        return None

    def _find_simple_cycles(self, imports_by_file: dict[str, list[str]]) -> list[list[str]]:
        module_to_path = {}
        for path in imports_by_file.keys():
            try:
                module = self._module_name(self.project_root / path)
                module_to_path[module] = path
            except Exception:
                pass

        path_graph: dict[str, list[str]] = {}
        for path, deps in imports_by_file.items():
            resolved = []
            for dep in deps:
                target_path = module_to_path.get(dep)
                if target_path:
                    resolved.append(target_path)
            path_graph[path] = resolved

        cycles: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()

        def dfs(start: str, current: str, visited: list[str]) -> None:
            for nxt in path_graph.get(current, []):
                if nxt == start and len(visited) > 1:
                    cycle = visited + [start]
                    key = tuple(sorted(set(cycle)))
                    if key not in seen:
                        seen.add(key)
                        cycles.append(cycle)
                elif nxt not in visited and len(visited) < 6:
                    dfs(start, nxt, visited + [nxt])

        for node in path_graph.keys():
            dfs(node, node, [node])

        return cycles[:20]
