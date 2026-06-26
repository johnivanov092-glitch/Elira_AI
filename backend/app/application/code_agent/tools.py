"""Code-agent tools — sandboxed file & shell operations exposed to the LLM
via OpenAI-compatible function-calling.

Every tool receives a `project_root` (resolved Path) and refuses to touch any
path that escapes it. This is the minimal viable tool set for a Claude
Code / Codex-class local agent:
    read_file, write_file, edit_file, glob, grep, run_bash

Each tool returns a structured dict with at minimum:
    {"text": <human-readable summary, fed back to the LLM as tool result>}
Some tools add extra fields the frontend uses to render diffs and
auto-open files (touched_path, old_content, new_content).
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.application.projects.scope import project_scope_id
# Static tool schemas extracted to .tool_schemas; re-exported so existing
# importers (tool_providers.builtin, tests) keep importing build_tool_schemas
# from tools unchanged.
from app.application.code_agent.tool_schemas import build_tool_schemas  # noqa: F401


_SHELL_TIMEOUT_MAX = 120
_SHELL_STDOUT_LIMIT = 16000
_SHELL_STDERR_LIMIT = 6000
_BLOCKED_SHELL_FRAGMENTS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "format c:",
    "shutdown",
    "reboot",
    ":(){:|:&};:",
    "deltree",
    "remove-item -recurse",
    "del /s",
    "rd /s",
    "rmdir /s",
    "git reset --hard",
    "git clean -fd",
    "git checkout --",
)


# Read-only command prefixes that auto-execute without user approval.
# A command is safe if it starts with one of these prefixes (case-insensitive).
# Anything not in this list and not in _BLOCKED_SHELL_FRAGMENTS requires approval.
_SHELL_READONLY_PREFIXES: tuple[str, ...] = (
    # VCS read-only
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "git stash list", "git tag", "git fetch --dry-run",
    "git ls-files", "git describe", "git rev-parse",
    # File listing / reading
    "ls", "dir", "find ", "tree",
    "cat ", "head ", "tail ", "less ", "more ", "type ",
    "wc ", "file ",
    # Search
    "grep ", "egrep ", "fgrep ", "rg ", "ag ",
    # System info / status
    "echo ", "pwd", "whoami", "id", "hostname",
    "which ", "where ", "command -v",
    "env", "printenv", "set",
    "ps ", "ps aux", "top -bn1",
    "df ", "du -sh", "free ",
    # Python / package status
    "python --version", "python3 --version", "python -V", "python3 -V",
    "pip list", "pip show ", "pip freeze", "pip check",
    "uv list", "poetry show",
    # Testing — collect only
    "pytest --collect-only", "pytest -v --collect-only",
    "jest --listTests", "cargo test -- --list",
    # Node / npm status
    "node --version", "npm list", "yarn list", "pnpm list",
    "npm outdated", "npm audit",
    # Docker status
    "docker ps", "docker images", "docker stats", "docker info",
    "docker compose ps", "docker-compose ps",
    # Rust / Go / etc.
    "cargo --version", "rustc --version", "go version",
    # Network / DNS read-only
    "nslookup ", "dig ", "host ", "ping ",
)


# ─── Cancellable shell processes ────────────────────────────────────────────
#
# The Stop button used to do nothing while a shell tool was running: the
# executor runs each tool in a daemon worker thread and blocks on it, so a
# `threading.Event` cancel flag set by the HTTP /cancel route is never *read*
# until the blocking subprocess returns (up to the shell timeout). A Python
# event cannot interrupt a foreign synchronous call.
#
# Fix: tool_run_bash launches the shell via Popen (not subprocess.run) and
# registers the live process against the current run_id. request_cancel can
# then reach in and proc.kill() the actual OS process, so Stop aborts a hung
# command in a fraction of a second instead of waiting out the timeout.
#
# The run_id reaches the tool via a ContextVar set by the executor's worker
# thread (same thread that calls the tool synchronously — no cross-thread
# propagation needed). When unset (e.g. direct unit calls) the tool still runs,
# just without cancel registration.
_CURRENT_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "code_agent_current_run_id", default=None
)

# run_id -> set of live Popen objects spawned by that run.
_LIVE_SHELL_PROCS: dict[str, set[subprocess.Popen]] = {}
_LIVE_SHELL_LOCK = threading.Lock()
# run_ids whose processes were explicitly killed by the Stop path. The tool
# checks this to report "Прервано пользователем" regardless of the OS exit code
# (taskkill on Windows yields a positive code, so we can't infer Stop from it).
_KILLED_RUN_IDS: set[str] = set()

_IS_WINDOWS = sys.platform.startswith("win")


def _new_process_group_kwargs() -> dict[str, Any]:
    """Popen kwargs that put the child in its own killable process group.

    On Windows a ``shell=True`` launch wraps the command in ``cmd.exe /c``;
    ``proc.kill()`` would only kill cmd.exe and orphan the real child. Giving
    the child its own process group lets ``taskkill /T`` (resp. ``killpg`` on
    POSIX) take down the whole tree — which is what makes Stop actually work.
    """
    if _IS_WINDOWS:
        # CREATE_NEW_PROCESS_GROUP so the cmd.exe + its children form a group.
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """Kill *proc* and every descendant it spawned.

    Bare ``proc.kill()`` is not enough on Windows: a ``shell=True`` command runs
    under ``cmd.exe``, and killing cmd.exe orphans the real worker (python.exe,
    node.exe, a dev server, …) which keeps running and holding the run alive.
    """
    if proc.poll() is not None:
        return
    pid = proc.pid
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return
        except Exception:
            # taskkill missing/failed — fall back to the single-process kill.
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), 9)  # SIGKILL the whole group
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def set_current_run_id(run_id: str | None) -> contextvars.Token:
    """Bind run_id to the calling thread so shell tools can register their
    process for cancellation. Returns the token for later reset()."""
    return _CURRENT_RUN_ID.set(run_id)


def reset_current_run_id(token: contextvars.Token) -> None:
    try:
        _CURRENT_RUN_ID.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. set on another thread) — ignore.
        pass


def _register_shell_proc(run_id: str | None, proc: subprocess.Popen) -> None:
    if not run_id:
        return
    with _LIVE_SHELL_LOCK:
        _LIVE_SHELL_PROCS.setdefault(run_id, set()).add(proc)


def _unregister_shell_proc(run_id: str | None, proc: subprocess.Popen) -> None:
    if not run_id:
        return
    with _LIVE_SHELL_LOCK:
        procs = _LIVE_SHELL_PROCS.get(run_id)
        if procs is not None:
            procs.discard(proc)
            if not procs:
                _LIVE_SHELL_PROCS.pop(run_id, None)


def kill_run_processes(run_id: str) -> int:
    """Kill every live shell process spawned by `run_id`. Called by the agent
    loop's cancel path so Stop aborts a hung command immediately. Returns the
    number of processes signalled."""
    with _LIVE_SHELL_LOCK:
        procs = list(_LIVE_SHELL_PROCS.get(run_id, ()))
        _KILLED_RUN_IDS.add(run_id)
    killed = 0
    for proc in procs:
        try:
            if proc.poll() is None:
                _kill_proc_tree(proc)
                killed += 1
        except Exception:
            # Process may have already exited between poll and kill.
            pass
    return killed


def is_shell_safe(command: str) -> bool:
    """Return True if *command* is in the read-only shell allowlist.

    Commands in this set auto-execute without user approval. Matching is
    prefix-based and case-insensitive so ``git status --short`` passes as
    a ``git status`` prefix.

    Prefixes that end with a space (e.g. ``"cat "``) match any command that
    starts with that string (``cat README.md``). Prefixes without a trailing
    space (e.g. ``"git status"``) are matched as exact or word-boundary
    (``git status``, ``git status --short``).
    """
    cmd = (command or "").strip().lower()
    if not cmd:
        return False
    # Reject any command containing shell composition or redirection metacharacters.
    # These could chain an unsafe subcommand past the prefix check.
    _UNSAFE_METACHAR = ("&&", "||", ";;", "|", ";", ">", "<", "`", "$(", "\n", "\r")
    if any(meta in cmd for meta in _UNSAFE_METACHAR):
        return False
    for prefix in _SHELL_READONLY_PREFIXES:
        p = prefix.lower()
        if p.endswith(" "):
            if cmd.startswith(p) or cmd == p.rstrip():
                return True
        else:
            if cmd == p or cmd.startswith(p + " ") or cmd.startswith(p + "\t"):
                return True
    return False


class SandboxError(Exception):
    """Raised when a tool tries to access a path outside the project root."""


def _resolve_safe(project_root: Path, raw_path: str) -> Path:
    """Resolve `raw_path` (absolute or relative to project_root) and confirm
    it stays inside project_root. Raises SandboxError otherwise.
    """
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    root_resolved = project_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SandboxError(
            f"Path '{raw_path}' resolves to {resolved}, which is outside the "
            f"project root {root_resolved}"
        ) from exc
    return resolved


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    budget = max(400, limit - 100)
    head_size = int(budget * 0.65)
    tail_size = budget - head_size
    removed = len(text) - head_size - tail_size
    return (
        text[:head_size]
        + f"\n[... truncated {removed} chars from middle ...]\n"
        + text[-tail_size:]
    )


def _blocked_shell_fragment(command: str) -> str | None:
    lowered = (command or "").strip().lower()
    return next((fragment for fragment in _BLOCKED_SHELL_FRAGMENTS if fragment in lowered), None)


# ─── tool implementations ────────────────────────────────────────────────────


def tool_read_file(
    project_root: Path,
    *,
    path: str,
    offset: int = 0,
    limit: int = 2000,
) -> dict[str, Any]:
    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}
    start = max(0, int(offset))
    end = start + max(1, int(limit))
    selected = lines[start:end]
    numbered = "".join(f"{i + 1 + start:>5}\t{ln}" for i, ln in enumerate(selected))
    suffix = "" if end >= len(lines) else f"\n[... truncated at line {end} of {len(lines)}]"
    return {
        "text": numbered + suffix,
        "touched_path": path,
    }


def tool_write_file(project_root: Path, *, path: str, content: str) -> dict[str, Any]:
    target = _resolve_safe(project_root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    old_content = ""
    if existed:
        try:
            old_content = target.read_text(encoding="utf-8")
        except Exception:
            old_content = ""
    target.write_text(content, encoding="utf-8")
    action = "Overwrote" if existed else "Created"
    return {
        "text": f"{action} {path} ({len(content)} chars)",
        "touched_path": path,
        "old_content": old_content,
        "new_content": content,
        "diff_action": "overwrite" if existed else "create",
    }


def tool_edit_file(
    project_root: Path,
    *,
    path: str,
    old_string: str,
    new_string: str,
) -> dict[str, Any]:
    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}
    current = target.read_text(encoding="utf-8")
    if old_string not in current:
        return {"text": f"ERROR: old_string not found in {path}"}
    occurrences = current.count(old_string)
    if occurrences > 1:
        return {
            "text": (
                f"ERROR: old_string matches {occurrences} times in {path}. "
                "Provide a larger surrounding context to make it unique."
            )
        }
    updated = current.replace(old_string, new_string, 1)
    target.write_text(updated, encoding="utf-8")
    return {
        "text": f"Edited {path} (1 replacement)",
        "touched_path": path,
        "old_content": current,
        "new_content": updated,
        "diff_action": "edit",
    }


def tool_glob(project_root: Path, *, pattern: str) -> dict[str, Any]:
    root = project_root.resolve()
    matches: list[str] = []
    for raw_match in root.glob(pattern):
        try:
            matches.append(str(raw_match.relative_to(root)).replace("\\", "/"))
        except ValueError:
            continue
    matches.sort()
    if not matches:
        return {"text": f"No files match '{pattern}'"}
    return {"text": "\n".join(matches[:200])}


# Directories the internal grep never descends into. These are dependency,
# build, VCS and agent-runtime trees: scanning them is never what the model
# wants and they hold the huge/binary files that previously made grep hang for
# minutes while holding the global write-lock. Matched against path parts so an
# excluded dir at any depth prunes the whole subtree.
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
    """Semantic search over RAG memory. Returns top matching items —
    relevant code chunks (if the project was indexed) and summaries of
    prior agent turns.

    Scope: results are restricted to entries tagged with this project
    name, plus global entries (project='') so user-level facts still
    surface. Cross-project leakage is prevented.
    """
    try:
        from app.application.rag_memory.service import search_rag
    except Exception as exc:
        return {"text": f"ERROR: RAG service unavailable: {exc}"}

    scope_id = project_scope_id(project_root)
    result = search_rag(
        query=query,
        limit=max(1, int(top_k)),
        min_score=float(min_score),
        project=scope_id,
    )
    if not result.get("ok"):
        return {"text": f"ERROR: {result.get('error', 'recall failed')}"}
    items = result.get("items", []) or []
    if not items:
        return {"text": f"No matches for '{query}' (min_score={min_score})"}
    lines = [f"Found {len(items)} relevant items:"]
    for i, item in enumerate(items, 1):
        score = item.get("score", 0.0)
        category = item.get("category", "fact")
        text = (item.get("text") or "").strip()
        if len(text) > 600:
            text = text[:600] + " [...]"
        lines.append(f"\n[{i}] score={score:.2f}  category={category}\n{text}")
    return {"text": "\n".join(lines)}


def tool_web_search(*, query: str, top_k: int = 5) -> dict[str, Any]:
    """Search the web via the configured engines (Tavily / DuckDuckGo /
    Wikipedia). Returns ranked results with title + URL + snippet. Use
    `web_fetch` after this to read the full content of a specific result.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {"text": "ERROR: query is empty"}
    try:
        from app.infrastructure.search.web_search import search_web
    except Exception as exc:  # pragma: no cover - import path
        return {"text": f"ERROR: web search unavailable: {exc}"}

    limit = max(1, min(int(top_k), 10))
    result = search_web(cleaned, max_results=limit)
    sources = result.get("sources") or []
    if not sources:
        return {"text": f"No web results for '{cleaned}'"}

    engines = ", ".join(result.get("engines_used") or []) or "?"
    lines = [f"Found {len(sources)} results via {engines}:"]
    for i, item in enumerate(sources[:limit], 1):
        title = (item.get("title") or "").strip() or "(no title)"
        url = (item.get("url") or "").strip()
        snippet = (item.get("snippet") or item.get("content") or "").strip()
        if len(snippet) > 350:
            snippet = snippet[:350] + " […]"
        lines.append(f"\n[{i}] {title}\n    {url}\n    {snippet}" if snippet else f"\n[{i}] {title}\n    {url}")
    return {"text": "\n".join(lines)}


def tool_sandbox_run(
    project_root: Path,
    *,
    code: str,
    install: list[str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute Python code in an isolated per-project venv.

    Persistent: pip installs and any files the script writes to
    `./work/` survive between calls within the same project.
    Reset with `sandbox_reset`.
    """
    if not isinstance(code, str) or not code.strip():
        return {"text": "ERROR: code is empty"}
    from app.application.code_agent.sandbox import run_in_sandbox

    result = run_in_sandbox(
        project_root,
        code=code,
        install=install,
        timeout=int(timeout),
    )

    parts: list[str] = []
    parts.append(f"[sandbox: {result['sandbox_path']}]")
    parts.append(f"exit={result['exit_code']}  took={result['took_seconds']}s")
    if result.get("error"):
        parts.append(f"ERROR: {result['error']}")
    if result.get("install_log"):
        parts.append(f"PIP:\n{result['install_log']}")
    if result.get("stdout"):
        parts.append(f"STDOUT:\n{result['stdout']}")
    if result.get("stderr"):
        parts.append(f"STDERR:\n{result['stderr']}")
    return {"text": "\n".join(parts)}


def tool_sandbox_reset(project_root: Path) -> dict[str, Any]:
    """Wipe the project's sandbox (venv + work dir). Next sandbox_run
    starts fresh."""
    from app.application.code_agent.sandbox import reset_sandbox

    result = reset_sandbox(project_root)
    if not result.get("ok"):
        return {"text": f"ERROR: {result.get('error', 'reset failed')}"}
    if not result.get("existed"):
        return {"text": "Sandbox did not exist (nothing to reset)."}
    return {"text": f"Sandbox reset: {result['sandbox_path']}"}


def tool_web_fetch(*, url: str, max_chars: int = 8000) -> dict[str, Any]:
    """Fetch a single web page and extract its main readable text.

    HTML noise (nav, footer, ads, scripts) is stripped via the project's
    existing BeautifulSoup-based extractor. Use this AFTER `web_search`
    has surfaced URLs worth reading in full.
    """
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return {"text": "ERROR: url is empty"}
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        return {"text": f"ERROR: url must start with http:// or https:// — got '{cleaned_url[:80]}'"}

    from app.application.web.ssrf_guard import check_ssrf
    ssrf_reason = check_ssrf(cleaned_url)
    if ssrf_reason:
        return {"text": f"ERROR: SSRF blocked — {ssrf_reason}"}

    try:
        from app.infrastructure.search.web_search import fetch_page_text
    except Exception as exc:  # pragma: no cover
        return {"text": f"ERROR: web fetch unavailable: {exc}"}

    limit = max(500, min(int(max_chars), 50000))
    try:
        body = fetch_page_text(cleaned_url, max_chars=limit)
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}

    body = (body or "").strip()
    if not body:
        return {"text": f"ERROR: empty or non-HTML response from {cleaned_url}"}
    return {"text": f"[fetched: {cleaned_url}]\n\n{body}"}


def _browser_render(url: str, wait_selector: str | None, limit: int) -> tuple[str, str, str]:
    """Render a page with Playwright. Runs in a worker thread (see tool_browser):
    the Playwright sync API must not be called from inside an asyncio loop."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            return page.title(), page.url, (page.inner_text("body") or "")[:limit]
        finally:
            browser.close()


def tool_browser(*, url: str, wait_selector: str | None = None, max_chars: int = 8000) -> dict[str, Any]:
    """Open a URL in a real headless browser (Playwright/Chromium), render
    JavaScript, and return the visible page text. Use when `web_fetch` is not
    enough — pages that need JS to render, SPAs, or to verify how a page
    actually looks/behaves.
    """
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return {"text": "ERROR: url is empty"}
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        return {"text": f"ERROR: url must start with http:// or https:// — got '{cleaned_url[:80]}'"}

    from app.application.web.ssrf_guard import check_ssrf
    reason = check_ssrf(cleaned_url)
    if reason:
        return {"text": f"ERROR: SSRF blocked — {reason}"}

    limit = max(500, min(int(max_chars), 50000))
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            title, final_url, text = ex.submit(_browser_render, cleaned_url, wait_selector, limit).result(timeout=50)
    except Exception as exc:
        return {"text": f"ERROR: browser failed: {str(exc)[:300]}"}

    text = (text or "").strip()
    if not text:
        return {"text": f"[browser: {final_url}] страница отрендерилась, но видимого текста нет"}
    return {"text": f"[browser: {final_url}]\nTITLE: {title}\n\n{text}"}


def tool_run_bash(project_root: Path, *, command: str, timeout: int = 60) -> dict[str, Any]:
    cleaned_command = (command or "").strip()
    if not cleaned_command:
        return {"text": "ERROR: command is empty"}
    blocked = _blocked_shell_fragment(cleaned_command)
    if blocked:
        return {"text": f"ERROR: blocked dangerous shell command fragment: {blocked}"}
    safe_timeout = max(1, min(int(timeout), _SHELL_TIMEOUT_MAX))

    run_id = _CURRENT_RUN_ID.get()
    try:
        # Popen (not subprocess.run) so the live process is registered and can
        # be killed mid-flight by the Stop button. We drive the wait ourselves
        # via communicate() with a deadline, killing on timeout OR cancel.
        proc = subprocess.Popen(
            cleaned_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(project_root.resolve()),
            # Close stdin: a shell tool must never block on input. Interactive
            # prompts (ssh host-key/password, apt, etc.) get EOF and fail fast
            # instead of hanging until the timeout. For real SSH use the ssh tool.
            stdin=subprocess.DEVNULL,
            # Own process group so Stop/timeout can kill the whole tree, not just
            # the cmd.exe wrapper (which would orphan the real child on Windows).
            **_new_process_group_kwargs(),
        )
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}

    _register_shell_proc(run_id, proc)
    cancelled = False
    timed_out = False
    # Drain stdout/stderr in background threads so a chatty command can't fill
    # the OS pipe buffer and deadlock (child blocks on write → never exits →
    # poll() never completes). The main loop then only watches poll()/deadline,
    # which keeps the process killable mid-flight by the Stop button.
    out_buf: list[str] = []
    err_buf: list[str] = []

    def _drain(stream, sink: list[str]) -> None:
        try:
            for chunk in iter(lambda: stream.read(8192), ""):
                if not chunk:
                    break
                sink.append(chunk)
        except Exception:
            pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()
    try:
        deadline = time.monotonic() + safe_timeout
        # Poll so a Stop press (which proc.kill()s us from another thread) is
        # observed within ~0.1s instead of waiting out the whole timeout.
        while True:
            if proc.poll() is not None:
                break
            if time.monotonic() >= deadline:
                _kill_proc_tree(proc)
                timed_out = True
                break
            time.sleep(0.1)
    except Exception as exc:
        try:
            _kill_proc_tree(proc)
        except Exception:
            pass
        return {"text": f"ERROR: {exc}"}
    finally:
        # Let the readers finish flushing whatever the process wrote/buffered.
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        try:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
        _unregister_shell_proc(run_id, proc)
        # Stop is detected via the explicit kill registry (taskkill on Windows
        # yields a *positive* exit code, so the returncode alone can't tell a
        # Stop from a normal failure). A timeout also kills the proc, but that
        # is a tool error, not a Stop. Fall back to a negative code for the
        # POSIX direct-kill case where no run_id was bound.
        if not timed_out:
            if run_id:
                with _LIVE_SHELL_LOCK:
                    if run_id in _KILLED_RUN_IDS:
                        _KILLED_RUN_IDS.discard(run_id)
                        cancelled = True
            if not cancelled and proc.returncode is not None and proc.returncode < 0:
                cancelled = True

    out, err = "".join(out_buf), "".join(err_buf)

    if timed_out:
        tail = f"\n{_truncate_middle(err.rstrip(), _SHELL_STDERR_LIMIT)}" if err else ""
        return {"text": f"ERROR: command timed out after {safe_timeout}s{tail}"}

    if cancelled:
        return {"text": f"$ {cleaned_command}\nПрервано пользователем (Стоп)."}

    stdout, stderr = out or "", err or ""
    parts = [f"$ {cleaned_command}", f"exit={proc.returncode}"]
    if stdout:
        parts.append(f"STDOUT:\n{_truncate_middle(stdout.rstrip(), _SHELL_STDOUT_LIMIT)}")
    if stderr:
        parts.append(f"STDERR:\n{_truncate_middle(stderr.rstrip(), _SHELL_STDERR_LIMIT)}")
    return {"text": "\n".join(parts)}


# ─── run_server: background process launcher ────────────────────────────────
# Unlike run_bash (which blocks until the command exits or times out), run_server
# starts a long-lived process via Popen and returns IMMEDIATELY. The process is
# tracked in a module-level registry keyed by pid so it can be listed and stopped
# explicitly later. These servers deliberately OUTLIVE the agent run that started
# them, so they are NOT registered in _LIVE_SHELL_PROCS and are NOT killed by the
# Stop button — only by `stop`/`stop_all`. Output is captured to log files under
# the project's .elira/servers/ so the model can inspect startup without blocking.

_SERVER_LOG_DIRNAME = ".elira/servers"
_SERVER_STARTUP_GRACE = 1.5  # seconds to let the process crash-or-bind before reporting
_SERVER_LOG_TAIL_CHARS = 4000


class _ServerHandle:
    __slots__ = ("pid", "command", "proc", "log_path", "port", "started_at")

    def __init__(self, pid: int, command: str, proc: subprocess.Popen,
                 log_path: Path, port: int | None) -> None:
        self.pid = pid
        self.command = command
        self.proc = proc
        self.log_path = log_path
        self.port = port
        self.started_at = time.time()


_LIVE_SERVERS: dict[int, _ServerHandle] = {}
_SERVERS_LOCK = threading.Lock()


def _reap_dead_servers() -> None:
    """Drop handles whose process has exited so `list` stays honest."""
    with _SERVERS_LOCK:
        dead = [pid for pid, h in _LIVE_SERVERS.items() if h.proc.poll() is not None]
        for pid in dead:
            _LIVE_SERVERS.pop(pid, None)


def _read_log_tail(log_path: Path, limit: int = _SERVER_LOG_TAIL_CHARS) -> str:
    try:
        data = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return _truncate_middle(data.rstrip(), limit)


def stop_all_servers() -> int:
    """Kill every tracked background server. Returns the count signalled.
    Intended for process/app shutdown, not the per-run Stop button."""
    with _SERVERS_LOCK:
        handles = list(_LIVE_SERVERS.values())
    killed = 0
    for h in handles:
        try:
            if h.proc.poll() is None:
                _kill_proc_tree(h.proc)
                killed += 1
        except Exception:
            pass
    with _SERVERS_LOCK:
        _LIVE_SERVERS.clear()
    return killed


def tool_run_server(
    project_root: Path,
    *,
    action: str = "start",
    command: str = "",
    port: int | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Manage long-lived background processes (dev servers, watchers).

    action:
        start  — launch `command` in the background, return immediately (pid + log).
        list   — show every running server this agent started (pid, port, command).
        logs   — tail the captured output of the server with the given `pid`.
        stop   — terminate the server with the given `pid`.
        stop_all — terminate every tracked server.
    """
    act = (action or "start").strip().lower()
    _reap_dead_servers()

    if act == "list":
        with _SERVERS_LOCK:
            handles = list(_LIVE_SERVERS.values())
        if not handles:
            return {"text": "No background servers are running."}
        lines = ["Running background servers:"]
        for h in sorted(handles, key=lambda x: x.started_at):
            age = int(time.time() - h.started_at)
            port_s = f" port={h.port}" if h.port else ""
            lines.append(f"  pid={h.pid}{port_s} age={age}s — {h.command}")
        return {"text": "\n".join(lines)}

    if act == "stop_all":
        n = stop_all_servers()
        return {"text": f"Stopped {n} background server(s)."}

    if act == "logs":
        if pid is None:
            return {"text": "ERROR: action 'logs' requires a pid."}
        with _SERVERS_LOCK:
            h = _LIVE_SERVERS.get(int(pid))
        if h is None:
            return {"text": f"ERROR: no tracked server with pid={pid}."}
        tail = _read_log_tail(h.log_path)
        status = "running" if h.proc.poll() is None else f"exited (code={h.proc.returncode})"
        body = tail or "(no output captured yet)"
        return {"text": f"server pid={pid} [{status}]\n$ {h.command}\n\n{body}"}

    if act == "stop":
        if pid is None:
            return {"text": "ERROR: action 'stop' requires a pid."}
        with _SERVERS_LOCK:
            h = _LIVE_SERVERS.pop(int(pid), None)
        if h is None:
            return {"text": f"ERROR: no tracked server with pid={pid}."}
        try:
            if h.proc.poll() is None:
                _kill_proc_tree(h.proc)
                try:
                    h.proc.wait(timeout=5)
                except Exception:
                    pass
            return {"text": f"Stopped server pid={pid} — {h.command}"}
        except Exception as exc:
            return {"text": f"ERROR stopping pid={pid}: {exc}"}

    if act != "start":
        return {"text": f"ERROR: unknown action '{action}'. Use start|list|logs|stop|stop_all."}

    cleaned_command = (command or "").strip()
    if not cleaned_command:
        return {"text": "ERROR: action 'start' requires a command."}
    blocked = _blocked_shell_fragment(cleaned_command)
    if blocked:
        return {"text": f"ERROR: blocked dangerous shell command fragment: {blocked}"}

    log_dir = (project_root.resolve() / _SERVER_LOG_DIRNAME)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"text": f"ERROR: cannot create server log dir: {exc}"}
    log_path = log_dir / f"server-{int(time.time() * 1000)}.log"

    try:
        log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"text": f"ERROR: cannot open log file: {exc}"}

    try:
        proc = subprocess.Popen(
            cleaned_command,
            shell=True,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(project_root.resolve()),
            # Background servers never read stdin; close it so they don't block.
            stdin=subprocess.DEVNULL,
            # Own process group so `stop` kills the whole server tree, not just
            # the cmd.exe wrapper (which would orphan the real server process).
            **_new_process_group_kwargs(),
        )
    except Exception as exc:
        try:
            log_fh.close()
        except Exception:
            pass
        return {"text": f"ERROR: {exc}"}

    handle = _ServerHandle(proc.pid, cleaned_command, proc, log_path, port)
    with _SERVERS_LOCK:
        _LIVE_SERVERS[proc.pid] = handle

    # Give it a moment to either bind its port or crash, so we can report
    # something useful instead of a bare "started" for a command that died.
    time.sleep(_SERVER_STARTUP_GRACE)
    if proc.poll() is not None:
        with _SERVERS_LOCK:
            _LIVE_SERVERS.pop(proc.pid, None)
        try:
            log_fh.close()
        except Exception:
            pass
        tail = _read_log_tail(log_path)
        body = f"\n{tail}" if tail else ""
        return {"text": (
            f"ERROR: server exited immediately (code={proc.returncode}).\n"
            f"$ {cleaned_command}{body}"
        )}

    port_s = f" on port {port}" if port else ""
    return {"text": (
        f"Server started in background{port_s}.\n"
        f"  pid={proc.pid}\n"
        f"  $ {cleaned_command}\n"
        f"Use run_server(action='logs', pid={proc.pid}) to read output, "
        f"run_server(action='stop', pid={proc.pid}) to stop it. "
        f"It keeps running across turns and is NOT killed by Stop."
    )}


# ─── P10.1: deferred tool search meta-tool (foundation) ─────────────────────

TOOL_SEARCH_RESULT_LIMIT = 20
TOOL_SEARCH_ACTIVATION_CAP = 5
DELEGATE_TASK_MAX_STEPS = 6
DELEGATE_TASK_MAX_CTX = 8192
DELEGATE_TASK_TIMEOUT_SECONDS = 60
DELEGATE_TASK_READONLY_TOOLS = ("read_file", "glob", "grep", "recall")
DELEGATE_TASK_ROLES = {"explore", "plan", "verify"}


def _clamp_to_max(value: Any, maximum: int) -> int:
    """Coerce a caller-controlled int and clamp to [0, maximum]; non-int / bad
    values fall back to `maximum`. Guarantees the model can never exceed the cap."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = maximum
    return min(max(0, n), maximum)


def _record_tool_search_metrics(
    run_id: str, query: str, match_count: int, activated: list[str], agent_id: str
) -> None:
    """Best-effort run metrics for tool.search / tool.activated (no schema change)."""
    try:
        from app.application.monitoring.runtime import record_metric

        record_metric(
            metric_type="tool.search",
            agent_id=agent_id,
            run_id=run_id,
            ok=True,
            details={
                "query": str(query),
                "match_count": int(match_count),
                "activated_count": len(activated),
            },
        )
        if activated:
            record_metric(
                metric_type="tool.activated",
                agent_id=agent_id,
                run_id=run_id,
                ok=True,
                details={"tools": list(activated), "query": str(query)},
            )
    except Exception:
        pass


def tool_search(
    *,
    run_id: str,
    query: str,
    agent_id: str = "code-agent",
    limit: int = TOOL_SEARCH_RESULT_LIMIT,
    activation_cap: int = TOOL_SEARCH_ACTIVATION_CAP,
) -> dict[str, Any]:
    """Read-only meta-tool (P10.1 foundation): search the ToolSpec registry and
    activate eligible, non-side-effect tools for THIS run only.

    - Requires a run_id (activation is run-scoped).
    - Activation grants VISIBILITY only — the unified executor still enforces
      policy / scope / approval at dispatch. This function executes nothing.
    - Disabled / unclassified / forbidden tools are surfaced but never activated.
    - Side-effect tools are surfaced but NOT auto-activated in this slice.
    - Activates at most ``activation_cap`` tools per call.
    - Uses the existing run-scoped deferred_tools store; ``activate_tools`` is a
      no-op unless the run already opted into deferred mode, so a non-deferred
      run is unchanged. No hidden global state.
    """
    rid = str(run_id or "").strip()
    if not rid:
        return {
            "ok": False,
            "text": "tool_search requires a run_id.",
            "error": "run_id_required",
            "matches": [],
            "activated": [],
        }

    from app.application.tool_registry.runtime import search_tool_specs
    from app.application.agent_kernel.deferred_tools import activate_tools

    safe_limit = _clamp_to_max(limit, TOOL_SEARCH_RESULT_LIMIT)
    matches = search_tool_specs(query, limit=safe_limit)

    cap = _clamp_to_max(activation_cap, TOOL_SEARCH_ACTIVATION_CAP)
    eligible: list[str] = []
    for match in matches:
        if len(eligible) >= cap:
            break
        if match["activatable"] and not match["side_effect"]:
            eligible.append(match["name"])

    activated: list[str] = []
    if eligible:
        active_set = activate_tools(rid, eligible)  # no-op unless run is deferred
        activated = [name for name in eligible if name in active_set]

    _record_tool_search_metrics(rid, query, len(matches), activated, agent_id)

    lines = [f"tool_search({query!r}): {len(matches)} match(es), {len(activated)} activated."]
    for match in matches:
        if match["name"] in activated:
            mark = "[activated]"
        elif not match["activatable"]:
            mark = f"[blocked: {match['reason']}]"
        elif match["side_effect"]:
            mark = "[side_effect: not auto-activated]"
        else:
            mark = "[eligible]"
        lines.append(f"  {match['name']} ({match['category']}/{match['source']}) {mark}")

    return {
        "ok": True,
        "text": "\n".join(lines),
        "matches": matches,
        "activated": activated,
    }


# ─── tool registry exposed to the local LLM provider ───────────────────────


def _format_checklist_text(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'todo_update failed')}"
    items = result.get("items") or []
    changed = result.get("changed") or []
    header = f"Checklist for run {result.get('run_id', '')}: {len(items)} item(s)"
    if changed:
        header += f", {len(changed)} changed"
    lines = [header]
    for item in items[:50]:
        blocker = str(item.get("blocker") or "").strip()
        suffix = f" blocker={blocker}" if blocker else ""
        lines.append(
            f"- {item.get('id')}: [{item.get('status')}] "
            f"{item.get('text')} (pos={item.get('position')}){suffix}"
        )
    if len(items) > 50:
        lines.append(f"[... truncated at 50 of {len(items)} items ...]")
    return "\n".join(lines)


def tool_todo_update(
    *,
    run_id: str,
    items: list[dict[str, Any]] | None = None,
    updates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read or update the durable checklist for the current agent run."""
    rid = str(run_id or "").strip()
    if not rid:
        return {"ok": False, "text": "ERROR: todo_update requires a run_id.", "error": "run_id_required"}
    if items is not None and not isinstance(items, list):
        return {"ok": False, "text": "ERROR: items must be a list.", "error": "invalid_items"}
    if updates is not None and not isinstance(updates, list):
        return {"ok": False, "text": "ERROR: updates must be a list.", "error": "invalid_updates"}

    try:
        from app.application.task_planner.service import todo_update
    except Exception as exc:
        return {"ok": False, "text": f"ERROR: task planner unavailable: {exc}", "error": str(exc)}

    try:
        result = todo_update(run_id=rid, items=items, updates=updates)
    except Exception as exc:
        return {"ok": False, "text": f"ERROR: {exc}", "error": str(exc)}
    result["text"] = _format_checklist_text(result)
    return result


def _delegate_prompt(role: str, task: str) -> str:
    role_guidance = {
        "explore": "Find relevant files, symbols, facts, and constraints. Do not propose edits unless asked.",
        "plan": "Produce a concise implementation plan and risks from read-only inspection.",
        "verify": "Inspect evidence and report whether the requested condition appears satisfied.",
    }
    guidance = role_guidance.get(role, role_guidance["explore"])
    return (
        f"You are a bounded read-only {role} subagent.\n"
        f"{guidance}\n"
        "Hard limits: do not write files, do not run shell, do not delegate further. "
        "Use only read_file/glob/grep/recall and non-side-effect tools activated by tool_search. "
        "Return concise findings with file paths when relevant.\n\n"
        f"Task:\n{task}"
    )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_delegate_text(result: dict[str, Any]) -> str:
    sub = result.get("subagent") or {}
    status = sub.get("status") or ("completed" if result.get("ok") else "failed")
    lines = [
        f"delegate_task role={result.get('role')} status={status}",
        f"subagent_run_id={result.get('subagent_run_id')}",
    ]
    if result.get("error"):
        lines.append(f"error={result['error']}")
    output = str(result.get("result_text") or "").strip()
    if output:
        lines.append("\nResult:\n" + output)
    return "\n".join(lines)


def tool_delegate_task(
    project_root: Path,
    *,
    run_id: str,
    role: str = "explore",
    task: str,
    max_steps: int = DELEGATE_TASK_MAX_STEPS,
    num_ctx: int = DELEGATE_TASK_MAX_CTX,
) -> dict[str, Any]:
    """Delegate a bounded read-only subtask to a child code-agent run."""
    parent_run_id = str(run_id or "").strip()
    if not parent_run_id:
        return {"ok": False, "text": "ERROR: delegate_task requires a run_id.", "error": "run_id_required"}
    normalized_role = str(role or "explore").strip().lower()
    if normalized_role not in DELEGATE_TASK_ROLES:
        return {"ok": False, "text": f"ERROR: unsupported delegate role: {normalized_role}", "error": "unsupported_role"}
    cleaned_task = str(task or "").strip()
    if not cleaned_task:
        return {"ok": False, "text": "ERROR: delegate_task requires a task.", "error": "task_required"}

    safe_steps = max(1, min(_safe_int(max_steps, DELEGATE_TASK_MAX_STEPS), DELEGATE_TASK_MAX_STEPS))
    safe_ctx = max(1024, min(_safe_int(num_ctx, DELEGATE_TASK_MAX_CTX), DELEGATE_TASK_MAX_CTX))

    try:
        from app.application.task_planner import service as task_service

        started = task_service.start_subagent_run(
            parent_run_id=parent_run_id,
            role=normalized_role,
            task=cleaned_task,
            depth=1,
            max_steps=safe_steps,
            max_context_tokens=safe_ctx,
            tool_allowlist=list(DELEGATE_TASK_READONLY_TOOLS),
        )
    except Exception as exc:
        return {"ok": False, "text": f"ERROR: failed to create subagent run: {exc}", "error": str(exc)}
    if not started.get("ok"):
        return {
            "ok": False,
            "text": f"ERROR: failed to create subagent run: {started.get('error')}",
            "error": str(started.get("error") or "start_failed"),
        }

    subagent_run_id = str(started.get("subagent_run_id") or "").strip()
    result_text = ""
    error = ""
    ok = False
    finished: dict[str, Any] = {}
    try:
        from app.application.code_agent.agent_loop import run_code_agent

        sub_result = run_code_agent(
            user_message=_delegate_prompt(normalized_role, cleaned_task),
            project_root=project_root,
            model="auto",
            agent_id=f"subagent-{normalized_role}",
            max_steps=safe_steps,
            run_id=subagent_run_id,
            num_ctx=safe_ctx,
            base_tools=DELEGATE_TASK_READONLY_TOOLS,
            execution_timeout_seconds=DELEGATE_TASK_TIMEOUT_SECONDS,
            auto_remember=False,
        )
        ok = bool(sub_result.get("ok"))
        result_text = str(sub_result.get("response") or "")
        error = str(sub_result.get("error") or "")
    except Exception as exc:
        error = str(exc)

    status = "completed" if ok else "failed"
    try:
        finished = task_service.finish_subagent_run(
            subagent_run_id=subagent_run_id,
            status=status,
            result_text=result_text,
            error=error,
        )
    except Exception as exc:
        error = f"{error}; finish_failed={exc}" if error else f"finish_failed={exc}"
        finished = {"ok": False, "error": str(exc), "subagent_run_id": subagent_run_id, "status": status}

    output = {
        "ok": ok,
        "role": normalized_role,
        "subagent_run_id": subagent_run_id,
        "result_text": result_text,
        "error": error,
        "subagent": finished if finished.get("ok") else {**started, "status": status},
    }
    output["text"] = _format_delegate_text(output)
    return output


# ─── content generation (image / Word / Excel) ───────────────────────────────
#
# These wrap the shared generators in app.application.{media,skills}. Each
# returns the artifact's absolute path plus a server-served download/view URL
# (/api/skills/{download,view}/...). The generated bytes are also copied into
# the project's generated/ subdir and reported via touched_path, so the file
# lands in the "Дерево" tab and fires a file_changed journal event. We do NOT
# set new_content (the payload is binary) — the diff/code preview correctly
# shows "no visual preview" rather than rendering raw bytes.

_GENERATED_SUBDIR = "generated"


def _mirror_into_project(project_root: Path, src_path: str, filename: str) -> str | None:
    """Copy a freshly-generated artifact into <project_root>/generated/<filename>.

    Returns the in-project relative path (POSIX-style) on success, else None.
    Never raises: a failed mirror just means the file is reachable only via its
    download URL, which is still returned to the model.
    """
    try:
        src = Path(src_path)
        if not src.is_file():
            return None
        dest = _resolve_safe(project_root, f"{_GENERATED_SUBDIR}/{filename}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return f"{_GENERATED_SUBDIR}/{filename}"
    except Exception:
        return None


def _format_runtime_result(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return {"text": f"ERROR: {result.get('error') or 'unknown error'}"}
    return {"text": f"{label}:\n{json.dumps(result, ensure_ascii=False, indent=2)}"}


def tool_translator(
    project_root: Path,
    *,
    text: str,
    target_lang: str = "english",
    model: str = "local-model",
) -> dict[str, Any]:
    from app.application.skills_extra.runtime import translate_text

    result = translate_text(text, target_lang=target_lang, model=model)
    if not result.get("ok"):
        return {"text": f"ERROR: translation failed: {result.get('error') or 'unknown error'}"}
    translated = result.get("translated") or result.get("translation") or result.get("text") or ""
    return {"text": str(translated).strip() or json.dumps(result, ensure_ascii=False)}


def tool_regex(
    project_root: Path,
    *,
    pattern: str,
    text: str,
    flags: str = "",
) -> dict[str, Any]:
    from app.application.skills_extra.runtime import test_regex

    return _format_runtime_result("Regex result", test_regex(pattern, text, flags=flags))


def tool_csv(
    project_root: Path,
    *,
    file_path: str,
    query: str = "",
) -> dict[str, Any]:
    from app.application.skills_extra.runtime import analyze_csv

    target = _resolve_safe(project_root, file_path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {file_path}"}
    return _format_runtime_result("CSV analysis", analyze_csv(str(target), query=query))


def tool_converter(
    project_root: Path,
    *,
    source_path: str,
    target_format: str,
) -> dict[str, Any]:
    from app.application.skills_extra.runtime import convert_file

    target = _resolve_safe(project_root, source_path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {source_path}"}
    return _format_runtime_result("Conversion result", convert_file(str(target), target_format=target_format))


def tool_http_api(
    project_root: Path,
    *,
    url: str,
    method: str = "GET",
    headers: dict[str, Any] | None = None,
    body: Any = None,
    timeout: int = 15,
) -> dict[str, Any]:
    from app.application.skills.runtime import http_request

    return _format_runtime_result(
        "HTTP result",
        http_request(url, method=method, headers=headers, body=body, timeout=int(timeout)),
    )


def tool_sql(
    project_root: Path,
    *,
    action: str,
    db_path: str = "",
    query: str = "",
    params: list[Any] | None = None,
    max_rows: int = 100,
) -> dict[str, Any]:
    from app.application.skills.runtime import describe_db, list_databases, run_sql

    mode = (action or "").strip().lower()
    if mode == "list":
        result = list_databases()
    elif mode == "describe":
        if not db_path.strip():
            return {"text": "ERROR: db_path is required for action=describe"}
        result = describe_db(db_path)
    elif mode == "query":
        if not db_path.strip() or not query.strip():
            return {"text": "ERROR: db_path and query are required for action=query"}
        result = run_sql(db_path, query, params=params, max_rows=int(max_rows))
    else:
        return {"text": "ERROR: action must be one of: list, describe, query"}
    return _format_runtime_result("SQL result", result)


def tool_encrypt(
    project_root: Path,
    *,
    action: str,
    text: str = "",
    token: str = "",
) -> dict[str, Any]:
    from app.application.skills_extra.runtime import decrypt_text, encrypt_text

    mode = (action or "").strip().lower()
    if mode == "encrypt":
        if not text:
            return {"text": "ERROR: text is required for action=encrypt"}
        result = encrypt_text(text)
    elif mode == "decrypt":
        if not token:
            return {"text": "ERROR: token is required for action=decrypt"}
        result = decrypt_text(token)
    else:
        return {"text": "ERROR: action must be encrypt or decrypt"}
    return _format_runtime_result("Encryption result", result)


def tool_archiver(
    project_root: Path,
    *,
    action: str,
    source_path: str = "",
    zip_path: str = "",
    dest: str = "",
    output_name: str = "",
) -> dict[str, Any]:
    from app.application.skills_extra.runtime import create_zip, extract_zip

    mode = (action or "").strip().lower()
    if mode == "create":
        if not source_path:
            return {"text": "ERROR: source_path is required for action=create"}
        source = _resolve_safe(project_root, source_path)
        result = create_zip(str(source), output_name=output_name)
    elif mode == "extract":
        if not zip_path:
            return {"text": "ERROR: zip_path is required for action=extract"}
        archive = _resolve_safe(project_root, zip_path)
        safe_dest = str(_resolve_safe(project_root, dest)) if dest else ""
        result = extract_zip(str(archive), dest=safe_dest)
    else:
        return {"text": "ERROR: action must be create or extract"}
    return _format_runtime_result("Archive result", result)


def tool_webhook(
    project_root: Path,
    *,
    action: str,
    data: dict[str, Any] | None = None,
    source: str = "code-agent",
    limit: int = 20,
) -> dict[str, Any]:
    from app.application.skills_extra.runtime import clear_webhooks, list_webhooks, store_webhook

    mode = (action or "").strip().lower()
    if mode == "store":
        result = store_webhook(data or {}, source=source)
    elif mode == "list":
        result = list_webhooks(limit=int(limit))
    elif mode == "clear":
        result = clear_webhooks()
    else:
        return {"text": "ERROR: action must be store, list, or clear"}
    return _format_runtime_result("Webhook result", result)


def tool_screenshot(
    project_root: Path,
    *,
    url: str,
    width: int = 1280,
    height: int = 800,
    full_page: bool = False,
) -> dict[str, Any]:
    from app.application.skills.runtime import screenshot_url

    return _format_runtime_result(
        "Screenshot result",
        screenshot_url(url, width=int(width), height=int(height), full_page=bool(full_page)),
    )


def tool_file_gen(
    project_root: Path,
    *,
    format: str,
    title: str = "",
    content: str = "",
    headers: list[Any] | None = None,
    data: list[Any] | None = None,
    filename: str = "",
) -> dict[str, Any]:
    fmt = (format or "").strip().lower()
    if fmt in ("word", "docx"):
        from app.application.skills import generate_word

        result = generate_word(title or "", content or "", filename or "")
    elif fmt in ("excel", "xlsx"):
        from app.application.skills import generate_excel

        result = generate_excel(title or "", data or [], headers or None, filename or "")
    else:
        return {"text": f"ERROR: unsupported format '{format}'. Use 'word' or 'excel'."}

    if not result.get("ok"):
        return {"text": f"ERROR: file generation failed: {result.get('error') or 'unknown error'}"}

    fname = str(result.get("filename") or "")
    rel = _mirror_into_project(project_root, str(result.get("path") or ""), fname)
    summary = (
        f"Generated {fmt} file {fname} ({result.get('size')} bytes).\n"
        f"Download: {result.get('download_url')}"
    )
    if rel:
        summary += f"\nSaved into project: {rel}"
    out: dict[str, Any] = {"text": summary}
    if rel:
        out["touched_path"] = rel
        out["diff_action"] = "create"
    return out


# ─── vision / OCR on project files ────────────────────────────────────────────
#
# These let the agent autonomously "see" an image or extract text from a scanned
# document already present in the project, by wrapping the server vision (:8004)
# and OCR (:8002) clients. Both clients are env-gated (VISION_ENABLED /
# OCR_ENABLED, default OFF) and fail-closed (return None when disabled or on
# failure); we surface that as a clear, non-fatal ERROR string so the agent can
# react rather than crash the run.


def tool_read_image(
    project_root: Path,
    *,
    path: str,
    prompt: str = "",
) -> dict[str, Any]:
    try:
        from app.infrastructure.llm.vision_ocr import describe_image, is_vision_enabled
    except Exception as exc:  # pragma: no cover - import guard
        return {"text": f"ERROR: vision support unavailable: {exc}"}

    if not is_vision_enabled():
        return {"text": "ERROR: vision is disabled (set VISION_ENABLED=1 on the server to enable read_image)."}

    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}

    try:
        contents = target.read_bytes()
    except OSError as exc:
        return {"text": f"ERROR: failed to read image {path}: {exc}"}

    description = describe_image(target.name, contents, prompt=(prompt or None))
    if not description:
        return {"text": f"ERROR: vision returned no description for {path} (service unreachable or empty response)."}
    return {"text": f"Image description for {path}:\n{description}"}


def tool_ocr_file(
    project_root: Path,
    *,
    path: str,
    language: str = "",
) -> dict[str, Any]:
    try:
        from app.infrastructure.llm.vision_ocr import is_ocr_enabled, ocr_document
    except Exception as exc:  # pragma: no cover - import guard
        return {"text": f"ERROR: OCR support unavailable: {exc}"}

    if not is_ocr_enabled():
        return {"text": "ERROR: OCR is disabled (set OCR_ENABLED=1 on the server to enable ocr_file)."}

    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return {"text": f"ERROR: not a file or does not exist: {path}"}

    try:
        contents = target.read_bytes()
    except OSError as exc:
        return {"text": f"ERROR: failed to read file {path}: {exc}"}

    text = ocr_document(target.name, contents, language=(language or None))
    if not text:
        return {"text": f"ERROR: OCR found no text in {path} (service unreachable or no recognizable text)."}
    return {"text": f"OCR text from {path}:\n{text}"}


def build_tool_dispatch(project_root: Path) -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "read_file": lambda **kw: tool_read_file(project_root, **kw),
        "write_file": lambda **kw: tool_write_file(project_root, **kw),
        "edit_file": lambda **kw: tool_edit_file(project_root, **kw),
        "glob": lambda **kw: tool_glob(project_root, **kw),
        "grep": lambda **kw: tool_grep(project_root, **kw),
        "project_map": lambda **kw: tool_project_map(project_root, **kw),
        "recall": lambda **kw: tool_recall(project_root, **kw),
        "todo_update": lambda **kw: tool_todo_update(**kw),
        "delegate_task": lambda **kw: tool_delegate_task(project_root, **kw),
        "run_bash": lambda **kw: tool_run_bash(project_root, **kw),
        "run_server": lambda **kw: tool_run_server(project_root, **kw),
        "web_search": lambda **kw: tool_web_search(**kw),
        "web_fetch": lambda **kw: tool_web_fetch(**kw),
        "browser": lambda **kw: tool_browser(**kw),
        "sandbox_run": lambda **kw: tool_sandbox_run(project_root, **kw),
        "sandbox_reset": lambda **kw: tool_sandbox_reset(project_root, **kw),
        "translator": lambda **kw: tool_translator(project_root, **kw),
        "regex": lambda **kw: tool_regex(project_root, **kw),
        "csv": lambda **kw: tool_csv(project_root, **kw),
        "converter": lambda **kw: tool_converter(project_root, **kw),
        "http_api": lambda **kw: tool_http_api(project_root, **kw),
        "sql": lambda **kw: tool_sql(project_root, **kw),
        "encrypt": lambda **kw: tool_encrypt(project_root, **kw),
        "archiver": lambda **kw: tool_archiver(project_root, **kw),
        "webhook": lambda **kw: tool_webhook(project_root, **kw),
        "screenshot": lambda **kw: tool_screenshot(project_root, **kw),
        "file_gen": lambda **kw: tool_file_gen(project_root, **kw),
        "read_image": lambda **kw: tool_read_image(project_root, **kw),
        "ocr_file": lambda **kw: tool_ocr_file(project_root, **kw),
    }
