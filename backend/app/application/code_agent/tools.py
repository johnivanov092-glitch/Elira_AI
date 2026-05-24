"""Code-agent tools — sandboxed file & shell operations exposed to the LLM
via Ollama function-calling.

Every tool receives a `project_root` (resolved Path) and refuses to touch any
path that escapes it. This is the minimal viable tool set for a Claude
Code / Codex-class local agent:
    read_file, write_file, edit_file, glob, grep, run_bash
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


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


# ─── tool implementations ────────────────────────────────────────────────────


def tool_read_file(
    project_root: Path,
    *,
    path: str,
    offset: int = 0,
    limit: int = 2000,
) -> str:
    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return f"ERROR: not a file or does not exist: {path}"
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        return f"ERROR: {exc}"
    start = max(0, int(offset))
    end = start + max(1, int(limit))
    selected = lines[start:end]
    numbered = "".join(f"{i + 1 + start:>5}\t{ln}" for i, ln in enumerate(selected))
    suffix = "" if end >= len(lines) else f"\n[... truncated at line {end} of {len(lines)}]"
    return numbered + suffix


def tool_write_file(project_root: Path, *, path: str, content: str) -> str:
    target = _resolve_safe(project_root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content, encoding="utf-8")
    action = "Overwrote" if existed else "Created"
    return f"{action} {path} ({len(content)} chars)"


def tool_edit_file(
    project_root: Path,
    *,
    path: str,
    old_string: str,
    new_string: str,
) -> str:
    target = _resolve_safe(project_root, path)
    if not target.is_file():
        return f"ERROR: not a file or does not exist: {path}"
    current = target.read_text(encoding="utf-8")
    if old_string not in current:
        return f"ERROR: old_string not found in {path}"
    occurrences = current.count(old_string)
    if occurrences > 1:
        return (
            f"ERROR: old_string matches {occurrences} times in {path}. "
            "Provide a larger surrounding context to make it unique."
        )
    updated = current.replace(old_string, new_string, 1)
    target.write_text(updated, encoding="utf-8")
    return f"Edited {path} (1 replacement)"


def tool_glob(project_root: Path, *, pattern: str) -> str:
    root = project_root.resolve()
    matches: list[str] = []
    for raw_match in root.glob(pattern):
        try:
            matches.append(str(raw_match.relative_to(root)).replace("\\", "/"))
        except ValueError:
            continue
    matches.sort()
    if not matches:
        return f"No files match '{pattern}'"
    return "\n".join(matches[:200])


def tool_grep(
    project_root: Path,
    *,
    pattern: str,
    path: str = ".",
    glob: str = "*",
) -> str:
    base = _resolve_safe(project_root, path)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"
    if base.is_file():
        files = [base]
    else:
        files = [
            f for f in base.rglob(glob)
            if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts
        ]
    out: list[str] = []
    for f in files:
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if regex.search(line):
                        rel = str(f.relative_to(project_root.resolve())).replace("\\", "/")
                        out.append(f"{rel}:{lineno}:{line.rstrip()}")
                        if len(out) >= 200:
                            break
        except Exception:
            continue
        if len(out) >= 200:
            out.append("[... truncated at 200 matches]")
            break
    return "\n".join(out) if out else f"No matches for '{pattern}' in {path}"


def tool_run_bash(project_root: Path, *, command: str, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=int(timeout),
            cwd=str(project_root.resolve()),
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    except Exception as exc:
        return f"ERROR: {exc}"
    parts = [f"$ {command}", f"exit={proc.returncode}"]
    if proc.stdout:
        parts.append(f"STDOUT:\n{proc.stdout.rstrip()}")
    if proc.stderr:
        parts.append(f"STDERR:\n{proc.stderr.rstrip()}")
    return "\n".join(parts)


# ─── tool registry exposed to Ollama ────────────────────────────────────────


def build_tool_schemas() -> list[dict[str, Any]]:
    """Ollama function-calling tool schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the project. Returns lines with line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path relative to project root, or absolute inside it."},
                        "offset": {"type": "integer", "description": "Starting line (0-based). Default 0."},
                        "limit": {"type": "integer", "description": "Max lines to read. Default 2000."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create a new file or overwrite an existing one with the given content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace the single occurrence of `old_string` with `new_string` in `path`. Errors if old_string is not unique.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": "Find files matching a glob pattern (e.g. '**/*.py').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search file contents for a regex pattern. Returns 'file:line:match' lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "description": "Directory or file to search. Default: project root."},
                        "glob": {"type": "string", "description": "Glob filter for files. Default: '*'"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_bash",
                "description": "Run a shell command inside the project root. Returns stdout, stderr, and exit code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "description": "Seconds. Default 60."},
                    },
                    "required": ["command"],
                },
            },
        },
    ]


def build_tool_dispatch(project_root: Path) -> dict[str, Callable[..., str]]:
    return {
        "read_file": lambda **kw: tool_read_file(project_root, **kw),
        "write_file": lambda **kw: tool_write_file(project_root, **kw),
        "edit_file": lambda **kw: tool_edit_file(project_root, **kw),
        "glob": lambda **kw: tool_glob(project_root, **kw),
        "grep": lambda **kw: tool_grep(project_root, **kw),
        "run_bash": lambda **kw: tool_run_bash(project_root, **kw),
    }
