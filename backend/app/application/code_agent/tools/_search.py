from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.application.projects.scope import project_scope_id
from app.application.code_agent.tools._sandbox import _resolve_safe


_GREP_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", ".venv", "venv", ".agent", "target",
    "dist", "build", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".next", ".cache", "site-packages",
})
# Skip files larger than this — a multi-MB single file is almost always a
# bundle, lockfile, log or asset, not source the model meant to grep.
_GREP_MAX_FILE_BYTES = 2_000_000
# Hard ceiling on files actually opened, so even a pathological tree that slips
# past the dir-exclusions cannot turn grep into an unbounded scan.
_GREP_MAX_FILES = 5_000
_GREP_MAX_MATCHES = 200


def _grep_is_binary(path: Path) -> bool:
    """Cheap binary sniff: a NUL byte in the first 2 KB → treat as binary."""
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(2048)
    except Exception:
        return True  # unreadable → skip, don't let it stall the scan


def tool_grep(
    project_root: Path,
    *,
    pattern: str,
    path: str = ".",
    glob: str = "*",
) -> dict[str, Any]:
    base = _resolve_safe(project_root, path)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return {"text": f"ERROR: invalid regex: {exc}"}
    if base.is_file():
        files = [base]
    else:
        files = []
        for f in base.rglob(glob):
            if not f.is_file():
                continue
            if _GREP_EXCLUDE_DIRS.intersection(f.parts):
                continue
            files.append(f)
            if len(files) >= _GREP_MAX_FILES:
                break
    out: list[str] = []
    root = project_root.resolve()
    for f in files:
        try:
            if f.stat().st_size > _GREP_MAX_FILE_BYTES:
                continue
            if _grep_is_binary(f):
                continue
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if regex.search(line):
                        rel = str(f.relative_to(root)).replace("\\", "/")
                        out.append(f"{rel}:{lineno}:{line.rstrip()}")
                        if len(out) >= _GREP_MAX_MATCHES:
                            break
        except Exception:
            continue
        if len(out) >= _GREP_MAX_MATCHES:
            out.append(f"[... truncated at {_GREP_MAX_MATCHES} matches]")
            break
    return {"text": "\n".join(out) if out else f"No matches for '{pattern}' in {path}"}


# ─── project_map ─────────────────────────────────────────────────────────────
# A single-call structural overview of a codebase: pruned file tree + detected
# manifests / entry-points + light top-level signatures for the main languages.
# Dependency-free on purpose (stdlib ast + regex), so it never adds a package
# and never stalls on huge/binary trees. Inspired conceptually by smart_outline
# but is its own implementation — read-only, fail-soft, bounded.

# Reuse the same prune set the internal grep uses so the map and grep agree on
# what counts as "noise" (deps / build / VCS / caches).
_MAP_EXCLUDE_DIRS = _GREP_EXCLUDE_DIRS
# Manifest / config files whose presence pins down the stack at a glance.
_MAP_MANIFEST_FILES: frozenset[str] = frozenset({
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Pipfile", "poetry.lock", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "build.gradle.kts", "composer.json", "Gemfile", "tauri.conf.json",
    "tsconfig.json", "vite.config.ts", "vite.config.js", "next.config.js",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Makefile",
    "CMakeLists.txt", ".csproj", "main.go",
})
# Conventional entry-point file names worth surfacing explicitly.
_MAP_ENTRY_NAMES: frozenset[str] = frozenset({
    "main.py", "__main__.py", "app.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "index.tsx", "main.js", "main.ts", "main.tsx",
    "main.rs", "main.go", "server.py", "server.js", "server.ts", "cli.py",
})
# Source extensions we attempt to extract signatures from.
_MAP_SIG_EXTS: frozenset[str] = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go",
})
_MAP_SIG_MAX_FILE_BYTES = 400_000
_MAP_MAX_SIG_FILES = 60
_MAP_MAX_SIGS_PER_FILE = 25

# Regex fallbacks for non-Python languages (top-level-ish definitions). Kept
# deliberately loose — this is an overview, not a parser.
_MAP_SIG_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    ".js": (
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)"),
        re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z0-9_$]+)"),
        re.compile(r"^\s*(?:export\s+)?const\s+([A-Z][A-Za-z0-9_$]*)\s*="),
    ),
    ".rs": (
        re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)"),
        re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z0-9_]+)"),
    ),
    ".go": (
        re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z0-9_]+)"),
        re.compile(r"^\s*type\s+([A-Za-z0-9_]+)\s+(?:struct|interface)"),
    ),
}
# JS family shares the same patterns.
for _ext in (".jsx", ".ts", ".tsx"):
    _MAP_SIG_PATTERNS[_ext] = _MAP_SIG_PATTERNS[".js"]


def _map_py_signatures(text: str) -> list[str]:
    """Top-level def/class names via ast, with a regex fallback on syntax error."""
    sigs: list[str] = []
    try:
        import ast

        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                args = [a.arg for a in node.args.args]
                sigs.append(f"{prefix} {node.name}({', '.join(args)})")
            elif isinstance(node, ast.ClassDef):
                methods = [
                    m.name
                    for m in node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                tail = f" — {', '.join(methods[:8])}" if methods else ""
                sigs.append(f"class {node.name}{tail}")
            if len(sigs) >= _MAP_MAX_SIGS_PER_FILE:
                break
        return sigs
    except SyntaxError:
        pat_def = re.compile(r"^(?:async\s+)?def\s+([A-Za-z0-9_]+)\s*\(")
        pat_cls = re.compile(r"^class\s+([A-Za-z0-9_]+)")
        for line in text.splitlines():
            md = pat_def.match(line)
            if md:
                sigs.append(f"def {md.group(1)}")
            else:
                mc = pat_cls.match(line)
                if mc:
                    sigs.append(f"class {mc.group(1)}")
            if len(sigs) >= _MAP_MAX_SIGS_PER_FILE:
                break
        return sigs


def _map_regex_signatures(ext: str, text: str) -> list[str]:
    patterns = _MAP_SIG_PATTERNS.get(ext)
    if not patterns:
        return []
    sigs: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for pat in patterns:
            m = pat.match(line)
            if m:
                name = m.group(1)
                if name not in seen:
                    seen.add(name)
                    sigs.append(name)
                break
        if len(sigs) >= _MAP_MAX_SIGS_PER_FILE:
            break
    return sigs


def tool_project_map(
    project_root: Path,
    *,
    path: str | None = None,
    max_depth: int = 4,
    max_files: int = 400,
) -> dict[str, Any]:
    """One-call structural overview: file tree + manifests/entry-points +
    top-level signatures. Read-only, bounded, dependency-free.
    """
    base = _resolve_safe(project_root, path) if path else project_root.resolve()
    if not base.is_dir():
        return {"text": f"ERROR: not a directory: {path or '.'}"}
    root = project_root.resolve()
    try:
        max_depth = max(1, min(int(max_depth), 8))
        max_files = max(20, min(int(max_files), 2000))
    except (TypeError, ValueError):
        max_depth, max_files = 4, 400

    tree_lines: list[str] = []
    manifests: list[str] = []
    entry_points: list[str] = []
    sig_targets: list[Path] = []
    file_count = 0
    truncated = False

    def walk(directory: Path, depth: int) -> None:
        nonlocal file_count, truncated
        if depth > max_depth or truncated:
            return
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            return
        dirs = [e for e in entries if e.is_dir() and e.name not in _MAP_EXCLUDE_DIRS]
        files = [e for e in entries if e.is_file()]
        indent = "  " * (depth - 1)
        for d in dirs:
            tree_lines.append(f"{indent}{d.name}/")
            walk(d, depth + 1)
        for f in files:
            if file_count >= max_files:
                truncated = True
                return
            file_count += 1
            tree_lines.append(f"{indent}{f.name}")
            name = f.name
            rel = str(f.relative_to(root)).replace("\\", "/")
            if name in _MAP_MANIFEST_FILES or f.suffix == ".csproj":
                manifests.append(rel)
            if name in _MAP_ENTRY_NAMES:
                entry_points.append(rel)
            if (
                f.suffix in _MAP_SIG_EXTS
                and len(sig_targets) < _MAP_MAX_SIG_FILES
            ):
                sig_targets.append(f)

    walk(base, 1)

    # Signatures for a bounded set of source files (entry-points first).
    entry_set = set(entry_points)
    sig_targets.sort(
        key=lambda p: (
            str(p.relative_to(root)).replace("\\", "/") not in entry_set,
            str(p.relative_to(root)).lower(),
        )
    )
    sig_blocks: list[str] = []
    for f in sig_targets[:_MAP_MAX_SIG_FILES]:
        try:
            if f.stat().st_size > _MAP_SIG_MAX_FILE_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f.suffix == ".py":
            sigs = _map_py_signatures(text)
        else:
            sigs = _map_regex_signatures(f.suffix, text)
        if sigs:
            rel = str(f.relative_to(root)).replace("\\", "/")
            body = "\n".join(f"    {s}" for s in sigs[:_MAP_MAX_SIGS_PER_FILE])
            sig_blocks.append(f"{rel}:\n{body}")

    rel_base = str(base.relative_to(root)).replace("\\", "/") if base != root else "."
    sections: list[str] = [f"Project map for: {rel_base}"]
    if manifests:
        sections.append("Manifests / config:\n" + "\n".join(f"  {m}" for m in manifests))
    if entry_points:
        sections.append("Entry points:\n" + "\n".join(f"  {e}" for e in entry_points))
    tree_body = "\n".join(tree_lines) if tree_lines else "  (empty)"
    if truncated:
        tree_body += f"\n  [... tree truncated at {max_files} files]"
    sections.append(f"File tree (depth {max_depth}):\n{tree_body}")
    if sig_blocks:
        sections.append("Signatures (top-level):\n\n" + "\n\n".join(sig_blocks))
    return {"text": "\n\n".join(sections)}


def tool_recall(
    project_root: Path,
    *,
    query: str,
    top_k: int = 5,
    min_score: float = 0.3,
) -> dict[str, Any]:
    """Recall from memory through the unified MemoryService facade — returns
    BOTH curated facts (what we know about the user: name, preferences,
    working details) AND semantic/episodic matches (relevant code chunks,
    prior-turn summaries, chat reflection episodes).

    Facts are user-level so they surface regardless of project. Semantic
    results are restricted to this project plus global entries (project='')
    so cross-project leakage is prevented.
    """
    from app.application import memory as mem

    limit = max(1, int(top_k))
    sections: list[str] = []

    # 1) Curated facts (lexical, smart_memory).
    try:
        fact_items = mem.search_facts(query, limit=limit).get("items", []) or []
    except Exception:
        fact_items = []
    if fact_items:
        flines = [f"Known facts ({len(fact_items)}):"]
        for item in fact_items:
            text = (item.get("text") or "").strip()
            if len(text) > 300:
                text = text[:300] + " [...]"
            flines.append(f"- [{item.get('category', 'fact')}] {text}")
        sections.append("\n".join(flines))

    # 2) Semantic / episodic (vector, rag_memory) — project-scoped + global.
    scope_id = project_scope_id(project_root)
    try:
        result = mem.search_semantic(query, limit=limit, min_score=float(min_score), project=scope_id)
    except Exception as exc:
        result = {"ok": False, "error": f"RAG service unavailable: {exc}"}
    if not result.get("ok"):
        if sections:  # facts are still useful even if the semantic side failed
            return {"text": "\n\n".join(sections)}
        return {"text": f"ERROR: {result.get('error', 'recall failed')}"}

    sem_items = result.get("items", []) or []
    if sem_items:
        slines = [f"Found {len(sem_items)} relevant items:"]
        for i, item in enumerate(sem_items, 1):
            score = item.get("score", 0.0)
            category = item.get("category", "fact")
            text = (item.get("text") or "").strip()
            if len(text) > 600:
                text = text[:600] + " [...]"
            slines.append(f"\n[{i}] score={score:.2f}  category={category}\n{text}")
        sections.append("\n".join(slines))

    if not sections:
        return {"text": f"No matches for '{query}' (min_score={min_score})"}
    return {"text": "\n\n".join(sections)}
