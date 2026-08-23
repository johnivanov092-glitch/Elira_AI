"""Per-project Python execution sandbox.

The code-agent's `run_bash` tool runs commands in the user's actual
project directory with their main Python. That's correct for tasks
where the agent edits real code and runs the real test suite, but
it's the wrong tool for "let me try this snippet" or "install
package X and see if it works". Such experiments would either
pollute the user's main environment or risk modifying tracked
project files.

This module provides a sandboxed alternative:

  - Each project gets its own venv at `<data>/sandbox/<slug>/venv/`
    where slug includes a hash of the normalized absolute project path.
  - Scripts execute with `cwd` set to `<data>/sandbox/<slug>/work/`,
    which starts empty and stays empty unless the script writes into
    it. The user's actual project root is never touched.
  - `pip install` requests go into the venv only, so the user's
    main Python interpreter is never polluted.
  - The sandbox persists across agent turns and even across whole
    chat sessions for the same project — installed packages stay
    installed, files written stay written, until the user explicitly
    calls `sandbox_reset`.

API:
  run_in_sandbox(project_root, code, install, timeout) → dict result
  reset_sandbox(project_root) → dict
  sandbox_status(project_root) → dict
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path
from typing import Any

from app.application.code_agent.tools._run import _agent_child_env
from app.application.code_agent.tools._shell import (
    _CURRENT_RUN_ID,
    _KILLED_RUN_IDS,
    _LIVE_SHELL_LOCK,
    _new_process_group_kwargs,
    _register_shell_proc,
    _unregister_shell_proc,
)
from app.application.projects.scope import project_scope_slug
from app.core.data_files import DATA_DIR
from app.infrastructure.encoding import decode_console
from app.infrastructure.text import truncate_head as _truncate


_SANDBOX_ROOT = DATA_DIR / "sandbox"
_SCRIPT_NAME = "_agent_script.py"
# Hard caps to keep tool output sane for the LLM context (lower limit
# than the LLM-context cap because the agent already gets tool output
# in raw form on its next turn — bigger means slower iterations).
_STDOUT_LIMIT = 16000
_STDERR_LIMIT = 6000


def _slug(project_root: Path) -> str:
    """Filesystem-safe key unique to the normalized absolute project path."""
    return project_scope_slug(project_root)


def _sandbox_dir(project_root: Path) -> Path:
    return _SANDBOX_ROOT / _slug(project_root)


def _venv_python(sandbox: Path) -> Path:
    """Path to the venv's python executable. Windows puts it in
    Scripts/, POSIX puts it in bin/."""
    if sys.platform == "win32":
        return sandbox / "venv" / "Scripts" / "python.exe"
    return sandbox / "venv" / "bin" / "python"


def _venv_pip(sandbox: Path) -> Path:
    if sys.platform == "win32":
        return sandbox / "venv" / "Scripts" / "pip.exe"
    return sandbox / "venv" / "bin" / "pip"


def _ensure_sandbox(project_root: Path) -> Path:
    """Create the per-project sandbox dir and venv if they don't
    exist yet. First call costs ~3-5 s (venv creation); subsequent
    calls are no-ops."""
    sandbox = _sandbox_dir(project_root)
    work_dir = sandbox / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    py = _venv_python(sandbox)
    if not py.exists():
        # `with_pip=True` is the default but spell it out so the failure
        # mode is obvious if a system lacks ensurepip.
        venv.create(sandbox / "venv", with_pip=True, clear=False, symlinks=False)

    return sandbox


def run_in_sandbox(
    project_root: Path | str,
    *,
    code: str,
    install: list[str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Execute `code` (Python) inside the project's sandbox venv.

    `install` is a list of pip package specs (e.g. ["requests",
    "rich>=13"]). They are installed into the venv before running
    the script — installs are cached, so repeating the same
    package name is cheap.

    Returns:
      {
        "ok": bool,         # process exit was 0 AND no setup error
        "stdout": str,      # captured stdout (truncated)
        "stderr": str,      # captured stderr (truncated)
        "exit_code": int,   # subprocess returncode (-1 if not started)
        "took_seconds": float,
        "sandbox_path": str,  # absolute path of the sandbox
        "install_log": str,   # output of pip install, if any
      }
    """
    del timeout  # compatibility input; Workflow Stop owns termination
    root = Path(project_root).resolve()
    started = time.monotonic()
    sandbox = _ensure_sandbox(root)
    work = sandbox / "work"

    install_log = ""
    if install:
        # Popen receives an argv list, so every non-empty string is passed to pip
        # literally. Workflow permission, not a hidden package/option allowlist,
        # authorizes the operation.
        clean = [p.strip() for p in install if isinstance(p, str) and p.strip()]
        if clean:
            proc, stdout, stderr, cancelled = _run_cancellable(
                [str(_venv_pip(sandbox)), "install", "--disable-pip-version-check", "--quiet", *clean],
                env=_agent_child_env(),
            )
            install_log = decode_console(stdout) + decode_console(stderr)
            if cancelled:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                    "took_seconds": round(time.monotonic() - started, 3),
                    "sandbox_path": str(sandbox),
                    "install_log": "",
                    "error": "pip install stopped by user",
                }
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                    "took_seconds": round(time.monotonic() - started, 3),
                    "sandbox_path": str(sandbox),
                    "install_log": _truncate(install_log, _STDERR_LIMIT),
                    "error": f"pip install failed (exit {proc.returncode})",
                }

    script_path = sandbox / _SCRIPT_NAME
    script_path.write_text(code or "", encoding="utf-8")

    # Use the same full current-token environment as run_bash, then layer the
    # UTF-8 knobs that keep print()/repr predictable cross-platform.
    env = {**_agent_child_env(), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    proc, stdout, stderr, cancelled = _run_cancellable(
        [str(_venv_python(sandbox)), str(script_path)],
        cwd=str(work),
        env=env,
    )
    if cancelled:
        return {
            "ok": False,
            "stdout": _truncate(decode_console(stdout), _STDOUT_LIMIT),
            "stderr": _truncate(decode_console(stderr), _STDERR_LIMIT),
            "exit_code": -1,
            "took_seconds": round(time.monotonic() - started, 3),
            "sandbox_path": str(sandbox),
            "install_log": _truncate(install_log, _STDERR_LIMIT),
            "error": "sandbox run stopped by user",
        }

    return {
        "ok": proc.returncode == 0,
        "stdout": _truncate(decode_console(stdout), _STDOUT_LIMIT),
        "stderr": _truncate(decode_console(stderr), _STDERR_LIMIT),
        "exit_code": int(proc.returncode),
        "took_seconds": round(time.monotonic() - started, 3),
        "sandbox_path": str(sandbox),
        "install_log": _truncate(install_log, _STDERR_LIMIT),
    }


def _run_cancellable(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen, bytes, bytes, bool]:
    """Run a sandbox child until natural exit or the Workflow Stop signal."""
    run_id = _CURRENT_RUN_ID.get()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        env=env,
        **_new_process_group_kwargs(),
    )
    _register_shell_proc(run_id, proc)
    try:
        stdout, stderr = proc.communicate()
    finally:
        _unregister_shell_proc(run_id, proc)
    cancelled = False
    if run_id:
        with _LIVE_SHELL_LOCK:
            if run_id in _KILLED_RUN_IDS:
                _KILLED_RUN_IDS.discard(run_id)
                cancelled = True
    if not cancelled and proc.returncode is not None and proc.returncode < 0:
        cancelled = True
    return proc, stdout or b"", stderr or b"", cancelled


def reset_sandbox(project_root: Path | str) -> dict[str, Any]:
    """Nuke the project's sandbox (venv + work dir + scripts) entirely.

    Next `run_in_sandbox` call will recreate it from scratch.
    """
    root = Path(project_root).resolve()
    sandbox = _sandbox_dir(root)
    if not sandbox.exists():
        return {"ok": True, "existed": False, "sandbox_path": str(sandbox)}
    try:
        shutil.rmtree(sandbox)
    except Exception as exc:
        return {"ok": False, "existed": True, "sandbox_path": str(sandbox), "error": str(exc)}
    return {"ok": True, "existed": True, "sandbox_path": str(sandbox)}


def sandbox_status(project_root: Path | str) -> dict[str, Any]:
    """Report whether the sandbox exists and how many files live in
    the work dir."""
    root = Path(project_root).resolve()
    sandbox = _sandbox_dir(root)
    if not sandbox.exists():
        return {"exists": False, "sandbox_path": str(sandbox)}
    py = _venv_python(sandbox)
    work = sandbox / "work"
    file_count = 0
    if work.is_dir():
        for _ in work.rglob("*"):
            file_count += 1
    return {
        "exists": True,
        "venv_ready": py.exists(),
        "work_file_count": file_count,
        "sandbox_path": str(sandbox),
    }
