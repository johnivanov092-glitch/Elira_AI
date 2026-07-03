"""Project-prompt CRUD — read/init/write the per-project agent instructions
file (.elira/agent.md).

A leaf module: it imports nothing from agent_loop. agent_loop re-exports these
names (and PROJECT_PROMPT_FILENAME) so existing importers (code_agent_routes,
tests) keep resolving them from agent_loop unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PROJECT_PROMPT_FILENAME = ".elira/agent.md"
# Opt-in verify command: if a project has `.elira/verify` with a shell command,
# the code-agent must run it green before it may declare a task done after edits
# (a non-skippable E2E gate — the model can't rubber-stamp "проверено").
VERIFY_COMMAND_FILENAME = ".elira/verify"


def get_verify_command(project_root: Path | str) -> str | None:
    """The project's configured verify command, or None if not set. Reads the
    first non-empty, non-comment line of `.elira/verify`.

    Robust to how the user created the file on Windows: utf-8-sig strips a UTF-8
    BOM (PowerShell `>` / Notepad "Save as UTF-8"), and a UTF-16 BOM (PowerShell
    5.1 default) is decoded as a fallback — otherwise the command would silently
    read as broken (`﻿pytest`) or fail to decode."""
    target = Path(project_root).resolve() / VERIFY_COMMAND_FILENAME
    if not target.is_file():
        return None
    try:
        data = target.read_bytes()
    except Exception:
        return None
    text = ""
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿").strip()
        if line and not line.startswith("#"):
            return line
    return None


def get_project_prompt(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    target = root / PROJECT_PROMPT_FILENAME
    exists = target.is_file()
    content = ""
    if exists:
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "exists": True, "content": "", "error": str(exc), "path": str(target)}
    return {"ok": True, "exists": exists, "content": content, "path": str(target)}


def suggest_verify_command(project_root: Path | str) -> str:
    """Best-effort guess of a verify command from the project's marker files, so
    the UI can pre-fill it and the user just confirms. Returns "" if unsure."""
    root = Path(project_root).resolve()

    def has(name: str) -> bool:
        return (root / name).exists()

    # Node: prefer a real `test` script, else a build.
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            import json
            scripts = (json.loads(pkg.read_text(encoding="utf-8-sig")) or {}).get("scripts") or {}
            if isinstance(scripts, dict):
                test = str(scripts.get("test") or "")
                if test and "no test specified" not in test:
                    return "npm test"
                if scripts.get("build"):
                    return "npm run build"
        except Exception:
            pass
    # Python
    if has("pytest.ini") or has("pyproject.toml") or has("setup.cfg") or has("conftest.py") or (root / "tests").is_dir():
        return "pytest -q"
    if has("Cargo.toml"):
        return "cargo test"
    if has("go.mod"):
        return "go test ./..."
    mk = root / "Makefile"
    if mk.is_file():
        try:
            if any(ln.strip().startswith("test:") for ln in mk.read_text(encoding="utf-8-sig", errors="ignore").splitlines()):
                return "make test"
        except Exception:
            pass
    return ""


def set_verify_command(project_root: Path | str, command: str) -> dict[str, Any]:
    """Write (or clear) the project's `.elira/verify` command. An empty command
    removes the file — disabling the gate. Written as plain UTF-8, no BOM."""
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}
    target = root / VERIFY_COMMAND_FILENAME
    cmd = (command or "").strip()
    try:
        if cmd:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(cmd + "\n", encoding="utf-8")
        elif target.exists():
            target.unlink()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(target)}
    return {"ok": True, "command": cmd, "path": str(target)}


def init_project_prompt(project_root: Path | str, content: str | None = None) -> dict[str, Any]:
    from app.application.instructions.loader import init_project_instructions

    return init_project_instructions(project_root, content=content)


def set_project_prompt(project_root: Path | str, content: str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}
    target = root / PROJECT_PROMPT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(target)}
    return {"ok": True, "exists": True, "content": content, "path": str(target)}
