"""Terminal service — sandboxed shell command execution.

Holds the process-level current working directory and handles
Windows CP866/CP1251 → UTF-8 output decoding.
"""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from app.core.config import DATA_DIR

WORKSPACE: Path = (DATA_DIR / "workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

_cwd: str = str(WORKSPACE)
_IS_WINDOWS: bool = platform.system() == "Windows"
_TIMEOUT: int = 15

BLOCKED = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", "format c:",
    "shutdown", "reboot", ":(){:|:&};:", "deltree",
]


def get_cwd() -> str:
    return _cwd


def change_dir(target: str) -> dict:
    global _cwd
    target = target.strip().strip('"').strip("'")
    if not target:
        return {"ok": True, "cwd": _cwd}
    new_path = (Path(_cwd) / target) if not Path(target).is_absolute() else Path(target)
    new_path = new_path.resolve()
    if new_path.exists() and new_path.is_dir():
        _cwd = str(new_path)
        return {"ok": True, "cwd": _cwd}
    return {"ok": False, "error": f"Not found: {target}", "cwd": _cwd}


def exec_command(command: str, cwd: str = "") -> dict:
    global _cwd
    cmd = command.strip()
    if not cmd:
        return {"ok": False, "error": "Empty command"}

    cmd_lower = cmd.lower()
    for blocked in BLOCKED:
        if blocked in cmd_lower:
            return {"ok": False, "error": f"Blocked command: {blocked}"}

    if cmd.startswith("cd "):
        return change_dir(cmd[3:].strip().strip('"').strip("'"))

    work_dir = cwd or _cwd
    if not Path(work_dir).exists():
        work_dir = _cwd

    try:
        if _IS_WINDOWS:
            full_cmd = f"chcp 65001 >nul 2>&1 && {cmd}"
            result = subprocess.run(
                full_cmd, shell=True, cwd=work_dir,
                capture_output=True, timeout=_TIMEOUT,
            )
            stdout = _decode_win(result.stdout)
            stderr = _decode_win(result.stderr)
        else:
            result = subprocess.run(
                cmd, shell=True, cwd=work_dir,
                capture_output=True, text=True, timeout=_TIMEOUT,
                encoding="utf-8", errors="replace",
            )
            stdout = result.stdout
            stderr = result.stderr

        return {
            "ok": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "cwd": work_dir,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout ({_TIMEOUT}s)", "cwd": work_dir}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "cwd": work_dir}


def _decode_win(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")
