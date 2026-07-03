from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from app.core.config import DATA_DIR, TERMINAL_BLOCKED

WORKSPACE = (DATA_DIR / "workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

_cwd = str(WORKSPACE)
_IS_WINDOWS = platform.system() == "Windows"

# Blocked-command list is centralised in app.core.config (single source of
# truth shared with domain/tools/terminal_tool.py).
BLOCKED = TERMINAL_BLOCKED
TIMEOUT = 15
STDOUT_LIMIT = 16000
STDERR_LIMIT = 6000


from app.infrastructure.text import truncate_middle  # canonical (single source)


def exec_command(command: str, cwd: str = ""):
    global _cwd
    cmd = command.strip()
    if not cmd:
        return {"ok": False, "error": "\u041f\u0443\u0441\u0442\u0430\u044f \u043a\u043e\u043c\u0430\u043d\u0434\u0430"}

    cmd_lower = cmd.lower()
    for blocked in BLOCKED:
        if blocked in cmd_lower:
            return {
                "ok": False,
                "error": (
                    "\u041a\u043e\u043c\u0430\u043d\u0434\u0430 "
                    "\u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u0430: "
                    f"{blocked}"
                ),
            }

    if cmd.startswith("cd "):
        return change_dir(cmd[3:].strip().strip('"').strip("'"))

    work_dir = cwd or _cwd
    if not Path(work_dir).exists():
        work_dir = _cwd

    try:
        if _IS_WINDOWS:
            full_cmd = f"chcp 65001 >nul 2>&1 && {cmd}"
            result = subprocess.run(
                full_cmd,
                shell=True,
                cwd=work_dir,
                capture_output=True,
                timeout=TIMEOUT,
            )
            stdout = truncate_middle(decode_win(result.stdout), STDOUT_LIMIT)
            stderr = truncate_middle(decode_win(result.stderr), STDERR_LIMIT)
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
            stdout = truncate_middle(result.stdout, STDOUT_LIMIT)
            stderr = truncate_middle(result.stderr, STDERR_LIMIT)

        return {
            "ok": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "cwd": work_dir,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"\u0422\u0430\u0439\u043c\u0430\u0443\u0442 ({TIMEOUT}\u0441)",
            "cwd": work_dir,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "cwd": work_dir}


# Canonical decoder now lives in app.infrastructure.encoding; kept as decode_win
# for this module's call sites.
from app.infrastructure.encoding import decode_console as decode_win


def get_cwd():
    return {"ok": True, "cwd": _cwd}


def change_dir(target: str):
    global _cwd
    target = target.strip().strip('"').strip("'")
    if not target:
        return {"ok": True, "cwd": _cwd}

    new_path = Path(_cwd) / target if not Path(target).is_absolute() else Path(target)
    new_path = new_path.resolve()

    if new_path.exists() and new_path.is_dir():
        _cwd = str(new_path)
        return {"ok": True, "cwd": _cwd}
    return {
        "ok": False,
        "error": f"\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430: {target}",
        "cwd": _cwd,
    }
