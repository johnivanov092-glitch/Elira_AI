from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from app.core.config import GENERATED_DIR
from app.core.llm import ask_model, clean_code_fence

PYTHON_EXEC_TIMEOUT = 30

FIGURE_SAVER = textwrap.dedent(
    """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import atexit, os, pathlib

_FIG_DIR = pathlib.Path(os.environ.get("_FIG_DIR", "."))

def _save_all_figures():
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


def self_heal_python_code(
    generated_code: str,
    task: str,
    file_path: str,
    schema_text: str,
    model_name: str,
    max_retries: int = 2,
    num_ctx: int = 4096,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    history: list[dict[str, Any]] = []
    current_code = generated_code
    last_result: dict[str, Any] | None = None

    for attempt in range(1, max_retries + 2):
        result = execute_python_with_capture(current_code)
        history.append(
            {
                "attempt": attempt,
                "code": current_code,
                "ok": result["ok"],
                "output": result["output"],
                "traceback": result["traceback"],
            }
        )
        last_result = result

        if result["ok"]:
            return current_code, result, history
        if attempt >= max_retries + 1:
            break

        repair_prompt = (
            "Ты исправляешь Python-код после ошибки выполнения.\n"
            "Верни только исправленный Python-код без markdown и пояснений.\n\n"
            f"Путь к файлу данных:\n{file_path}\n\n"
            f"Задача:\n{task}\n\n"
            f"Схема данных:\n{schema_text}\n\n"
            f"Текущий код:\n{current_code}\n\n"
            f"STDOUT:\n{result['output']}\n\n"
            f"TRACEBACK:\n{result['traceback']}\n\n"
            "Исправь код так, чтобы он выполнился успешно. "
            "Не используй markdown, верни только чистый Python."
        )
        fixed = ask_model(
            model_name=model_name,
            profile_name="Аналитик",
            user_input=repair_prompt,
            temp=0.1,
            include_history=False,
            num_ctx=num_ctx,
        ).strip()
        current_code = clean_code_fence(fixed)

    return current_code, last_result or {}, history


def generate_file_code(
    target_file: str,
    task: str,
    model_name: str,
    project_context: str,
    file_context: str,
    num_ctx: int = 4096,
) -> str:
    prompt = (
        f"Напиши полный рабочий код для файла {target_file}.\n"
        "Верни только содержимое файла без markdown.\n\n"
        f"Задача:\n{task}\n\n"
        f"Контекст проекта:\n{project_context[:20000]}\n\n"
        f"Контекст файлов:\n{file_context[:8000]}"
    )
    code = ask_model(
        model_name,
        "Программист",
        prompt,
        project_context=project_context,
        file_context=file_context,
        include_history=False,
        num_ctx=num_ctx,
    )
    return clean_code_fence(code)


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


def run_build_loop(
    target_file: str,
    task: str,
    run_command: str,
    model_name: str,
    max_retries: int,
    project_context: str,
    file_context: str,
    num_ctx: int = 4096,
) -> tuple[str, str, list[dict[str, Any]]]:
    history: list[dict[str, Any]] = []

    code = generate_file_code(
        target_file,
        task,
        model_name,
        project_context,
        file_context,
        num_ctx,
    )

    with tempfile.TemporaryDirectory(prefix="elira_build_") as tmp:
        tmp_path = Path(tmp)
        target_path = tmp_path / Path(target_file).name

        for attempt in range(1, max_retries + 2):
            target_path.write_text(code, encoding="utf-8")
            output = run_in_dir(run_command, cwd=tmp_path, timeout=60)

            stdout_part = ""
            stderr_part = ""
            if "STDOUT:\n" in output:
                stdout_part = output.split("STDOUT:\n", 1)[1].split("\n\nSTDERR:\n")[0]
            if "STDERR:\n" in output:
                stderr_part = output.split("STDERR:\n", 1)[1]

            returncode = 0 if "Traceback" not in output and "Error" not in stderr_part else 1
            ok = ok_check(stdout_part, stderr_part, returncode)

            history.append(
                {
                    "attempt": attempt,
                    "code": code,
                    "run_output": output,
                    "ok": ok,
                }
            )

            if ok:
                destination = GENERATED_DIR / Path(target_file).name
                shutil.copy2(target_path, destination)
                return code, output, history

            if attempt >= max_retries + 1:
                break

            fix_prompt = (
                f"Исправь код файла '{target_file}' после неудачного запуска.\n"
                "Верни только новый код без markdown.\n\n"
                f"Задача:\n{task}\n\n"
                f"Текущий код:\n{code}\n\n"
                f"Команда запуска:\n{run_command}\n\n"
                f"STDOUT:\n{stdout_part}\n\n"
                f"STDERR:\n{stderr_part}"
            )
            code = clean_code_fence(
                ask_model(
                    model_name,
                    "Программист",
                    fix_prompt,
                    project_context=project_context,
                    file_context=file_context,
                    temp=0.1,
                    include_history=False,
                    num_ctx=num_ctx,
                )
            )

    return code, history[-1]["run_output"] if history else "", history
