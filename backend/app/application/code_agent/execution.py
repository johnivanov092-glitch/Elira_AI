from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

PYTHON_EXEC_TIMEOUT = 30

FIGURE_SAVER = textwrap.dedent(
    """
import atexit, os, pathlib

_FIG_DIR = pathlib.Path(os.environ.get("_FIG_DIR", "."))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:
    plt = None

def _save_all_figures():
    if plt is None:
        return
    for n in plt.get_fignums():
        fig = plt.figure(n)
        out = _FIG_DIR / f"fig_{n}.png"
        fig.savefig(str(out), bbox_inches="tight", dpi=100)
        plt.close(fig)

atexit.register(_save_all_figures)
"""
)

def execute_python_with_capture(
    code: str,
    extra_globals: dict | None = None,
    timeout: int = PYTHON_EXEC_TIMEOUT,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="elira_exec_") as tmp:
        tmp_path = Path(tmp)
        code_file = tmp_path / "_run.py"

        prelude = FIGURE_SAVER
        if extra_globals:
            serializable: dict[str, Any] = {}
            for key, value in extra_globals.items():
                try:
                    json.dumps(value)
                    serializable[key] = value
                except Exception:
                    pass
            if serializable:
                prelude += (
                    "\nimport json as _json\n"
                    f"_injected = _json.loads({repr(json.dumps(serializable))})\n"
                    "globals().update(_injected)\n"
                )

        code_file.write_text(prelude + "\n" + code, encoding="utf-8")

        env = os.environ.copy()
        env["_FIG_DIR"] = str(tmp_path)
        env["MPLBACKEND"] = "Agg"

        try:
            proc = subprocess.run(
                [sys.executable, str(code_file)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(tmp_path),
                env=env,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            is_error = proc.returncode != 0 or "Traceback" in stderr

            figures: list[bytes] = []
            for figure_file in sorted(tmp_path.glob("fig_*.png")):
                try:
                    figures.append(figure_file.read_bytes())
                except Exception:
                    pass

            return {
                "ok": not is_error,
                "output": stdout or ("Код выполнен без вывода" if not is_error else ""),
                "traceback": stderr if is_error else "",
                "warnings": stderr if not is_error and stderr else "",
                "figures": figures,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "output": "",
                "traceback": (
                    f"⏱ Превышен таймаут выполнения ({timeout} сек). "
                    "Проверь нет ли бесконечного цикла."
                ),
                "warnings": "",
                "figures": [],
            }
        except Exception as exc:
            return {
                "ok": False,
                "output": "",
                "traceback": str(exc),
                "warnings": "",
                "figures": [],
            }

def ok_check(stdout: str, stderr: str, returncode: int) -> bool:
    if returncode != 0:
        return False
    if "Traceback (most recent call last)" in stderr:
        return False
    error_lines = [line for line in stderr.splitlines() if re.match(r"^\w*Error:", line.strip())]
    return len(error_lines) == 0

def run_in_dir(cmd: str, cwd: Path, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
        )
        return f"$ {cmd}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n\nКоманда остановлена по таймауту ({timeout} сек.)"
    except Exception as exc:
        return f"Ошибка запуска: {exc}"
