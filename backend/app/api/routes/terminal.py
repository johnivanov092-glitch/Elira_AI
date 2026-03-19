"""
terminal.py — терминал для Code вкладки.

Фикс: Windows CP866 кодировка → chcp 65001 (UTF-8) перед командой.
"""
from __future__ import annotations
import os
import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

WORKSPACE = Path("data/workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

_cwd = str(WORKSPACE)
_IS_WINDOWS = platform.system() == "Windows"

BLOCKED = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", "format c:",
    "shutdown", "reboot", ":(){:|:&};:", "deltree",
]
TIMEOUT = 15


class ExecRequest(BaseModel):
    command: str
    cwd: str = ""


class CdRequest(BaseModel):
    path: str


@router.post("/exec")
def exec_command(payload: ExecRequest):
    global _cwd
    cmd = payload.command.strip()
    if not cmd:
        return {"ok": False, "error": "Пустая команда"}

    cmd_lower = cmd.lower()
    for blocked in BLOCKED:
        if blocked in cmd_lower:
            return {"ok": False, "error": f"Команда заблокирована: {blocked}"}

    if cmd.startswith("cd "):
        return _change_dir(cmd[3:].strip().strip('"').strip("'"))

    work_dir = payload.cwd or _cwd
    if not Path(work_dir).exists():
        work_dir = _cwd

    try:
        if _IS_WINDOWS:
            # Принудительно UTF-8 на Windows: chcp 65001
            full_cmd = f"chcp 65001 >nul 2>&1 && {cmd}"
            result = subprocess.run(
                full_cmd,
                shell=True,
                cwd=work_dir,
                capture_output=True,
                timeout=TIMEOUT,
            )
            # Декодируем: сначала utf-8, потом cp866, потом cp1251
            stdout = _decode_win(result.stdout)
            stderr = _decode_win(result.stderr)
        else:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                encoding="utf-8",
                errors="replace",
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
        return {"ok": False, "error": f"Таймаут ({TIMEOUT}с)", "cwd": work_dir}
    except Exception as e:
        return {"ok": False, "error": str(e), "cwd": work_dir}


def _decode_win(data: bytes) -> str:
    """Декодирует вывод Windows cmd: UTF-8 → CP866 → CP1251."""
    if not data:
        return ""
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


@router.get("/cwd")
def get_cwd():
    global _cwd
    return {"ok": True, "cwd": _cwd}


@router.post("/cd")
def change_dir(payload: CdRequest):
    return _change_dir(payload.path)


def _change_dir(target: str):
    global _cwd
    target = target.strip().strip('"').strip("'")
    if not target:
        return {"ok": True, "cwd": _cwd}

    new_path = Path(_cwd) / target if not Path(target).is_absolute() else Path(target)
    new_path = new_path.resolve()

    if new_path.exists() and new_path.is_dir():
        _cwd = str(new_path)
        return {"ok": True, "cwd": _cwd}
    else:
        return {"ok": False, "error": f"Не найдена: {target}", "cwd": _cwd}
