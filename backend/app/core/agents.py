"""
agents.py вЂ” РІСЃРµ Р°РіРµРЅС‚РЅС‹Рµ РјРѕРґСѓР»Рё.

РљР»СЋС‡РµРІС‹Рµ СѓР»СѓС‡С€РµРЅРёСЏ v7.1:
  вЂў execute_python_with_capture  вЂ” subprocess-РёР·РѕР»СЏС†РёСЏ + С‚Р°Р№РјР°СѓС‚ + matplotlib С‡РµСЂРµР· С„Р°Р№Р»С‹
  вЂў self_heal_python_code        вЂ” Р°РІС‚Рѕ-РёСЃРїСЂР°РІР»РµРЅРёРµ РґРѕ N РїРѕРїС‹С‚РѕРє
  вЂў run_build_loop               вЂ” РёР·РѕР»РёСЂРѕРІР°РЅРЅР°СЏ temp-dir, СѓР»СѓС‡С€РµРЅРЅС‹Р№ ok-check
  вЂў run_multi_agent              вЂ” РїСЂРѕРіСЂРµСЃСЃ-Р±Р°СЂ, РЅР°РґС‘Р¶РЅС‹Р№ fallback РїР»Р°РЅР°
  вЂў Browser / Terminal           вЂ” Р±РµР· РёР·РјРµРЅРµРЅРёР№
"""
# Legacy monolith: keep behavior stable and prefer extraction into
# smaller modules over new feature work in this file.
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import textwrap
from pathlib import Path
from typing import List, Dict, Any, Tuple
from urllib.parse import urljoin, urlparse, quote_plus
from uuid import uuid4

from .config import APP_DIR, TERMINAL_BLOCKED, GENERATED_DIR, OUTPUT_DIR, IMAGE_MODEL_ID
from .files import truncate_text
from .llm import ask_model, clean_code_fence, safe_json_parse

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# PYTHON LAB вЂ” РёР·РѕР»РёСЂРѕРІР°РЅРЅС‹Р№ subprocess
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

_PYTHON_EXEC_TIMEOUT = 30   # СЃРµРєСѓРЅРґ РЅР° РІС‹РїРѕР»РЅРµРЅРёРµ РєРѕРґР°

# Р’СЂР°РїРїРµСЂ РєРѕС‚РѕСЂС‹Р№ СЃРѕС…СЂР°РЅСЏРµС‚ matplotlib-С„РёРіСѓСЂС‹ РєР°Рє PNG РІ СЂР°Р±РѕС‡СѓСЋ РїР°РїРєСѓ
_FIGURE_SAVER = textwrap.dedent("""
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
""")


def execute_python_with_capture(
    code: str,
    extra_globals: dict = None,
    timeout: int = _PYTHON_EXEC_TIMEOUT,
) -> Dict[str, Any]:
    """
    Р—Р°РїСѓСЃРєР°РµС‚ Python-РєРѕРґ РІ РёР·РѕР»РёСЂРѕРІР°РЅРЅРѕРј subprocess.
    Р’РѕР·РІСЂР°С‰Р°РµС‚: {ok, output, traceback, figures}
      figures вЂ” СЃРїРёСЃРѕРє bytes (PNG) РґР»СЏ st.image()

    РР·РѕР»СЏС†РёСЏ РіР°СЂР°РЅС‚РёСЂСѓРµС‚:
      - Р±РµСЃРєРѕРЅРµС‡РЅС‹Р№ С†РёРєР» в†’ СѓР±РёРІР°РµС‚СЃСЏ РїРѕ С‚Р°Р№РјР°СѓС‚Сѓ
      - sys.exit() / os._exit() в†’ РЅРµ СЂРѕРЅСЏРµС‚ Streamlit
      - РёРјРїРѕСЂС‚ С‚СЏР¶С‘Р»С‹С… Р»РёР± в†’ РѕС‚РґРµР»СЊРЅС‹Р№ РїСЂРѕС†РµСЃСЃ, РЅРµ Р·Р°СЃРѕСЂСЏРµС‚ РїР°РјСЏС‚СЊ
    """
    with tempfile.TemporaryDirectory(prefix="elira_exec_") as tmp:
        tmp_path  = Path(tmp)
        code_file = tmp_path / "_run.py"

        # Р•СЃР»Рё extra_globals СЃРѕРґРµСЂР¶Р°С‚ РґР°РЅРЅС‹Рµ вЂ” СЃРµСЂРёР°Р»РёР·СѓРµРј Рё РїРµСЂРµРґР°С‘Рј
        prelude = _FIGURE_SAVER
        if extra_globals:
            serializable = {}
            for k, v in extra_globals.items():
                try:
                    json.dumps(v)   # С‚РѕР»СЊРєРѕ JSON-СЃРµСЂРёР°Р»РёР·СѓРµРјС‹Рµ
                    serializable[k] = v
                except Exception:
                    pass
            if serializable:
                prelude += (
                    f"\nimport json as _json\n"
                    f"_injected = _json.loads({repr(json.dumps(serializable))})\n"
                    f"globals().update(_injected)\n"
                )

        full_code = prelude + "\n" + code
        code_file.write_text(full_code, encoding="utf-8")

        env = os.environ.copy()
        env["_FIG_DIR"] = str(tmp_path)
        env["MPLBACKEND"] = "Agg"

        try:
            proc = subprocess.run(
                [sys.executable, str(code_file)],
                capture_output=True, text=True,
                timeout=timeout, cwd=str(tmp_path), env=env,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            # Р Р°Р·РґРµР»СЏРµРј РЅР°СЃС‚РѕСЏС‰РёРµ РѕС€РёР±РєРё РѕС‚ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёР№
            is_error = proc.returncode != 0 or "Traceback" in stderr

            # РЎРѕР±РёСЂР°РµРј PNG-С„РёРіСѓСЂС‹
            figures = []
            for fig_file in sorted(tmp_path.glob("fig_*.png")):
                try:
                    figures.append(fig_file.read_bytes())
                except Exception:
                    pass

            return {
                "ok":        not is_error,
                "output":    stdout or ("РљРѕРґ РІС‹РїРѕР»РЅРµРЅ Р±РµР· РІС‹РІРѕРґР°" if not is_error else ""),
                "traceback": stderr if is_error else "",
                "warnings":  stderr if not is_error and stderr else "",
                "figures":   figures,
            }

        except subprocess.TimeoutExpired:
            return {
                "ok":        False,
                "output":    "",
                "traceback": f"вЏ± РџСЂРµРІС‹С€РµРЅ С‚Р°Р№РјР°СѓС‚ РІС‹РїРѕР»РЅРµРЅРёСЏ ({timeout} СЃРµРє). "
                             f"РџСЂРѕРІРµСЂСЊ РЅРµС‚ Р»Рё Р±РµСЃРєРѕРЅРµС‡РЅРѕРіРѕ С†РёРєР»Р°.",
                "warnings":  "",
                "figures":   [],
            }
        except Exception as e:
            return {
                "ok": False, "output": "", "traceback": str(e),
                "warnings": "", "figures": [],
            }


def self_heal_python_code(
    generated_code: str,
    task: str,
    file_path: str,
    schema_text: str,
    model_name: str,
    max_retries: int = 2,
    num_ctx: int = 4096,
) -> Tuple[str, Dict, List]:
    """
    Р—Р°РїСѓСЃРєР°РµС‚ РєРѕРґ, РїСЂРё РѕС€РёР±РєРµ РїСЂРѕСЃРёС‚ РјРѕРґРµР»СЊ РёСЃРїСЂР°РІРёС‚СЊ, РїРѕРІС‚РѕСЂСЏРµС‚ РґРѕ max_retries СЂР°Р·.
    Р’РѕР·РІСЂР°С‰Р°РµС‚: (РёС‚РѕРіРѕРІС‹Р№_РєРѕРґ, РїРѕСЃР»РµРґРЅРёР№_СЂРµР·СѓР»СЊС‚Р°С‚, РёСЃС‚РѕСЂРёСЏ_РїРѕРїС‹С‚РѕРє)
    """
    history, current_code, last_result = [], generated_code, None

    for attempt in range(1, max_retries + 2):
        result = execute_python_with_capture(current_code)
        history.append({
            "attempt":   attempt,
            "code":      current_code,
            "ok":        result["ok"],
            "output":    result["output"],
            "traceback": result["traceback"],
        })
        last_result = result

        if result["ok"]:
            return current_code, result, history
        if attempt >= max_retries + 1:
            break

        repair_prompt = (
            f"РўС‹ РёСЃРїСЂР°РІР»СЏРµС€СЊ Python-РєРѕРґ РїРѕСЃР»Рµ РѕС€РёР±РєРё РІС‹РїРѕР»РЅРµРЅРёСЏ.\n"
            f"Р’РµСЂРЅРё С‚РѕР»СЊРєРѕ РёСЃРїСЂР°РІР»РµРЅРЅС‹Р№ Python-РєРѕРґ Р±РµР· markdown Рё РїРѕСЏСЃРЅРµРЅРёР№.\n\n"
            f"РџСѓС‚СЊ Рє С„Р°Р№Р»Сѓ РґР°РЅРЅС‹С…:\n{file_path}\n\n"
            f"Р—Р°РґР°С‡Р°:\n{task}\n\n"
            f"РЎС…РµРјР° РґР°РЅРЅС‹С…:\n{schema_text}\n\n"
            f"РўРµРєСѓС‰РёР№ РєРѕРґ:\n{current_code}\n\n"
            f"STDOUT:\n{result['output']}\n\n"
            f"TRACEBACK:\n{result['traceback']}\n\n"
            f"РСЃРїСЂР°РІСЊ РєРѕРґ С‚Р°Рє, С‡С‚РѕР±С‹ РѕРЅ РІС‹РїРѕР»РЅРёР»СЃСЏ СѓСЃРїРµС€РЅРѕ. "
            f"РќРµ РёСЃРїРѕР»СЊР·СѓР№ markdown, РІРµСЂРЅРё С‚РѕР»СЊРєРѕ С‡РёСЃС‚С‹Р№ Python."
        )
        fixed = ask_model(
            model_name=model_name, profile_name="РђРЅР°Р»РёС‚РёРє",
            user_input=repair_prompt, temp=0.1,
            include_history=False, num_ctx=num_ctx,
        ).strip()
        current_code = clean_code_fence(fixed)

    return current_code, last_result, history


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# CODE BUILDER LOOP вЂ” РёР·РѕР»РёСЂРѕРІР°РЅРЅР°СЏ temp-РґРёСЂРµРєС‚РѕСЂРёСЏ
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def generate_file_code(
    target_file: str, task: str, model_name: str,
    project_context: str, file_context: str, num_ctx: int = 4096,
) -> str:
    prompt = (
        f"РќР°РїРёС€Рё РїРѕР»РЅС‹Р№ СЂР°Р±РѕС‡РёР№ РєРѕРґ РґР»СЏ С„Р°Р№Р»Р° {target_file}.\n"
        f"Р’РµСЂРЅРё С‚РѕР»СЊРєРѕ СЃРѕРґРµСЂР¶РёРјРѕРµ С„Р°Р№Р»Р° Р±РµР· markdown.\n\n"
        f"Р—Р°РґР°С‡Р°:\n{task}\n\n"
        f"РљРѕРЅС‚РµРєСЃС‚ РїСЂРѕРµРєС‚Р°:\n{project_context[:20000]}\n\n"
        f"РљРѕРЅС‚РµРєСЃС‚ С„Р°Р№Р»РѕРІ:\n{file_context[:8000]}"
    )
    code = ask_model(
        model_name, "РџСЂРѕРіСЂР°РјРјРёСЃС‚", prompt,
        project_context=project_context, file_context=file_context,
        include_history=False, num_ctx=num_ctx,
    )
    return clean_code_fence(code)


def _ok_check(stdout: str, stderr: str, returncode: int) -> bool:
    """
    РЎС‡РёС‚Р°РµС‚ Р·Р°РїСѓСЃРє СѓСЃРїРµС€РЅС‹Рј РµСЃР»Рё:
      - returncode == 0
      - РЅРµС‚ Traceback РІ stderr
      - РЅРµС‚ 'Error:' РІ РЅР°С‡Р°Р»Рµ СЃС‚СЂРѕРєРё stderr
    РџСЂРµРґСѓРїСЂРµР¶РґРµРЅРёСЏ (DeprecationWarning, UserWarning) РќР• СЃС‡РёС‚Р°СЋС‚СЃСЏ РѕС€РёР±РєРѕР№.
    """
    if returncode != 0:
        return False
    if "Traceback (most recent call last)" in stderr:
        return False
    # РћС€РёР±РєРё С‚РёРїР° "ModuleNotFoundError: ..." Р±РµР· Traceback
    error_lines = [l for l in stderr.splitlines()
                   if re.match(r"^\w*Error:", l.strip())]
    return len(error_lines) == 0


def run_build_loop(
    target_file: str,
    task: str,
    run_command: str,
    model_name: str,
    max_retries: int,
    project_context: str,
    file_context: str,
    num_ctx: int = 4096,
) -> Tuple[str, str, List]:
    """
    Р“РµРЅРµСЂРёСЂСѓРµС‚ РєРѕРґ, Р·Р°РїСѓСЃРєР°РµС‚ РІ РёР·РѕР»РёСЂРѕРІР°РЅРЅРѕР№ temp-РґРёСЂРµРєС‚РѕСЂРёРё, РїСЂРё РѕС€РёР±РєРµ вЂ” С‡РёРЅРёС‚.

    РР·РѕР»СЏС†РёСЏ: РєРѕРґ РїРёС€РµС‚СЃСЏ РІ tempdir, РЅРµ РІ APP_DIR.
    РџРѕСЃР»Рµ СѓСЃРїРµС…Р° вЂ” РєРѕРїРёСЂСѓРµС‚СЃСЏ РІ GENERATED_DIR.
    """
    history = []

    # Р“РµРЅРµСЂРёСЂСѓРµРј РЅР°С‡Р°Р»СЊРЅС‹Р№ РєРѕРґ
    code = generate_file_code(
        target_file, task, model_name,
        project_context, file_context, num_ctx,
    )

    with tempfile.TemporaryDirectory(prefix="elira_build_") as tmp:
        tmp_path    = Path(tmp)
        target_path = tmp_path / Path(target_file).name

        for attempt in range(1, max_retries + 2):
            # РџРёС€РµРј РєРѕРґ РІ temp-РґРёСЂРµРєС‚РѕСЂРёСЋ
            target_path.write_text(code, encoding="utf-8")

            # Р—Р°РїСѓСЃРєР°РµРј РєРѕРјР°РЅРґСѓ РІ temp-РґРёСЂРµРєС‚РѕСЂРёРё
            output = _run_in_dir(run_command, cwd=tmp_path, timeout=60)

            # РџР°СЂСЃРёРј stdout / stderr РёР· РІС‹РІРѕРґР° run_terminal
            stdout_part = ""
            stderr_part = ""
            if "STDOUT:\n" in output:
                stdout_part = output.split("STDOUT:\n", 1)[1].split("\n\nSTDERR:\n")[0]
            if "STDERR:\n" in output:
                stderr_part = output.split("STDERR:\n", 1)[1]

            # РџРѕР»СѓС‡Р°РµРј returncode (run_terminal РЅРµ РІРѕР·РІСЂР°С‰Р°РµС‚ РµРіРѕ, СЌРІСЂРёСЃС‚РёРєР°)
            returncode = 0 if "Traceback" not in output and "Error" not in stderr_part else 1

            ok = _ok_check(stdout_part, stderr_part, returncode)

            history.append({
                "attempt":    attempt,
                "code":       code,
                "run_output": output,
                "ok":         ok,
            })

            if ok:
                # РљРѕРїРёСЂСѓРµРј СЂРµР·СѓР»СЊС‚Р°С‚ РІ GENERATED_DIR
                dest = GENERATED_DIR / Path(target_file).name
                shutil.copy2(target_path, dest)
                return code, output, history

            if attempt >= max_retries + 1:
                break

            # РџСЂРѕСЃРёРј РјРѕРґРµР»СЊ РїРѕС‡РёРЅРёС‚СЊ
            fix_prompt = (
                f"РСЃРїСЂР°РІСЊ РєРѕРґ С„Р°Р№Р»Р° '{target_file}' РїРѕСЃР»Рµ РЅРµСѓРґР°С‡РЅРѕРіРѕ Р·Р°РїСѓСЃРєР°.\n"
                f"Р’РµСЂРЅРё С‚РѕР»СЊРєРѕ РЅРѕРІС‹Р№ РєРѕРґ Р±РµР· markdown.\n\n"
                f"Р—Р°РґР°С‡Р°:\n{task}\n\n"
                f"РўРµРєСѓС‰РёР№ РєРѕРґ:\n{code}\n\n"
                f"РљРѕРјР°РЅРґР° Р·Р°РїСѓСЃРєР°:\n{run_command}\n\n"
                f"STDOUT:\n{stdout_part}\n\n"
                f"STDERR:\n{stderr_part}"
            )
            code = clean_code_fence(
                ask_model(
                    model_name, "РџСЂРѕРіСЂР°РјРјРёСЃС‚", fix_prompt,
                    project_context=project_context, file_context=file_context,
                    temp=0.1, include_history=False, num_ctx=num_ctx,
                )
            )

    return code, history[-1]["run_output"] if history else "", history


def _run_in_dir(cmd: str, cwd: Path, timeout: int = 60) -> str:
    """Р—Р°РїСѓСЃРєР°РµС‚ РєРѕРјР°РЅРґСѓ РІ СѓРєР°Р·Р°РЅРЅРѕР№ РґРёСЂРµРєС‚РѕСЂРёРё."""
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(cwd),
        )
        return f"$ {cmd}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n\nРљРѕРјР°РЅРґР° РѕСЃС‚Р°РЅРѕРІР»РµРЅР° РїРѕ С‚Р°Р№РјР°СѓС‚Сѓ ({timeout} СЃРµРє.)"
    except Exception as e:
        return f"РћС€РёР±РєР° Р·Р°РїСѓСЃРєР°: {e}"


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# MULTI-AGENT ORCHESTRATOR
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def _run_multi_agent_legacy_pipeline(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,  # callable(step: int, total: int, label: str)
    project_context: str = "",
    file_context: str = "",
) -> Dict[str, Any]:
    """
    Р—Р°РїСѓСЃРєР°РµС‚ 5-С€Р°РіРѕРІС‹Р№ Multi-Agent pipeline:
      1. Planner     в†’ СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅС‹Р№ РїР»Р°РЅ (JSON)
      2. Researcher  в†’ РёСЃСЃР»РµРґРѕРІР°РЅРёРµ Рё С„Р°РєС‚С‹
      3. Coder       в†’ РєРѕРґ Рё СЂРµР°Р»РёР·Р°С†РёСЏ
      4. Reviewer    в†’ РїСЂРѕРІРµСЂРєР° Рё РєСЂРёС‚РёРєР°
      5. Orchestratorв†’ С„РёРЅР°Р»СЊРЅС‹Р№ deliverable

    progress_callback РІС‹Р·С‹РІР°РµС‚СЃСЏ РїРѕСЃР»Рµ РєР°Р¶РґРѕРіРѕ С€Р°РіР° вЂ” РёСЃРїРѕР»СЊР·СѓР№ РґР»СЏ st.progress().
    """
    from .memory import build_memory_context
    memory_context = build_memory_context(task, memory_profile, top_k=5)

    total_steps = 5
    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    # в”Ђв”Ђ РЁР°Рі 1: Planner в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    _progress(1, "рџ—є Planner: СЃРѕСЃС‚Р°РІР»СЏСЋ РїР»Р°РЅ...")
    planner_prompt = (
        "РўС‹ Planner Р°РіРµРЅС‚. Р Р°Р·Р±РµР№ Р·Р°РґР°С‡Сѓ РЅР° 4 Р±Р»РѕРєР° Рё РІРµСЂРЅРё РўРћР›Р¬РљРћ JSON Р±РµР· РїРѕСЏСЃРЅРµРЅРёР№:\n"
        '{"research":"<С‡С‚Рѕ РёСЃСЃР»РµРґРѕРІР°С‚СЊ>","coding":"<С‡С‚Рѕ РЅР°РїРёСЃР°С‚СЊ>","review":"<С‡С‚Рѕ РїСЂРѕРІРµСЂРёС‚СЊ>",'
        '"deliverable":"<РёС‚РѕРіРѕРІС‹Р№ СЂРµР·СѓР»СЊС‚Р°С‚>"}\n\n'
        f"Р—Р°РґР°С‡Р°:\n{task}"
    )
    raw_plan = ask_model(
        model_name=model_name, profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=planner_prompt, memory_context=memory_context,
        use_memory=True, temp=0.05, include_history=False, num_ctx=num_ctx,
    )
    plan = safe_json_parse(raw_plan)

    # РќР°РґС‘Р¶РЅС‹Р№ fallback: РµСЃР»Рё РїР»Р°РЅ РЅРµ СЂР°СЃРїР°СЂСЃРёР»СЃСЏ вЂ” СЃРѕР·РґР°С‘Рј РёР· Р·Р°РґР°С‡Рё
    if not isinstance(plan, dict) or not plan.get("research"):
        plan = {
            "research":    f"РСЃСЃР»РµРґСѓР№ С‚РµРјСѓ: {task[:500]}",
            "coding":      f"РќР°РїРёС€Рё РєРѕРґ РёР»Рё СЂРµС€РµРЅРёРµ РґР»СЏ: {task[:500]}",
            "review":      f"РџСЂРѕРІРµСЂСЊ РєРѕСЂСЂРµРєС‚РЅРѕСЃС‚СЊ СЂРµС€РµРЅРёСЏ Р·Р°РґР°С‡Рё: {task[:500]}",
            "deliverable": "РЎРѕР±РµСЂРё РёС‚РѕРі: РєСЂР°С‚РєРёР№ РІС‹РІРѕРґ + РїСЂР°РєС‚РёС‡РµСЃРєРёРµ С€Р°РіРё + РєРѕРґ РµСЃР»Рё РЅСѓР¶РµРЅ",
        }

    # в”Ђв”Ђ РЁР°Рі 2: Researcher в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    _progress(2, "рџ”¬ Researcher: РёСЃСЃР»РµРґСѓСЋ...")
    research = ask_model(
        model_name=model_name, profile_name="РСЃСЃР»РµРґРѕРІР°С‚РµР»СЊ",
        user_input=plan["research"],
        memory_context=memory_context, use_memory=True,
        include_history=False, num_ctx=num_ctx,
    )

    # в”Ђв”Ђ РЁР°Рі 3: Coder в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    _progress(3, "рџ‘ЁвЂЌрџ’» Coder: РїРёС€Сѓ СЂРµС€РµРЅРёРµ...")
    coding_input = (
        f"{plan['coding']}\n\n"
        f"РљРѕРЅС‚РµРєСЃС‚ РёСЃСЃР»РµРґРѕРІР°РЅРёСЏ:\n{research[:3000]}"
    )
    coding = ask_model(
        model_name=model_name, profile_name="РџСЂРѕРіСЂР°РјРјРёСЃС‚",
        user_input=coding_input,
        project_context=project_context,
        file_context=file_context,
        memory_context=memory_context, use_memory=True,
        include_history=False, num_ctx=num_ctx,
    )

    # в”Ђв”Ђ РЁР°Рі 4: Reviewer в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    _progress(4, "рџ”Ќ Reviewer: РїСЂРѕРІРµСЂСЏСЋ...")
    review_input = (
        f"РџСЂРѕРІРµСЂСЊ СЂРµР·СѓР»СЊС‚Р°С‚С‹ Research Рё Coding Р°РіРµРЅС‚РѕРІ.\n\n"
        f"РРЎРҐРћР”РќРђРЇ Р—РђР”РђР§Рђ:\n{task}\n\n"
        f"PLAN: {plan['review']}\n\n"
        f"RESEARCH:\n{research[:2000]}\n\n"
        f"CODING:\n{coding[:2000]}\n\n"
        f"РЈРєР°Р¶Рё: С‡С‚Рѕ РІРµСЂРЅРѕ, С‡С‚Рѕ СЃРѕРјРЅРёС‚РµР»СЊРЅРѕ, С‡С‚Рѕ РЅСѓР¶РЅРѕ СѓР»СѓС‡С€РёС‚СЊ."
    )
    review = ask_model(
        model_name=model_name, profile_name="РђРЅР°Р»РёС‚РёРє",
        user_input=review_input,
        memory_context=memory_context, use_memory=True,
        include_history=False, num_ctx=num_ctx,
    )

    # в”Ђв”Ђ РЁР°Рі 5: Orchestrator в†’ С„РёРЅР°Р»СЊРЅС‹Р№ deliverable в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    _progress(5, "рџЋЇ Orchestrator: СЃРѕР±РёСЂР°СЋ РёС‚РѕРі...")
    final_input = (
        f"РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ deliverable РЅР° РѕСЃРЅРѕРІРµ СЂР°Р±РѕС‚С‹ РІСЃРµС… Р°РіРµРЅС‚РѕРІ.\n\n"
        f"Р—РђР”РђР§Рђ:\n{task}\n\n"
        f"RESEARCH:\n{research[:1500]}\n\n"
        f"CODING:\n{coding[:1500]}\n\n"
        f"REVIEW:\n{review[:1000]}\n\n"
        f"РўР Р•Р‘РћР’РђРќРР• Рљ DELIVERABLE: {plan['deliverable']}\n\n"
        f"РЎС‚СЂСѓРєС‚СѓСЂР° РѕС‚РІРµС‚Р°:\n"
        f"1. РљСЂР°С‚РєРёР№ РІС‹РІРѕРґ (2-3 РїСЂРµРґР»РѕР¶РµРЅРёСЏ)\n"
        f"2. РћСЃРЅРѕРІРЅР°СЏ С‡Р°СЃС‚СЊ (РєРѕРґ / Р°РЅР°Р»РёР· / СЂРµС€РµРЅРёРµ)\n"
        f"3. РџСЂР°РєС‚РёС‡РµСЃРєРёРµ СЃР»РµРґСѓСЋС‰РёРµ С€Р°РіРё"
    )
    final_draft = ask_model(
        model_name=model_name, profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=final_input,
        include_history=False, num_ctx=num_ctx,
    )
    reflected = reflect_and_improve_answer(task, final_draft, model_name, extra_context=research[:3000] + "\n\n" + review[:2000], num_ctx=num_ctx)

    from .memory import record_tool_usage
    record_tool_usage("multi_agent", task, True, score=1.4, notes="5-step orchestration + reflection", profile_name=memory_profile)

    return {
        "plan":     plan,
        "research": research,
        "coding":   coding,
        "review":   review,
        "final":    reflected["final"],
        "reflection": reflected["critique"],
    }





# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# SELF-REFLECTION LOOP
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def reflect_and_improve_answer(
    task: str,
    draft: str,
    model_name: str,
    profile_name: str = "РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
    extra_context: str = "",
    num_ctx: int = 4096,
) -> Dict[str, str]:
    critic_prompt = (
        "РўС‹ reflection-critic. РљРѕСЂРѕС‚РєРѕ РїСЂРѕРІРµСЂСЊ С‡РµСЂРЅРѕРІРёРє РѕС‚РІРµС‚Р° РЅР°: РїРѕР»РЅРѕС‚Сѓ, С„Р°РєС‚РёС‡РµСЃРєСѓСЋ РѕРїРѕСЂСѓ РЅР° РєРѕРЅС‚РµРєСЃС‚, "
        "РїРѕР»РµР·РЅРѕСЃС‚СЊ Рё РєРѕРЅРєСЂРµС‚РЅРѕСЃС‚СЊ. Р’РµСЂРЅРё РўРћР›Р¬РљРћ JSON Р±РµР· markdown: "
        '{"score":0-10,"issues":["..."],"improve":"yes|no","brief":"..."}.\n\n'
        f"Р—РђР”РђР§Рђ:\n{task}\n\n"
        f"РљРћРќРўР•РљРЎРў:\n{extra_context[:6000]}\n\n"
        f"Р§Р•Р РќРћР’РРљ:\n{draft[:8000]}"
    )
    raw = ask_model(
        model_name=model_name,
        profile_name="РђРЅР°Р»РёС‚РёРє",
        user_input=critic_prompt,
        include_history=False,
        temp=0.05,
        num_ctx=min(num_ctx, 4096),
    )
    critique = safe_json_parse(clean_code_fence(raw))
    if not isinstance(critique, dict):
        critique = {"score": 7, "issues": ["РќРµ СѓРґР°Р»РѕСЃСЊ СЂР°СЃРїР°СЂСЃРёС‚СЊ critique"], "improve": "yes", "brief": str(raw)[:500]}

    improve_flag = str(critique.get("improve", "yes")).lower() == "yes" or float(critique.get("score", 7)) < 8
    improved = draft
    if improve_flag:
        improve_prompt = (
            "РЈР»СѓС‡С€Рё РѕС‚РІРµС‚ РїРѕСЃР»Рµ self-reflection. РЎРѕС…СЂР°РЅРё С„Р°РєС‚С‹, СѓР±РµСЂРё СЃР»Р°Р±С‹Рµ РјРµСЃС‚Р°, СЃРґРµР»Р°Р№ РѕС‚РІРµС‚ С‚РѕС‡РЅРµРµ Рё РїСЂР°РєС‚РёС‡РЅРµРµ.\n\n"
            f"Р—РђР”РђР§Рђ:\n{task}\n\n"
            f"РљРћРќРўР•РљРЎРў:\n{extra_context[:6000]}\n\n"
            f"Р§Р•Р РќРћР’РРљ:\n{draft[:8000]}\n\n"
            f"CRITIQUE:\n{json.dumps(critique, ensure_ascii=False, indent=2)}"
        )
        improved = ask_model(
            model_name=model_name,
            profile_name=profile_name,
            user_input=improve_prompt,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        )
    return {
        "draft": draft,
        "final": improved,
        "critique": json.dumps(critique, ensure_ascii=False, indent=2),
    }
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# PLANNER AGENT вЂ” orchestration РїРѕРІРµСЂС… Browser / Terminal / Reasoning
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def _extract_first_url(text: str) -> str:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import extract_first_url
    return extract_first_url(text)


def _planner_safe_terminal_command(cmd: str) -> bool:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import planner_safe_terminal_command
    return planner_safe_terminal_command(cmd)


def _planner_default_steps(task: str) -> List[dict]:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import planner_default_steps
    return planner_default_steps(task)


def run_planner_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import run_planner_agent as _run
    return _run(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=progress_callback)


def _run_planner_agent_frozen(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
    """Original inline implementation — frozen, will be removed."""
    from .memory import build_memory_context

    total_steps = 3
    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    memory_context = build_memory_context(task, memory_profile, top_k=8)

    _progress(1, "рџ§­ Planner: СЃС‚СЂРѕСЋ РїР»Р°РЅ...")
    planner_prompt = (
        "РўС‹ Planner agent. Р Р°Р·Р±РµР№ Р·Р°РґР°С‡Сѓ РЅР° 2-4 С€Р°РіР° Рё РІРµСЂРЅРё РўРћР›Р¬РљРћ JSON-РјР°СЃСЃРёРІ Р±РµР· РїРѕСЏСЃРЅРµРЅРёР№.\n"
        "Р¤РѕСЂРјР°С‚ С€Р°РіР°:\n"
        '[{"tool":"browser|terminal|reasoning","goal":"...","url":"optional","command":"optional"}]\n'
        "РџСЂР°РІРёР»Р°:\n"
        "- browser: С‚РѕР»СЊРєРѕ РµСЃР»Рё РЅСѓР¶РµРЅ РІРµР±/СЃС‚СЂР°РЅРёС†Р°/РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ/РїРѕРёСЃРє.\n"
        "- terminal: С‚РѕР»СЊРєРѕ РґР»СЏ Р‘Р•Р—РћРџРђРЎРќР«РҐ read-only РєРѕРјР°РЅРґ Р»РѕРєР°Р»СЊРЅРѕРіРѕ Р°РЅР°Р»РёР·Р°.\n"
        "- reasoning: С„РёРЅР°Р»СЊРЅС‹Р№ Р°РЅР°Р»РёС‚РёС‡РµСЃРєРёР№ С€Р°Рі.\n"
        "- РќРµ РїСЂРµРґР»Р°РіР°Р№ РѕРїР°СЃРЅС‹Рµ РєРѕРјР°РЅРґС‹.\n"
        "- Р•СЃР»Рё РІ Р·Р°РґР°С‡Рµ РµСЃС‚СЊ URL, РёСЃРїРѕР»СЊР·СѓР№ РµРіРѕ РІ browser step.\n"
        "- РџРѕСЃР»РµРґРЅРёР№ С€Р°Рі РІСЃРµРіРґР° reasoning.\n\n"
        f"Р—Р°РґР°С‡Р°:\n{task}"
    )
    raw_plan = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=planner_prompt,
        memory_context=memory_context,
        use_memory=True,
        temp=0.05,
        include_history=False,
        num_ctx=num_ctx,
    )
    raw_plan = clean_code_fence(re.sub(r"^```json\s*", "", (raw_plan or "").strip()))
    plan = safe_json_parse(raw_plan)
    if not isinstance(plan, list):
        plan = _planner_default_steps(task)

    normalized_plan = []
    for item in plan[:4]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool", "")).strip().lower()
        if tool not in {"browser", "terminal", "reasoning"}:
            continue
        normalized_plan.append({
            "tool": tool,
            "goal": str(item.get("goal", "")).strip() or task[:400],
            "url": str(item.get("url", "")).strip(),
            "command": str(item.get("command", "")).strip(),
        })
    if not normalized_plan:
        normalized_plan = _planner_default_steps(task)
    if normalized_plan[-1]["tool"] != "reasoning":
        normalized_plan.append({"tool": "reasoning", "goal": "РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ РѕС‚РІРµС‚ РїРѕ РІСЃРµРј РЅР°Р±Р»СЋРґРµРЅРёСЏРј.", "url": "", "command": ""})
    normalized_plan = normalized_plan[:5]

    _progress(2, "рџ›  Planner: РІС‹РїРѕР»РЅСЏСЋ С€Р°РіРё...")
    steps_log: List[dict] = []
    gathered_contexts: List[str] = []

    for idx, step in enumerate(normalized_plan, start=1):
        tool = step["tool"]
        if tool == "browser":
            url = step.get("url") or _extract_first_url(task)
            if not url:
                url = f"https://duckduckgo.com/?q={quote_plus(step['goal'][:200])}"
            result = run_browser_agent(url, step["goal"], max_pages=3)
            steps_log.append({
                "step": idx,
                "tool": tool,
                "goal": step["goal"],
                "url": url,
                "ok": result.get("ok", False),
                "trace": result.get("trace", []),
                "output": result.get("text", "")[:12000],
            })
            if result.get("ok"):
                gathered_contexts.append(f"[BROWSER]\nURL: {url}\nGOAL: {step['goal']}\n{result.get('text', '')[:12000]}")
                try:
                    persist_web_knowledge(
                        query=step["goal"],
                        web_context=result.get("text", ""),
                        profile_name=memory_profile,
                        source_kind="planner_browser",
                        url=url,
                        title=step["goal"],
                    )
                except Exception:
                    pass
        elif tool == "terminal":
            cmd = step.get("command", "")
            if not _planner_safe_terminal_command(cmd):
                steps_log.append({
                    "step": idx,
                    "tool": tool,
                    "goal": step["goal"],
                    "command": cmd,
                    "ok": False,
                    "output": "РЁР°Рі РїСЂРѕРїСѓС‰РµРЅ: РєРѕРјР°РЅРґР° РЅРµ РїСЂРѕС€Р»Р° safe-check planner-Р°.",
                })
                continue
            output = run_terminal(cmd, timeout=20)
            steps_log.append({
                "step": idx,
                "tool": tool,
                "goal": step["goal"],
                "command": cmd,
                "ok": True,
                "output": output[:12000],
            })
            gathered_contexts.append(f"[TERMINAL]\nGOAL: {step['goal']}\nCOMMAND: {cmd}\n{output[:8000]}")
        else:
            steps_log.append({
                "step": idx,
                "tool": tool,
                "goal": step["goal"],
                "ok": True,
                "output": "Reasoning step reserved for final synthesis.",
            })

    _progress(3, "рџЋЇ Planner: СЃРѕР±РёСЂР°СЋ РёС‚РѕРі...")
    context_blob = "\n\n".join(gathered_contexts)[:24000]
    final_prompt = (
        "РўС‹ Planner-Orchestrator. РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ РѕС‚РІРµС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ РЅР° РѕСЃРЅРѕРІРµ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рё "
        "Рё СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ С€Р°РіРѕРІ. Р•СЃР»Рё РґР°РЅРЅС‹С… РЅРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ, С‡РµСЃС‚РЅРѕ СѓРєР°Р¶Рё СЌС‚Рѕ.\n\n"
        f"Р—РђР”РђР§Рђ:\n{task}\n\n"
        f"РџР›РђРќ:\n{json.dumps(normalized_plan, ensure_ascii=False, indent=2)}\n\n"
        f"РљРћРќРўР•РљРЎРў РЁРђР“РћР’:\n{context_blob}\n\n"
        "РЎС‚СЂСѓРєС‚СѓСЂР°:\n"
        "1. РљСЂР°С‚РєРёР№ РёС‚РѕРі\n"
        "2. Р§С‚Рѕ СѓРґР°Р»РѕСЃСЊ СѓР·РЅР°С‚СЊ / СЃРґРµР»Р°С‚СЊ\n"
        "3. РџСЂР°РєС‚РёС‡РµСЃРєРёРµ СЃР»РµРґСѓСЋС‰РёРµ С€Р°РіРё"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=final_prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        num_ctx=num_ctx,
    )
    reflected = reflect_and_improve_answer(task, final_draft, model_name, extra_context=context_blob, num_ctx=num_ctx)
    from .memory import record_tool_usage
    record_tool_usage("planner_agent", task, True, score=1.2, notes="planner + execution + reflection", profile_name=memory_profile)

    return {
        "plan": normalized_plan,
        "steps": steps_log,
        "final": reflected["final"],
        "reflection": reflected["critique"],
    }




# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# TASK GRAPH вЂ” orchestration graph РїРѕРІРµСЂС… Browser / Terminal / Memory / Reasoning
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def _task_graph_default(task: str) -> List[dict]:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import task_graph_default
    return task_graph_default(task)


def _task_graph_default_frozen(task: str) -> List[dict]:
    """Original inline implementation — frozen, will be removed."""
    url = _extract_first_url(task)
    nodes: List[dict] = []

    if any(word in task.lower() for word in ["РІСЃРїРѕРјРЅРё", "РїР°РјСЏС‚СЊ", "С‡С‚Рѕ С‚С‹ Р·РЅР°РµС€СЊ", "РёР· РїР°РјСЏС‚Рё", "memory"]):
        nodes.append({
            "id": "n1",
            "tool": "memory_lookup",
            "goal": task[:400],
            "depends_on": [],
        })

    if url:
        nodes.append({
            "id": f"n{len(nodes)+1}",
            "tool": "browser",
            "goal": "РџСЂРѕС‡РёС‚Р°Р№ СЃС‚СЂР°РЅРёС†Сѓ Рё РёР·РІР»РµРєРё С„Р°РєС‚С‹, РїРѕР»РµР·РЅС‹Рµ РґР»СЏ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рё.",
            "url": url,
            "depends_on": [],
        })
    elif any(word in task.lower() for word in ["РЅР°Р№РґРё", "РїРѕРёСЃРє", "РІРµР±", "СЃР°Р№С‚", "РґРѕРєСѓРјРµРЅС‚Р°С†", "РёРЅС‚РµСЂРЅРµС‚", "СЃС‚СЂР°РЅРёС†"]):
        nodes.append({
            "id": f"n{len(nodes)+1}",
            "tool": "browser",
            "goal": task[:400],
            "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
            "depends_on": [],
        })

    nodes.append({
        "id": f"n{len(nodes)+1}",
        "tool": "reasoning",
        "goal": "РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ Р°РЅР°Р»РёС‚РёС‡РµСЃРєРёР№ РѕС‚РІРµС‚ РїРѕ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рµ.",
        "depends_on": [n["id"] for n in nodes],
    })
    return nodes[:6]


def _normalize_task_graph(raw_graph: Any, task: str) -> List[dict]:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import normalize_task_graph
    return normalize_task_graph(raw_graph, task)


def _normalize_task_graph_frozen(raw_graph: Any, task: str) -> List[dict]:
    """Original inline implementation — frozen, will be removed."""
    if not isinstance(raw_graph, list):
        return _task_graph_default(task)

    cleaned: List[dict] = []
    seen_ids = set()

    for idx, item in enumerate(raw_graph[:6], start=1):
        if not isinstance(item, dict):
            continue

        node_id = str(item.get("id", "")).strip() or f"n{idx}"
        if node_id in seen_ids:
            node_id = f"n{idx}"
        seen_ids.add(node_id)

        tool = str(item.get("tool", "")).strip().lower()
        if tool not in {"browser", "terminal", "reasoning", "memory_lookup"}:
            continue

        deps = item.get("depends_on", [])
        if not isinstance(deps, list):
            deps = []
        deps = [str(d).strip() for d in deps if str(d).strip() and str(d).strip() != node_id]

        cleaned.append({
            "id": node_id,
            "tool": tool,
            "goal": str(item.get("goal", "")).strip() or task[:400],
            "url": str(item.get("url", "")).strip(),
            "command": str(item.get("command", "")).strip(),
            "depends_on": deps,
        })

    if not cleaned:
        return _task_graph_default(task)

    if cleaned[-1]["tool"] != "reasoning":
        cleaned.append({
            "id": f"n{len(cleaned)+1}",
            "tool": "reasoning",
            "goal": "РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ Р°РЅР°Р»РёС‚РёС‡РµСЃРєРёР№ РѕС‚РІРµС‚ РїРѕ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рµ.",
            "url": "",
            "command": "",
            "depends_on": [n["id"] for n in cleaned],
        })

    valid_ids = {n["id"] for n in cleaned}
    for node in cleaned:
        node["depends_on"] = [d for d in node["depends_on"] if d in valid_ids and d != node["id"]]

    return cleaned[:7]


def make_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
) -> List[dict]:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import make_task_graph as _make
    return _make(task, model_name, memory_profile, num_ctx=num_ctx)


def _make_task_graph_frozen(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
) -> List[dict]:
    """Original inline implementation — frozen, will be removed."""
    from .memory import build_memory_context

    memory_context = build_memory_context(task, memory_profile, top_k=8)
    planner_prompt = (
        "РўС‹ СЃС‚СЂРѕРёС€СЊ task graph РґР»СЏ Р»РѕРєР°Р»СЊРЅРѕР№ AI-СЃРёСЃС‚РµРјС‹. "
        "Р’РµСЂРЅРё РўРћР›Р¬РљРћ JSON-РјР°СЃСЃРёРІ СѓР·Р»РѕРІ Р±РµР· РїРѕСЏСЃРЅРµРЅРёР№. "
        "РљР°Р¶РґС‹Р№ СѓР·РµР» РґРѕР»Р¶РµРЅ РёРјРµС‚СЊ С„РѕСЂРјР°С‚:\n"
        '[{"id":"n1","tool":"browser|terminal|reasoning|memory_lookup","goal":"...","url":"optional","command":"optional","depends_on":["n0"]}]\n\n'
        "РџСЂР°РІРёР»Р°:\n"
        "- РЈР·Р»С‹ РґРѕР»Р¶РЅС‹ Р±С‹С‚СЊ РєРѕСЂРѕС‚РєРёРјРё Рё РїСЂР°РєС‚РёС‡РЅС‹РјРё.\n"
        "- browser: С‚РѕР»СЊРєРѕ РµСЃР»Рё РЅСѓР¶РµРЅ РІРµР±/СЃР°Р№С‚/РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ/СЃС‚СЂР°РЅРёС†Р°.\n"
        "- terminal: С‚РѕР»СЊРєРѕ РґР»СЏ Р±РµР·РѕРїР°СЃРЅС‹С… read-only РєРѕРјР°РЅРґ Р»РѕРєР°Р»СЊРЅРѕРіРѕ Р°РЅР°Р»РёР·Р°.\n"
        "- memory_lookup: РєРѕРіРґР° РЅСѓР¶РЅРѕ РїРѕРґРЅСЏС‚СЊ СЂРµР»РµРІР°РЅС‚РЅСѓСЋ РїР°РјСЏС‚СЊ РїСЂРѕС„РёР»СЏ.\n"
        "- reasoning: РґР»СЏ Р°РЅР°Р»РёС‚РёРєРё Рё СЃРёРЅС‚РµР·Р°.\n"
        "- РџРѕСЃР»РµРґРЅРёР№ СѓР·РµР» РІСЃРµРіРґР° reasoning.\n"
        "- РњР°РєСЃРёРјСѓРј 6 СѓР·Р»РѕРІ.\n"
        "- РќРµ РїСЂРёРґСѓРјС‹РІР°Р№ РѕРїР°СЃРЅС‹Рµ РєРѕРјР°РЅРґС‹.\n\n"
        f"Р—Р°РґР°С‡Р°:\n{task}"
    )
    raw_graph = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=planner_prompt,
        memory_context=memory_context,
        use_memory=True,
        temp=0.05,
        include_history=False,
        num_ctx=num_ctx,
    )
    raw_graph = clean_code_fence(re.sub(r"^```json\s*", "", (raw_graph or "").strip()))
    parsed = safe_json_parse(raw_graph)
    return _normalize_task_graph(parsed, task)


def _task_graph_context_from_deps(node: dict, node_results: Dict[str, dict]) -> str:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import task_graph_context_from_deps
    return task_graph_context_from_deps(node, node_results)


def run_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
    """Facade — delegates to domain.agents.planner."""
    from app.domain.agents.planner import run_task_graph as _run
    return _run(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=progress_callback)


def _run_task_graph_frozen(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
    """Original inline implementation — frozen, will be removed."""
    from .memory import build_memory_context

    memory_context = build_memory_context(task, memory_profile, top_k=8)
    graph = make_task_graph(task, model_name, memory_profile, num_ctx=num_ctx)

    total_steps = max(len(graph) + 1, 2)
    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    node_results: Dict[str, dict] = {}
    execution_log: List[dict] = []
    remaining = list(graph)
    step_idx = 0

    while remaining:
        progressed = False
        for node in remaining[:]:
            deps = node.get("depends_on", [])
            if any(dep not in node_results for dep in deps):
                continue

            step_idx += 1
            _progress(step_idx, f"рџ•ё Р’С‹РїРѕР»РЅСЏСЋ {node['id']} В· {node['tool']}")
            dep_context = _task_graph_context_from_deps(node, node_results)
            tool = node["tool"]

            if tool == "browser":
                url = node.get("url") or _extract_first_url(task)
                if not url:
                    url = f"https://duckduckgo.com/?q={quote_plus(node['goal'][:200])}"
                result = run_browser_agent(url, node["goal"], max_pages=3)
                out = result.get("text", "")
                node_results[node["id"]] = {
                    "id": node["id"],
                    "tool": tool,
                    "goal": node["goal"],
                    "ok": result.get("ok", False),
                    "url": url,
                    "trace": result.get("trace", []),
                    "output": out[:15000],
                }
                if result.get("ok"):
                    try:
                        persist_web_knowledge(
                            query=node["goal"],
                            web_context=out,
                            profile_name=memory_profile,
                            source_kind="task_graph_browser",
                            url=url,
                            title=node["goal"],
                        )
                    except Exception:
                        pass

            elif tool == "terminal":
                cmd = node.get("command", "").strip()
                if not _planner_safe_terminal_command(cmd):
                    node_results[node["id"]] = {
                        "id": node["id"],
                        "tool": tool,
                        "goal": node["goal"],
                        "ok": False,
                        "command": cmd,
                        "output": "РЈР·РµР» РїСЂРѕРїСѓС‰РµРЅ: РєРѕРјР°РЅРґР° РЅРµ РїСЂРѕС€Р»Р° safe-check Task Graph.",
                    }
                else:
                    out = run_terminal(cmd, timeout=20)
                    node_results[node["id"]] = {
                        "id": node["id"],
                        "tool": tool,
                        "goal": node["goal"],
                        "ok": True,
                        "command": cmd,
                        "output": out[:12000],
                    }

            elif tool == "memory_lookup":
                lookup_q = node.get("goal") or task
                mem = build_memory_context(lookup_q, memory_profile, top_k=8)
                node_results[node["id"]] = {
                    "id": node["id"],
                    "tool": tool,
                    "goal": node["goal"],
                    "ok": True,
                    "output": mem[:12000] if mem else "Р РµР»РµРІР°РЅС‚РЅР°СЏ РїР°РјСЏС‚СЊ РЅРµ РЅР°Р№РґРµРЅР°.",
                }

            else:  # reasoning
                reasoning_prompt = (
                    "РўС‹ reasoning-node РІ task graph. Р’С‹РїРѕР»РЅРё С‚РѕР»СЊРєРѕ Р·Р°РґР°С‡Сѓ СЌС‚РѕРіРѕ СѓР·Р»Р°, "
                    "РѕРїРёСЂР°СЏСЃСЊ РЅР° РёСЃС…РѕРґРЅСѓСЋ Р·Р°РґР°С‡Сѓ Рё РєРѕРЅС‚РµРєСЃС‚ Р·Р°РІРёСЃРёРјРѕСЃС‚РµР№. "
                    "Р•СЃР»Рё РєРѕРЅС‚РµРєСЃС‚Р° РјР°Р»Рѕ вЂ” СЃРєР°Р¶Рё РѕР± СЌС‚РѕРј РїСЂСЏРјРѕ.\n\n"
                    f"РРЎРҐРћР”РќРђРЇ Р—РђР”РђР§Рђ:\n{task}\n\n"
                    f"Р—РђР”РђР§Рђ РЈР—Р›Рђ:\n{node['goal']}\n\n"
                    f"РљРћРќРўР•РљРЎРў Р—РђР’РРЎРРњРћРЎРўР•Р™:\n{dep_context or 'РќРµС‚ РєРѕРЅС‚РµРєСЃС‚Р° Р·Р°РІРёСЃРёРјРѕСЃС‚РµР№.'}"
                )
                out = ask_model(
                    model_name=model_name,
                    profile_name="РђРЅР°Р»РёС‚РёРє",
                    user_input=reasoning_prompt,
                    memory_context=memory_context,
                    use_memory=True,
                    include_history=False,
                    num_ctx=num_ctx,
                )
                node_results[node["id"]] = {
                    "id": node["id"],
                    "tool": tool,
                    "goal": node["goal"],
                    "ok": True,
                    "output": out[:15000],
                }

            execution_log.append(node_results[node["id"]])
            remaining.remove(node)
            progressed = True

        if not progressed:
            for node in remaining:
                node_results[node["id"]] = {
                    "id": node["id"],
                    "tool": node["tool"],
                    "goal": node["goal"],
                    "ok": False,
                    "output": "РЈР·РµР» РЅРµ РІС‹РїРѕР»РЅРµРЅ: Р·Р°РІРёСЃРёРјРѕСЃС‚Рё РЅРµ СЂР°Р·СЂРµС€РёР»РёСЃСЊ РёР»Рё РіСЂР°С„ РЅРµРєРѕСЂСЂРµРєС‚РµРЅ.",
                }
                execution_log.append(node_results[node["id"]])
            break

    # Auto-retry graph: РµСЃР»Рё browser/terminal С€Р°РіРё СѓРїР°Р»Рё вЂ” РїСЂРѕР±СѓРµРј fallback reasoning/safe browser
    retried = []
    for item in list(execution_log):
        if item.get("ok"):
            continue
        if item.get("tool") == "browser":
            retry_url = item.get("url") or f"https://duckduckgo.com/?q={quote_plus(item.get('goal', task)[:200])}"
            retry = run_browser_agent(retry_url, item.get("goal", task), max_pages=2)
            retry_node = {
                "id": f"{item['id']}_retry",
                "tool": "browser_retry",
                "goal": item.get("goal", task),
                "ok": retry.get("ok", False),
                "url": retry_url,
                "trace": retry.get("trace", []),
                "output": retry.get("text", "")[:12000],
            }
            retried.append(retry_node)
            if retry.get("ok"):
                try:
                    persist_web_knowledge(
                        query=item.get("goal", task),
                        web_context=retry.get("text", ""),
                        profile_name=memory_profile,
                        source_kind="task_graph_browser_retry",
                        url=retry_url,
                        title=item.get("goal", task),
                    )
                except Exception:
                    pass
        elif item.get("tool") == "terminal":
            retry_node = {
                "id": f"{item['id']}_retry",
                "tool": "reasoning_retry",
                "goal": item.get("goal", task),
                "ok": True,
                "output": "Terminal С€Р°Рі РЅРµ РїСЂРѕС€С‘Р» safe-check. Р”Р»СЏ СЃРѕС…СЂР°РЅРµРЅРёСЏ РїСЂРѕРіСЂРµСЃСЃР° РіСЂР°С„ РїРµСЂРµРєР»СЋС‡С‘РЅ РЅР° reasoning fallback.",
            }
            retried.append(retry_node)
    execution_log.extend(retried)

    _progress(total_steps, "рџЋЇ Task Graph: СЃРѕР±РёСЂР°СЋ РёС‚РѕРі...")
    state_blob = "\n\n".join(
        f"[{item['id']} В· {item['tool']} В· {'OK' if item.get('ok') else 'FAIL'}]\n"
        f"GOAL: {item.get('goal','')}\n"
        f"{truncate_text(str(item.get('output','')), 5000)}"
        for item in execution_log
    )[:30000]

    final_prompt = (
        "РўС‹ Task Graph Orchestrator. РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ РѕС‚РІРµС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ РЅР° РѕСЃРЅРѕРІРµ СЃРѕСЃС‚РѕСЏРЅРёСЏ РіСЂР°С„Р°. "
        "Р§РµСЃС‚РЅРѕ РѕС‚РјРµС‡Р°Р№, РµСЃР»Рё РєР°РєРѕР№-С‚Рѕ СѓР·РµР» РЅРµ СЃСЂР°Р±РѕС‚Р°Р» РёР»Рё РґР°РЅРЅС‹С… РЅРµ С…РІР°С‚РёР»Рѕ.\n\n"
        f"РРЎРҐРћР”РќРђРЇ Р—РђР”РђР§Рђ:\n{task}\n\n"
        f"Р“Р РђР¤:\n{json.dumps(graph, ensure_ascii=False, indent=2)}\n\n"
        f"STATE:\n{state_blob}\n\n"
        "РЎС‚СЂСѓРєС‚СѓСЂР° РѕС‚РІРµС‚Р°:\n"
        "1. РљСЂР°С‚РєРёР№ РёС‚РѕРі\n"
        "2. Р§С‚Рѕ СѓРґР°Р»РѕСЃСЊ СѓР·РЅР°С‚СЊ / СЃРґРµР»Р°С‚СЊ\n"
        "3. РћРіСЂР°РЅРёС‡РµРЅРёСЏ РёР»Рё СЃР±РѕРё\n"
        "4. РџСЂР°РєС‚РёС‡РµСЃРєРёРµ СЃР»РµРґСѓСЋС‰РёРµ С€Р°РіРё"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=final_prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        num_ctx=num_ctx,
    )
    reflected = reflect_and_improve_answer(task, final_draft, model_name, extra_context=state_blob, num_ctx=num_ctx)
    from .memory import record_tool_usage
    graph_ok = any(item.get("ok") for item in execution_log)
    record_tool_usage("task_graph", task, graph_ok, score=1.3 if graph_ok else 0.6, notes="task graph + auto-retry + reflection", profile_name=memory_profile)

    return {
        "graph": graph,
        "steps": execution_log,
        "final": reflected["final"],
        "reflection": reflected["critique"],
    }


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# IMAGE GENERATION вЂ” SDXL Turbo
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def _torch_gc():
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except Exception:
        pass


def _strip_ansi(text: str) -> str:
    text = text or ""
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text).strip()


def _contains_cyrillic(text: str) -> bool:
    return bool(re.search(r"[Рђ-РЇР°-СЏРЃС‘]", text or ""))


def prepare_image_prompt(
    prompt: str,
    model_name: str,
    auto_translate: bool = True,
    num_ctx: int = 2048,
) -> Dict[str, str]:
    """Р“РѕС‚РѕРІРёС‚ prompt РґР»СЏ image model.

    Р’РѕР·РІСЂР°С‰Р°РµС‚ dict:
      {original_prompt, final_prompt, translated, log, ok}
    РќРёРєРѕРіРґР° РЅРµ Р±СЂРѕСЃР°РµС‚ РёСЃРєР»СЋС‡РµРЅРёРµ РЅР°СЂСѓР¶Сѓ вЂ” РїСЂРё СЃР±РѕРµ РІРѕР·РІСЂР°С‰Р°РµС‚ РёСЃС…РѕРґРЅС‹Р№ prompt.
    """
    original = (prompt or "").strip()
    if not original:
        return {
            "ok": False,
            "original_prompt": "",
            "final_prompt": "",
            "translated": "false",
            "log": "РџСѓСЃС‚РѕР№ prompt.",
        }

    if not auto_translate:
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "false",
            "log": "РђРІС‚РѕРїРµСЂРµРІРѕРґ РѕС‚РєР»СЋС‡С‘РЅ.",
        }

    if not _contains_cyrillic(original):
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "false",
            "log": "РљРёСЂРёР»Р»РёС†Р° РЅРµ РЅР°Р№РґРµРЅР° вЂ” РёСЃРїРѕР»СЊР·СѓСЋ prompt РєР°Рє РµСЃС‚СЊ.",
        }

    try:
        translate_prompt = (
            "РџСЂРµРѕР±СЂР°Р·СѓР№ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРёР№ Р·Р°РїСЂРѕСЃ РІ РєРѕСЂРѕС‚РєРёР№ С‚РѕС‡РЅС‹Р№ Р°РЅРіР»РёР№СЃРєРёР№ prompt "
            "РґР»СЏ РіРµРЅРµСЂР°С†РёРё РёР·РѕР±СЂР°Р¶РµРЅРёСЏ РІ SDXL Turbo. "
            "РЎРѕС…СЂР°РЅРё СЃРјС‹СЃР». РќРµ РґРѕР±Р°РІР»СЏР№ РїРѕСЏСЃРЅРµРЅРёР№, РЅСѓРјРµСЂР°С†РёРё, markdown Рё РєР°РІС‹С‡РµРє. "
            "Р’РµСЂРЅРё С‚РѕР»СЊРєРѕ РѕРґРЅСѓ СЃС‚СЂРѕРєСѓ РіРѕС‚РѕРІРѕРіРѕ prompt.\n\n"
            f"Р—Р°РїСЂРѕСЃ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ:\n{original}"
        )
        translated = ask_model(
            model_name=model_name,
            profile_name="РђРЅР°Р»РёС‚РёРє",
            user_input=translate_prompt,
            include_history=False,
            num_ctx=num_ctx,
            temp=0.1,
        ).strip()
        translated = clean_code_fence(translated).strip().strip('"').strip("'")
        translated = re.sub(r"\s+", " ", translated)
        if not translated:
            raise ValueError("empty translation")
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": translated,
            "translated": "true",
            "log": f"RU в†’ EN: {translated}",
        }
    except Exception as e:
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "fallback",
            "log": f"РџРµСЂРµРІРѕРґ РЅРµ СЃСЂР°Р±РѕС‚Р°Р», РёСЃРїРѕР»СЊР·СѓСЋ РёСЃС…РѕРґРЅС‹Р№ prompt. РћС€РёР±РєР°: {e}",
        }


def stop_ollama_model(model_name: str) -> Dict[str, Any]:
    """РџС‹С‚Р°РµС‚СЃСЏ РІС‹РіСЂСѓР·РёС‚СЊ Р»РѕРєР°Р»СЊРЅСѓСЋ РјРѕРґРµР»СЊ Ollama РёР· VRAM РїРµСЂРµРґ РіРµРЅРµСЂР°С†РёРµР№."""
    name = (model_name or "").strip()
    if not name:
        return {"ok": True, "message": "РњРѕРґРµР»СЊ РЅРµ СѓРєР°Р·Р°РЅР° вЂ” РїСЂРѕРїСѓСЃРєР°СЋ РІС‹РіСЂСѓР·РєСѓ."}
    if "cloud" in name.lower():
        return {"ok": True, "message": f"{name} вЂ” РѕР±Р»Р°С‡РЅР°СЏ РјРѕРґРµР»СЊ, РІС‹РіСЂСѓР¶Р°С‚СЊ РЅРµС‡РµРіРѕ."}

    try:
        proc = subprocess.run(
            ["ollama", "stop", name],
            capture_output=True, text=True, timeout=20, cwd=str(APP_DIR),
        )
        stdout = _strip_ansi(proc.stdout or "")
        stderr = _strip_ansi(proc.stderr or "")
        if proc.returncode == 0:
            msg = stdout or f"Р›РѕРєР°Р»СЊРЅР°СЏ РјРѕРґРµР»СЊ {name} РѕСЃС‚Р°РЅРѕРІР»РµРЅР°."
            return {"ok": True, "message": msg}
        msg = stderr or stdout or f"РќРµ СѓРґР°Р»РѕСЃСЊ РІС‹РіСЂСѓР·РёС‚СЊ РјРѕРґРµР»СЊ {name}."
        return {"ok": False, "message": msg}
    except FileNotFoundError:
        return {"ok": False, "message": "РљРѕРјР°РЅРґР° ollama РЅРµ РЅР°Р№РґРµРЅР° РІ PATH."}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def generate_image_sdxl_turbo(
    prompt: str,
    negative_prompt: str = "",
    model_name_to_unload: str = "",
    seed: int | None = None,
    width: int = 512,
    height: int = 512,
    num_inference_steps: int = 4,
    guidance_scale: float = 0.0,
    output_path: str | None = None,
    model_id: str | None = None,
) -> Dict[str, Any]:
    """Р“РµРЅРµСЂР°С†РёСЏ РёР·РѕР±СЂР°Р¶РµРЅРёСЏ С‡РµСЂРµР· SDXL Turbo СЃ Р°РєРєСѓСЂР°С‚РЅРѕР№ РѕС‡РёСЃС‚РєРѕР№ VRAM."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "РџСЂРѕРјРїС‚ РїСѓСЃС‚.", "path": "", "log": ""}

    logs = []
    model_id = (model_id or IMAGE_MODEL_ID).strip()
    unload_info = stop_ollama_model(model_name_to_unload)
    logs.append(f"LLM unload: {unload_info['message']}")

    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except Exception as e:
        return {
            "ok": False,
            "error": (
                "РќРµ СѓРґР°Р»РѕСЃСЊ РёРјРїРѕСЂС‚РёСЂРѕРІР°С‚СЊ diffusers/torch. "
                "РЈСЃС‚Р°РЅРѕРІРё: pip install diffusers transformers accelerate safetensors"
            ),
            "path": "",
            "log": f"{logs[0]}\nImport error: {e}",
        }

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    logs.append(f"Device: {device}, dtype: {dtype}")

    if output_path:
        out_path = Path(output_path)
    else:
        out_path = OUTPUT_DIR / f"image_{abs(hash(prompt)) % 10**10}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pipe = None
    try:
        _torch_gc()
        fp16_kwargs = {
            "torch_dtype": dtype,
            "use_safetensors": True,
        }
        if device == "cuda":
            fp16_kwargs["variant"] = "fp16"

        pipe = AutoPipelineForText2Image.from_pretrained(model_id, **fp16_kwargs)
        if device == "cuda":
            pipe.to("cuda")
            if hasattr(pipe, "enable_attention_slicing"):
                pipe.enable_attention_slicing()
            if hasattr(pipe, "enable_vae_slicing"):
                pipe.enable_vae_slicing()
        else:
            pipe.to(device)

        generator = None
        if seed is not None:
            generator = torch.Generator(device=device).manual_seed(int(seed))
            logs.append(f"Seed: {seed}")

        logs.append("Preset: SDXL Turbo quality defaults (512x512, 4 steps, guidance=0.0)")

        kwargs = {
            "prompt": prompt,
            "num_inference_steps": int(num_inference_steps),
            "guidance_scale": float(guidance_scale),
            "width": int(width),
            "height": int(height),
        }
        if negative_prompt.strip():
            kwargs["negative_prompt"] = negative_prompt.strip()
        if generator is not None:
            kwargs["generator"] = generator

        image = pipe(**kwargs).images[0]
        image.save(out_path)
        logs.append(f"Saved: {out_path}")
        return {
            "ok": True,
            "path": str(out_path),
            "log": "\n".join(logs),
            "model_id": model_id,
            "prompt": prompt,
        }
    except Exception as e:
        logs.append(f"Generation error: {e}")
        return {
            "ok": False,
            "error": str(e),
            "path": "",
            "log": "\n".join(logs),
        }
    finally:
        try:
            del pipe
        except Exception:
            pass
        _torch_gc()


def _hf_access_hint(exc_text: str) -> str:
    low = (exc_text or "").lower()
    if any(x in low for x in ["gated", "401", "403", "access to model", "accept the conditions", "must be logged in"]):
        return (
            "Р”Р»СЏ FLUX.1-schnell РЅСѓР¶РЅРѕ РїСЂРёРЅСЏС‚СЊ СѓСЃР»РѕРІРёСЏ РјРѕРґРµР»Рё РЅР° Hugging Face "
            "Рё Р°РІС‚РѕСЂРёР·РѕРІР°С‚СЊСЃСЏ Р»РѕРєР°Р»СЊРЅРѕ С‡РµСЂРµР· `huggingface-cli login`."
        )
    return ""


def generate_image_flux_schnell(
    prompt: str,
    negative_prompt: str = "",
    model_name_to_unload: str = "",
    seed: int | None = None,
    width: int = 896,
    height: int = 512,
    num_inference_steps: int = 4,
    guidance_scale: float = 0.0,
    output_path: str | None = None,
    model_id: str | None = None,
    max_sequence_length: int = 160,
) -> Dict[str, Any]:
    """Р“РµРЅРµСЂР°С†РёСЏ РёР·РѕР±СЂР°Р¶РµРЅРёСЏ С‡РµСЂРµР· FLUX.1-schnell СЃ С‰Р°РґСЏС‰РёРј РїСЂРµСЃРµС‚РѕРј РґР»СЏ 8 GB VRAM."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "РџСЂРѕРјРїС‚ РїСѓСЃС‚.", "path": "", "log": ""}

    logs = []
    model_id = (model_id or "black-forest-labs/FLUX.1-schnell").strip()
    unload_info = stop_ollama_model(model_name_to_unload)
    logs.append(f"LLM unload: {unload_info['message']}")

    try:
        import gc
        import os
        import torch
        from diffusers import FluxPipeline
    except Exception as e:
        return {
            "ok": False,
            "error": (
                "РќРµ СѓРґР°Р»РѕСЃСЊ РёРјРїРѕСЂС‚РёСЂРѕРІР°С‚СЊ FLUX/diffusers. "
                "РЈСЃС‚Р°РЅРѕРІРё: pip install -U diffusers transformers accelerate safetensors sentencepiece protobuf"
            ),
            "path": "",
            "log": f"{logs[0]}\nImport error: {e}",
        }

    device = "cuda" if torch.cuda.is_available() else "cpu"
    flux_dtype = torch.bfloat16 if device == "cuda" else torch.float32
    logs.append(f"Device: {device}, dtype: {flux_dtype}")

    if output_path:
        out_path = Path(output_path)
    else:
        out_path = OUTPUT_DIR / f"flux_{abs(hash(prompt)) % 10**10}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pipe = None
    try:
        gc.collect()
        _torch_gc()
        if device == "cuda":
            os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
            _torch_gc()
            logs.append("CUDA cache РѕС‡РёС‰РµРЅ")

        pipe = FluxPipeline.from_pretrained(
            model_id,
            torch_dtype=flux_dtype,
            use_safetensors=True,
        )

        if device == "cuda":
            pipe.enable_model_cpu_offload()
            if hasattr(pipe, "enable_attention_slicing"):
                pipe.enable_attention_slicing()
                logs.append("Attention slicing enabled")
            if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_slicing"):
                pipe.vae.enable_slicing()
            if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_tiling"):
                pipe.vae.enable_tiling()
            logs.append("Offload: model_cpu_offload enabled")
        else:
            pipe.to("cpu")

        generator = None
        if seed is not None:
            generator = torch.Generator("cpu").manual_seed(int(seed))
            logs.append(f"Seed: {seed}")

        if negative_prompt.strip():
            logs.append("Note: negative prompt ignored for FLUX.1-schnell preset")

        logs.append(
            f"Preset: FLUX.1-schnell safe defaults ({int(width)}x{int(height)}, "
            f"{int(num_inference_steps)} steps, guidance={float(guidance_scale)}, max_seq={int(max_sequence_length)})"
        )

        kwargs = {
            "prompt": prompt,
            "guidance_scale": float(guidance_scale),
            "num_inference_steps": int(num_inference_steps),
            "max_sequence_length": int(max_sequence_length),
            "width": int(width),
            "height": int(height),
        }
        if generator is not None:
            kwargs["generator"] = generator

        image = pipe(**kwargs).images[0]
        image.save(out_path)
        logs.append(f"Saved: {out_path}")
        return {
            "ok": True,
            "path": str(out_path),
            "log": "\n".join(logs),
            "model_id": model_id,
            "prompt": prompt,
        }
    except Exception as e:
        err = str(e)
        hint = _hf_access_hint(err)
        if hint:
            logs.append(hint)
        if "cuda out of memory" in err.lower():
            logs.append("Tip: РґР»СЏ 8 GB VRAM РїРѕРїСЂРѕР±СѓР№ РµС‰С‘ РЅРёР¶Рµ: 768x512 Рё max_seq=128.")
        logs.append(f"Generation error: {err}")
        return {
            "ok": False,
            "error": hint or err,
            "path": "",
            "log": "\n".join(logs),
        }
    finally:
        try:
            del pipe
        except Exception:
            pass
        try:
            gc.collect()
        except Exception:
            pass
        _torch_gc()


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# BROWSER AGENT
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def _browser_runtime_hint(exc: Exception | str) -> str:
    text = str(exc or "")
    low = text.lower()
    if isinstance(exc, NotImplementedError) or "_make_subprocess_transport" in low or "notimplementederror" in low:
        return (
            "Playwright РЅРµ СЃРјРѕРі Р·Р°РїСѓСЃС‚РёС‚СЊ subprocess Р±СЂР°СѓР·РµСЂР°. "
            "РќР° Windows РґР»СЏ Streamlit РЅСѓР¶РЅРѕ РІРєР»СЋС‡РёС‚СЊ WindowsProactorEventLoopPolicy "
            "РґРѕ РёРјРїРѕСЂС‚Р° streamlit Рё Р·Р°С‚РµРј РїРѕР»РЅРѕСЃС‚СЊСЋ РїРµСЂРµР·Р°РїСѓСЃС‚РёС‚СЊ РїСЂРёР»РѕР¶РµРЅРёРµ."
        )
    if "executable doesn't exist" in low or "browsertype.launch" in low:
        return (
            "Р‘СЂР°СѓР·РµСЂ Chromium РґР»СЏ Playwright РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅ. "
            "Р—Р°РїСѓСЃС‚Рё: playwright install chromium"
        )
    return text


def _goal_keywords(goal: str) -> List[str]:
    words = re.findall(r"[\wР°-СЏРђ-РЇС‘РЃ-]+", (goal or "").lower())
    stop = {
        "Рё", "РёР»Рё", "РІ", "РЅР°", "СЃ", "РїРѕ", "РґР»СЏ", "Рѕ", "РѕР±", "РЅРµ", "СЌС‚Рѕ", "РєР°Рє", "С‡С‚Рѕ",
        "the", "and", "for", "with", "from", "into", "about", "read", "page", "website",
        "РїСЂРѕ", "СЃС‚СЂР°РЅРёС†Сѓ", "СЃР°Р№С‚", "СЃРґРµР»Р°Р№", "РєСЂР°С‚РєРёР№", "Р°РЅР°Р»РёР·", "РїСЂРѕС‡РёС‚Р°Р№", "РЅР°Р№РґРё", "РЅСѓР¶РЅРѕ",
    }
    return [w for w in words if len(w) >= 3 and w not in stop][:12]


def _extract_page_payload(page, max_chars: int = 9000) -> str:
    title = ""
    try:
        title = page.title()
    except Exception:
        pass

    headings = []
    try:
        headings = page.locator("h1, h2, h3").evaluate_all(
            "els => els.slice(0,12).map(e => (e.innerText || '').trim()).filter(Boolean)"
        )
    except Exception:
        pass

    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        body_text = ""

    lines = []
    if title:
        lines.append(f"Р—Р°РіРѕР»РѕРІРѕРє: {title}")
    lines.append(f"URL: {page.url}")
    if headings:
        lines.append("РџРѕРґР·Р°РіРѕР»РѕРІРєРё:\n- " + "\n- ".join(headings[:10]))
    if body_text.strip():
        lines.append("РўРµРєСЃС‚ СЃС‚СЂР°РЅРёС†С‹:\n" + truncate_text(body_text, max_chars))
    return "\n\n".join(lines)


def _collect_links(page, base_url: str) -> List[Dict[str, Any]]:
    try:
        links = page.locator("a").evaluate_all(
            """els => els.slice(0,150).map(a => ({
                text: (a.innerText || '').trim(),
                href: a.href || a.getAttribute('href') || '',
                title: a.getAttribute('title') || ''
            }))"""
        )
    except Exception:
        return []

    cleaned = []
    seen = set()
    base_host = urlparse(base_url).netloc.lower()
    for item in links:
        href = (item.get("href") or "").strip()
        if not href:
            continue
        href = urljoin(base_url, href)
        if not href.startswith(("http://", "https://")):
            continue
        if href in seen:
            continue
        seen.add(href)
        parsed = urlparse(href)
        cleaned.append({
            "href": href,
            "text": (item.get("text") or "").strip(),
            "title": (item.get("title") or "").strip(),
            "same_domain": parsed.netloc.lower() == base_host,
        })
    return cleaned


def _score_link(link: Dict[str, Any], goal_keywords: List[str]) -> int:
    bag = f"{link.get('text', '')} {link.get('title', '')} {link.get('href', '')}".lower()
    score = 0
    if link.get("same_domain"):
        score += 3
    if any(k in bag for k in ["about", "pricing", "docs", "product", "contact", "blog", "features", "faq"]):
        score += 1
    for kw in goal_keywords:
        if kw in bag:
            score += 4
    if any(x in bag for x in ["login", "signup", "register", "signin"]):
        score -= 4
    if any(x in bag for x in ["facebook", "twitter", "instagram", "linkedin", "youtube", "t.me"]):
        score -= 3
    return score


def _rank_links(links: List[Dict[str, Any]], goal: str, limit: int) -> List[Dict[str, Any]]:
    keywords = _goal_keywords(goal)
    ranked = []
    for link in links:
        ranked.append({**link, "score": _score_link(link, keywords)})
    ranked.sort(
        key=lambda x: (x["score"], x.get("same_domain", False), len(x.get("text", ""))),
        reverse=True,
    )
    positives = [x for x in ranked if x["score"] > 0]
    return (positives or ranked)[:limit]


def run_browser_agent(start_url: str, goal: str, max_pages: int = 3) -> Dict[str, Any]:
    trace = []
    if sync_playwright is None:
        return {
            "ok": False,
            "text": "Playwright РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅ.\nР—Р°РїСѓСЃС‚Рё: pip install playwright && playwright install",
            "trace": [],
        }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.goto(start_url, wait_until="load", timeout=30000)
            page.wait_for_timeout(1200)

            trace.append({"step": 1, "action": "open", "url": page.url, "title": page.title()})
            collected = ["=== РЎС‚СЂР°РЅРёС†Р° 1 ===\n" + _extract_page_payload(page, max_chars=10000)]

            ranked_links = _rank_links(_collect_links(page, page.url), goal, limit=max(0, max_pages - 1))
            visited = {page.url}

            for idx, link in enumerate(ranked_links, start=2):
                href = link.get("href", "")
                if not href or href in visited:
                    continue
                visited.add(href)
                sub = None
                try:
                    sub = context.new_page()
                    sub.goto(href, wait_until="load", timeout=25000)
                    sub.wait_for_timeout(1000)
                    collected.append("\n\n=== РЎС‚СЂР°РЅРёС†Р° {idx} ===\n".format(idx=idx) + _extract_page_payload(sub, max_chars=8000))
                    trace.append({
                        "step": idx,
                        "action": "open_link",
                        "url": sub.url,
                        "title": sub.title(),
                        "score": link.get("score", 0),
                        "link_text": link.get("text", ""),
                    })
                except Exception as e:
                    trace.append({
                        "step": idx,
                        "action": "error",
                        "url": href,
                        "title": str(e),
                        "score": link.get("score", 0),
                        "link_text": link.get("text", ""),
                    })
                finally:
                    try:
                        if sub is not None:
                            sub.close()
                    except Exception:
                        pass
            browser.close()
            return {"ok": True, "text": truncate_text("\n".join(collected), 30000), "trace": trace}
    except Exception as e:
        return {"ok": False, "text": _browser_runtime_hint(e), "trace": trace}


def _sanitize_browser_actions(actions: List[dict]) -> List[dict]:
    allowed = {"open", "click", "fill", "extract", "wait"}
    cleaned = []
    for item in actions or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip().lower()
        if action not in allowed:
            continue
        try:
            ms = int(item.get("ms", 1000) or 1000)
        except Exception:
            ms = 1000
        cleaned.append({
            "action": action,
            "url": str(item.get("url", "")).strip(),
            "selector": str(item.get("selector", "")).strip(),
            "value": str(item.get("value", "")),
            "ms": ms,
        })
    return cleaned[:12]


def browser_actions_from_goal(goal: str, model_name: str) -> List[dict]:
    prompt = (
        "РЎРѕСЃС‚Р°РІСЊ РїР»Р°РЅ РґРµР№СЃС‚РІРёР№ Р±СЂР°СѓР·РµСЂР° РІ JSON-РјР°СЃСЃРёРІРµ.\n"
        "Р Р°Р·СЂРµС€С‘РЅРЅС‹Рµ РґРµР№СЃС‚РІРёСЏ: open, click, fill, extract, wait.\n"
        "РљР°Р¶РґС‹Р№ С€Р°Рі РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РјРёРЅРёРјР°Р»СЊРЅС‹Рј Рё Р±РµР·РѕРїР°СЃРЅС‹Рј.\n"
        "Р”Р»СЏ С‡С‚РµРЅРёСЏ РєРѕРЅС‚РµРЅС‚Р° С‡Р°С‰Рµ РёСЃРїРѕР»СЊР·СѓР№ extract СЃ СЃРµР»РµРєС‚РѕСЂР°РјРё: main, article, body, h1.\n"
        "РќРµ РёСЃРїРѕР»СЊР·СѓР№ РЅРµРёР·РІРµСЃС‚РЅС‹Рµ РґРµР№СЃС‚РІРёСЏ. Р’РµСЂРЅРё С‚РѕР»СЊРєРѕ JSON Р±РµР· РїРѕСЏСЃРЅРµРЅРёР№.\n\n"
        "РџСЂРёРјРµСЂ:\n"
        '[{"action":"extract","selector":"main"},\n'
        ' {"action":"extract","selector":"body"}]\n\n'
        f"Р¦РµР»СЊ:\n{goal}"
    )
    raw = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=prompt,
        temp=0.1,
        include_history=False,
    )
    raw = clean_code_fence(re.sub(r"^```json\s*", "", raw.strip()))
    data = safe_json_parse(raw)
    return _sanitize_browser_actions(data if isinstance(data, list) else [])


def run_browser_actions(start_url: str, actions: List[dict]) -> Dict[str, Any]:
    trace, extracted = [], []
    if sync_playwright is None:
        return {"ok": False, "text": "Playwright РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅ.", "trace": []}
    actions = _sanitize_browser_actions(actions)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.goto(start_url, wait_until="load", timeout=30000)
            page.wait_for_timeout(1000)
            trace.append({"step": 1, "action": "open", "detail": page.url})
            for idx, item in enumerate(actions, start=2):
                action = item.get("action", "")
                try:
                    if action == "open" and item.get("url"):
                        target = urljoin(page.url, item["url"])
                        page.goto(target, wait_until="load", timeout=30000)
                        page.wait_for_timeout(800)
                        trace.append({"step": idx, "action": "open", "detail": page.url})
                    elif action == "click" and item.get("selector"):
                        page.locator(item["selector"]).first.click(timeout=10000)
                        page.wait_for_timeout(800)
                        trace.append({"step": idx, "action": "click", "detail": item["selector"]})
                    elif action == "fill" and item.get("selector"):
                        page.locator(item["selector"]).first.fill(str(item.get("value", "")), timeout=10000)
                        trace.append({"step": idx, "action": "fill", "detail": item["selector"]})
                    elif action == "wait":
                        page.wait_for_timeout(max(100, min(int(item.get("ms", 1000)), 10000)))
                        trace.append({"step": idx, "action": "wait", "detail": item.get("ms")})
                    elif action == "extract":
                        selector = item.get("selector", "body") or "body"
                        text = page.locator(selector).first.inner_text(timeout=10000)
                        extracted.append(f"EXTRACT {selector}:\nURL: {page.url}\n" + truncate_text(text, 7000))
                        trace.append({"step": idx, "action": "extract", "detail": selector})
                except Exception as e:
                    trace.append({"step": idx, "action": f"{action}_error", "detail": str(e)})
            browser.close()
            return {
                "ok": True,
                "text": "\n\n".join(extracted) if extracted else "Р”РµР№СЃС‚РІРёСЏ РІС‹РїРѕР»РЅРµРЅС‹, РЅРѕ С‚РµРєСЃС‚Р° РґР»СЏ РёР·РІР»РµС‡РµРЅРёСЏ РЅРµ Р±С‹Р»Рѕ.",
                "trace": trace,
            }
    except Exception as e:
        return {"ok": False, "text": _browser_runtime_hint(e), "trace": trace}

# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# РўР•Р РњРРќРђР›
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def is_dangerous_command(cmd: str) -> bool:
    low = cmd.lower().strip()
    return any(b in low for b in TERMINAL_BLOCKED)


def run_terminal(cmd: str, timeout: int = 25) -> str:
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(APP_DIR),
        )
        return f"$ {cmd}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n\nРљРѕРјР°РЅРґР° РѕСЃС‚Р°РЅРѕРІР»РµРЅР° РїРѕ С‚Р°Р№РјР°СѓС‚Сѓ ({timeout} СЃРµРє.)"
    except Exception as e:
        return f"РћС€РёР±РєР° С‚РµСЂРјРёРЅР°Р»Р°: {e}"


# в”Ђв”Ђ Playwright sync helper в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def sync_playwright_available() -> bool:
    return sync_playwright is not None



# ================================
# Browser в†’ RAG helpers
# ================================

def _clean_browser_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r", " ")
    text = text.replace("\t", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


def _chunk_browser_text(text: str, size: int = 1200):
    chunks = []
    text = _clean_browser_text(text)

    start = 0
    while start < len(text):
        chunk = text[start:start + size]
        if chunk.strip():
            chunks.append(chunk.strip())
        start += size

    return chunks


def build_browser_rag_records(url: str, goal: str, summary: str, page_text: str):
    records = []

    summary = _clean_browser_text(summary)
    page_text = _clean_browser_text(page_text)

    if summary:
        records.append({
            "type": "browser_summary",
            "url": url,
            "goal": goal,
            "content": summary
        })

    for chunk in _chunk_browser_text(page_text):
        records.append({
            "type": "browser_page",
            "url": url,
            "goal": goal,
            "content": chunk
        })

    return records


def build_web_knowledge_records(query: str, web_context: str, source_kind: str = "web_search", max_chars: int = 14000):
    records = []
    clean_query = (query or "").strip()
    clean_text = _clean_browser_text(truncate_text(web_context or "", max_chars))
    if not clean_text:
        return records

    records.append({
        "type": "web_summary",
        "url": "",
        "goal": clean_query,
        "content": f"WEB QUERY: {clean_query}\n\n{clean_text[:3000]}",
        "title": clean_query[:300],
        "source_kind": source_kind,
    })

    for chunk in _chunk_browser_text(clean_text, size=1200):
        records.append({
            "type": "web_chunk",
            "url": "",
            "goal": clean_query,
            "content": chunk,
            "title": clean_query[:300],
            "source_kind": source_kind,
        })

    return records


def persist_web_knowledge(
    query: str,
    web_context: str,
    profile_name: str,
    source_kind: str = "web_search",
    url: str = "",
    title: str = "",
):
    from .memory import add_kb_record, add_memory, record_web_learning_run

    saved_memory = 0
    saved_kb = 0
    records = build_web_knowledge_records(query=query, web_context=web_context, source_kind=source_kind)
    for rec in records:
        content = rec.get("content", "")
        rec_url = rec.get("url") or url
        rec_title = rec.get("title") or title or query
        rec_type = rec.get("type", "web_chunk")
        if add_memory(
            content,
            source=f"{source_kind}:{rec_url or query[:80]}",
            memory_type=rec_type,
            profile_name=profile_name,
        ):
            saved_memory += 1
        if add_kb_record(
            content=content,
            title=rec_title,
            url=rec_url,
            source=source_kind,
            chunk_type=rec_type,
            profile_name=profile_name,
        ):
            saved_kb += 1

    record_web_learning_run(
        query=query,
        url=url,
        title=title or query[:300],
        source_kind=source_kind,
        ok=bool(web_context and web_context.strip()),
        saved_kb=saved_kb,
        saved_memory=saved_memory,
        notes=(web_context or "")[:1200],
        profile_name=profile_name,
    )
    return {"saved_memory": saved_memory, "saved_kb": saved_kb, "records": len(records)}




def route_task(user_text: str, model_name: str = "", memory_profile: str = "", num_ctx: int = 4096) -> dict:
    """Facade — delegates to domain.agents.router."""
    from app.domain.agents.router import route_task as _route
    return _route(user_text, model_name=model_name, memory_profile=memory_profile, num_ctx=num_ctx)


# Facade — canonical definition in domain.agents.router
from app.domain.agents.router import TASK_GRAPH_TEMPLATES_V8  # noqa: E402


def _safe_json_object(text: str) -> dict:
    try:
        data = safe_json_parse((text or "").strip())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def reflection_v2(task: str, answer: str, model_name: str, memory_context: str = "", kb_context: str = "", profile_name: str = "", num_ctx: int = 4096) -> dict:
    from .memory import record_reflection
    context = "\n\n".join(x for x in [memory_context, kb_context] if x.strip())
    prompt = f"""
РўС‹ evaluator.

Р’РµСЂРЅРё РўРћР›Р¬РљРћ JSON-РѕР±СЉРµРєС‚ С„РѕСЂРјР°С‚Р°:
{{
  "answered": true,
  "grounded": true,
  "complete": true,
  "actionable": true,
  "safe": true,
  "needs_retry": false,
  "notes": "РєРѕСЂРѕС‚РєРѕРµ РѕР±СЉСЏСЃРЅРµРЅРёРµ",
  "improved_answer": "СѓР»СѓС‡С€РµРЅРЅР°СЏ РІРµСЂСЃРёСЏ РѕС‚РІРµС‚Р°"
}}

РџСЂР°РІРёР»Р°:
- answered = РѕС‚РІРµС‚РёР» Р»Рё РѕС‚РІРµС‚ РЅР° РёСЃС…РѕРґРЅСѓСЋ Р·Р°РґР°С‡Сѓ.
- grounded = РѕРїРёСЂР°РµС‚СЃСЏ Р»Рё РѕС‚РІРµС‚ РЅР° РґРѕСЃС‚СѓРїРЅС‹Р№ РєРѕРЅС‚РµРєСЃС‚.
- complete = РЅРµС‚ Р»Рё Р·Р°РјРµС‚РЅС‹С… РїСЂРѕРїСѓСЃРєРѕРІ.
- actionable = РµСЃС‚СЊ Р»Рё РїРѕР»РµР·РЅС‹Р№ СЃР»РµРґСѓСЋС‰РёР№ С€Р°Рі РёР»Рё РїСЂР°РєС‚РёС‡РµСЃРєРёР№ РІС‹РІРѕРґ.
- safe = РЅРµС‚ Р»Рё СЏРІРЅС‹С… РіР°Р»Р»СЋС†РёРЅР°С†РёР№, РѕРїР°СЃРЅС‹С… РёР»Рё РЅРµРѕР±РѕСЃРЅРѕРІР°РЅРЅС‹С… СѓС‚РІРµСЂР¶РґРµРЅРёР№.
- needs_retry = true, РµСЃР»Рё РѕС‚РІРµС‚ РЅСѓР¶РЅРѕ РїРµСЂРµСЃРѕР±СЂР°С‚СЊ Р·Р°РЅРѕРІРѕ.
- improved_answer = Р»РёР±Рѕ СѓР»СѓС‡С€РµРЅРЅС‹Р№ РѕС‚РІРµС‚, Р»РёР±Рѕ РёСЃС…РѕРґРЅС‹Р№, РµСЃР»Рё СѓР»СѓС‡С€РµРЅРёРµ РЅРµ С‚СЂРµР±СѓРµС‚СЃСЏ.

Р—РђР”РђР§Рђ:
{task}

РљРћРќРўР•РљРЎРў:
{context[:7000]}

РћРўР’Р•Рў:
{(answer or '')[:9000]}
"""
    raw = ask_model(
        model_name=model_name,
        profile_name="РђРЅР°Р»РёС‚РёРє",
        user_input=prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        temp=0.05,
        num_ctx=num_ctx,
    )
    raw = clean_code_fence((raw or '').strip())
    data = _safe_json_object(raw)
    result = {
        "answered": bool(data.get("answered", True)),
        "grounded": bool(data.get("grounded", True)),
        "complete": bool(data.get("complete", True)),
        "actionable": bool(data.get("actionable", True)),
        "safe": bool(data.get("safe", True)),
        "needs_retry": bool(data.get("needs_retry", False)),
        "notes": str(data.get("notes", "") or ""),
        "improved_answer": str(data.get("improved_answer", "") or answer or ""),
    }
    try:
        record_reflection(task, answer, result, profile_name=profile_name)
    except Exception:
        pass
    return result


def _count_false_flags(reflection: dict) -> int:
    checks = [
        reflection.get("answered", True),
        reflection.get("grounded", True),
        reflection.get("complete", True),
        reflection.get("actionable", True),
        reflection.get("safe", True),
    ]
    return sum(1 for x in checks if not bool(x))


def regenerate_answer_from_context(task: str, model_name: str, memory_context: str = "", kb_context: str = "", prior_answer: str = "", reflection_notes: str = "", num_ctx: int = 4096) -> str:
    prompt = f"""
РџРµСЂРµСЃРѕР±РµСЂРё РѕС‚РІРµС‚ Р»СѓС‡С€Рµ.

РСЃС…РѕРґРЅР°СЏ Р·Р°РґР°С‡Р°:
{task}

РџСЂРѕР±Р»РµРјС‹ РїСЂРѕС€Р»РѕРіРѕ РѕС‚РІРµС‚Р°:
{reflection_notes}

РџСЂРѕС€Р»С‹Р№ РѕС‚РІРµС‚:
{prior_answer[:4000]}

РўСЂРµР±РѕРІР°РЅРёСЏ:
- РґР°Р№ Р±РѕР»РµРµ С‚РѕС‡РЅС‹Р№ Рё РїРѕР»РµР·РЅС‹Р№ РѕС‚РІРµС‚,
- РЅРµ РІС‹РґСѓРјС‹РІР°Р№ С„Р°РєС‚С‹,
- РµСЃР»Рё РґР°РЅРЅС‹С… РЅРµ С…РІР°С‚Р°РµС‚ вЂ” СЃРєР°Р¶Рё СЌС‚Рѕ СЏРІРЅРѕ,
- РµСЃР»Рё СѓРјРµСЃС‚РЅРѕ, РґР°Р№ СЃР»РµРґСѓСЋС‰РёР№ РїСЂР°РєС‚РёС‡РµСЃРєРёР№ С€Р°Рі.
"""
    return ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=prompt,
        memory_context="\n\n".join(x for x in [memory_context, kb_context] if x.strip()),
        use_memory=True,
        include_history=False,
        temp=0.15,
        num_ctx=num_ctx,
    )


def get_fallback_node_v8(node_name: str, state: dict) -> str:
    mapping = {
        "task_graph": "planner",
        "planner": "finalize",
        "reflection_v2": "finalize",
        "finalize": "finalize",
    }
    return mapping.get(node_name, "")


def run_graph_with_retry_v8(graph: list, handlers: dict, state: dict, max_retries: int = 2) -> dict:
    state.setdefault("errors", [])
    state.setdefault("retries", {})
    state.setdefault("timeline", [])
    for node in graph:
        tries = 0
        while tries <= max_retries:
            started = time.time()
            try:
                state = handlers[node](state)
                elapsed = round(time.time() - started, 3)
                state["timeline"].append({"node": node, "status": "ok", "seconds": elapsed})
                break
            except Exception as e:
                tries += 1
                elapsed = round(time.time() - started, 3)
                state["errors"].append({"node": node, "error": str(e)})
                state["retries"][node] = tries
                state["timeline"].append({"node": node, "status": "error", "seconds": elapsed, "error": str(e)})
                fallback = get_fallback_node_v8(node, state)
                if fallback and fallback in handlers and fallback != node:
                    try:
                        state = handlers[fallback](state)
                        state["timeline"].append({"node": fallback, "status": "fallback_ok"})
                        break
                    except Exception as e2:
                        state["errors"].append({"node": fallback, "error": str(e2)})
                        state["timeline"].append({"node": fallback, "status": "fallback_error", "error": str(e2)})
                if tries > max_retries:
                    state["failed_node"] = node
                    return state
    return state



def choose_v8_strategy(
    task: str,
    route: dict,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    force_strategy: str | None = None,
) -> dict:
    """Facade — delegates to domain.agents.router."""
    from app.domain.agents.router import choose_v8_strategy as _choose
    return _choose(
        task, route, model_name, memory_profile,
        num_ctx=num_ctx, force_strategy=force_strategy,
    )


def _choose_v8_strategy_legacy(
    task: str,
    route: dict,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    force_strategy: str | None = None,
) -> dict:
    """Original inline implementation — frozen, will be removed."""
    from .memory import get_v8_strategy_preferences

    if force_strategy:
        return {
            "strategy": str(force_strategy),
            "confidence": 1.0,
            "source": "forced",
            "reason": "force_strategy parameter",
            "scores": {str(force_strategy): 1.0},
            "learned_preferences": [],
        }

    mode = str((route or {}).get("mode", "chat") or "chat")
    text = (task or "").lower()

    scores = {
        "direct": 0.20,
        "planner": 0.20,
        "task_graph": 0.20,
        "multi_agent": 0.20,
        "self_improve": 0.10,
    }

    mode_bias = {
        "chat": {"direct": 1.30},
        "research": {"task_graph": 1.05, "planner": 0.35},
        "code": {"task_graph": 0.90, "multi_agent": 0.70},
        "file": {"task_graph": 0.90, "planner": 0.40},
        "multi_step": {"planner": 1.10, "multi_agent": 0.55},
    }
    for k, v in mode_bias.get(mode, {}).items():
        scores[k] = scores.get(k, 0.0) + v

    long_task = len(task or "") > 280
    if long_task:
        scores["multi_agent"] += 0.35
        scores["task_graph"] += 0.25

    if any(x in text for x in ["С‡С‚Рѕ С‚Р°РєРѕРµ", "РѕР±СЉСЏСЃРЅРё", "РєСЂР°С‚РєРѕ", "РїСЂРѕСЃС‚С‹РјРё СЃР»РѕРІР°РјРё", "short", "summary"]):
        scores["direct"] += 0.80

    if any(x in text for x in ["РїР»Р°РЅ", "roadmap", "РїРѕ С€Р°РіР°Рј", "С€Р°РіРё РІРЅРµРґСЂРµРЅРёСЏ", "СЃС‚СЂР°С‚РµРіРёСЏ РІРЅРµРґСЂРµРЅРёСЏ"]):
        scores["planner"] += 0.95

    if any(x in text for x in ["РёСЃСЃР»РµРґСѓР№", "СЃСЂР°РІРЅРё", "РґРѕРєСѓРјРµРЅС‚Р°С†", "РїСЂР°РєС‚РёРєРё", "best practices", "РЅР°Р№РґРё", "langgraph", "crewai"]):
        scores["task_graph"] += 0.80

    if any(x in text for x in ["Р°СЂС…РёС‚РµРєС‚СѓСЂ", "СЂРµС„Р°РєС‚РѕСЂ", "РїСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ РїСЂРѕРµРєС‚", "РєРѕРґРѕРІС‹Рµ РёР·РјРµРЅРµРЅРёСЏ", "review code"]):
        scores["multi_agent"] += 0.95

    if any(x in text for x in ["СѓР»СѓС‡С€Рё РѕС‚РІРµС‚", "СЃР°РјРѕР°РЅР°Р»РёР·", "self-improve", "РїРµСЂРµРїСЂРѕРІРµСЂСЊ", "РґРѕСЂР°Р±РѕС‚Р°Р№ РѕС‚РІРµС‚"]):
        scores["self_improve"] += 1.10

    learned = []
    try:
        learned = get_v8_strategy_preferences(task, profile_name=memory_profile, limit=5)
    except Exception:
        learned = []

    for pref in learned:
        strategy = pref.get("strategy")
        if strategy not in scores:
            continue
        runs = max(int(pref.get("runs", 0) or 0), 1)
        success_rate = float(pref.get("success_rate", 0.0) or 0.0)
        avg_latency = float(pref.get("avg_latency", 0.0) or 0.0)
        learned_bonus = min(float(pref.get("score", 0.0)) / runs, 2.5) * 0.18
        latency_penalty = min(avg_latency / 10.0, 0.35)
        scores[strategy] += (success_rate * 0.95) + learned_bonus - latency_penalty

    strategy = max(scores.items(), key=lambda kv: kv[1])[0]
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_score = ordered[0][1]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0
    confidence = round(max(0.15, min(0.99, 0.45 + (top_score - second_score) / max(abs(top_score), 1.0))), 2)

    reason_parts = [f"mode={mode}"]
    if learned:
        best = learned[0]
        reason_parts.append(
            f"learned={best.get('strategy')} sr={best.get('success_rate')} runs={best.get('runs')}"
        )
    else:
        reason_parts.append("learned=no_history")

    return {
        "strategy": strategy,
        "confidence": confidence,
        "source": "learning_router",
        "reason": "; ".join(reason_parts),
        "scores": {k: round(v, 3) for k, v in sorted(scores.items())},
        "learned_preferences": learned,
    }



def run_agent_v8(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
    force_strategy: str | None = None,
) -> dict:
    from .memory import (
        add_working_memory,
        build_kb_context,
        build_memory_context,
        build_working_memory_context,
        get_tool_preferences,
        record_task_run,
        record_tool_usage,
        record_v8_strategy_usage,
    )

    run_started = time.time()
    run_id = uuid4().hex[:12]
    route = route_task(task, model_name=model_name, memory_profile=memory_profile, num_ctx=num_ctx)
    if not isinstance(route, dict):
        route = {
            "mode": "chat",
            "agent": "chat_agent",
            "use_graph": False,
            "confidence": 0.0,
            "source": "fallback",
            "reason": "route_task returned None or invalid data",
        }

    mode = route.get("mode", "chat") or "chat"
    strategy = choose_v8_strategy(
        task=task,
        route=route,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        force_strategy=force_strategy,
    )
    selected_strategy = strategy.get("strategy", "direct") or "direct"

    graph_map = {
        "direct": ["retrieve_memory", "retrieve_kb", "retrieve_working_memory", "finalize"],
        "planner": ["retrieve_memory", "retrieve_kb", "retrieve_working_memory", "planner", "reflection_v2", "finalize"],
        "task_graph": ["retrieve_memory", "retrieve_kb", "retrieve_working_memory", "tool_hint", "task_graph", "reflection_v2", "finalize"],
        "multi_agent": ["retrieve_memory", "retrieve_kb", "retrieve_working_memory", "multi_agent", "reflection_v2", "finalize"],
        "self_improve": ["retrieve_memory", "retrieve_kb", "retrieve_working_memory", "self_improve", "finalize"],
    }
    graph = graph_map.get(selected_strategy) or TASK_GRAPH_TEMPLATES_V8.get(mode, ["retrieve_memory", "retrieve_working_memory", "finalize"])
    total_steps = max(len(graph), 1)

    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    state = {
        "run_id": run_id,
        "task": task,
        "model_name": model_name,
        "memory_profile": memory_profile,
        "route": route,
        "mode": mode,
        "strategy": strategy,
        "selected_strategy": selected_strategy,
        "graph": graph,
        "memory_context": "",
        "kb_context": "",
        "working_context": "",
        "tool_hint": "",
        "plan_result": None,
        "task_graph_result": None,
        "multi_agent_result": None,
        "self_improve_result": None,
        "answer": "",
        "reflection": {},
        "errors": [],
        "timeline": [],
    }

    def _wm(step_name: str, fact_type: str, content: str, score: float = 1.0):
        try:
            add_working_memory(
                run_id=run_id,
                step_name=step_name,
                fact_type=fact_type,
                content=(content or "")[:6000],
                score=score,
                profile_name=memory_profile,
            )
        except Exception:
            pass

    def _refresh_working():
        try:
            state["working_context"] = build_working_memory_context(run_id, profile_name=memory_profile, limit=12)
        except Exception:
            state["working_context"] = state.get("working_context", "")

    def _record_tool(tool_name: str, ok: bool, meta: str = ""):
        try:
            record_tool_usage(
                tool_name=tool_name,
                task_hint=task,
                ok=ok,
                score=1.0 if ok else 0.0,
                notes=meta[:1000],
                profile_name=memory_profile,
            )
        except Exception:
            pass

    _wm("route", "goal", task, score=1.0)
    _wm(
        "route",
        "decision",
        f"mode={route.get('mode')} source={route.get('source', 'keyword')} confidence={route.get('confidence', 0)} reason={route.get('reason', '')}",
        score=float(route.get("confidence", 0.5) or 0.5),
    )
    _wm(
        "strategy",
        "decision",
        json.dumps(strategy, ensure_ascii=False)[:2000],
        score=float(strategy.get("confidence", 0.6) or 0.6),
    )
    _refresh_working()

    def h_retrieve_memory(s: dict) -> dict:
        _progress(1, "рџ§  РџР°РјСЏС‚СЊ")
        s["memory_context"] = build_memory_context(task, memory_profile, top_k=8)
        if s["memory_context"].strip():
            _wm("retrieve_memory", "finding", s["memory_context"][:2000], score=0.9)
        _refresh_working()
        return s

    def h_retrieve_kb(s: dict) -> dict:
        _progress(2, "рџ“љ KB")
        s["kb_context"] = build_kb_context(task, profile_name=memory_profile, top_k=4)
        if s["kb_context"].strip():
            _wm("retrieve_kb", "source", s["kb_context"][:2000], score=0.85)
        _refresh_working()
        return s

    def h_retrieve_working_memory(s: dict) -> dict:
        _progress(3, "рџ§© Working memory")
        _refresh_working()
        return s

    def h_tool_hint(s: dict) -> dict:
        _progress(4, "рџ›  Tool memory")
        try:
            prefs = get_tool_preferences(task, profile_name=memory_profile, limit=3)
        except Exception:
            prefs = []
        if prefs:
            lines = []
            for p in prefs:
                tool = p.get("tool", p.get("tool_name", "unknown"))
                success_rate = p.get("success_rate")
                if success_rate is None:
                    runs = max(int(p.get("runs", 0) or p.get("uses", 0) or 0), 1)
                    success_rate = round(float(p.get("success", 0)) / runs, 2)
                uses = p.get("uses", p.get("runs", 0))
                lines.append(f"- {tool}: success_rate={success_rate}, uses={uses}")
            s["tool_hint"] = "РџСЂРµРґРїРѕС‡С‚РёС‚РµР»СЊРЅС‹Рµ РёРЅСЃС‚СЂСѓРјРµРЅС‚С‹ РїРѕ РїСЂРѕС€Р»РѕРјСѓ РѕРїС‹С‚Сѓ:\n" + "\n".join(lines)
            _wm("tool_hint", "decision", s["tool_hint"][:1800], score=0.75)
        else:
            s["tool_hint"] = ""
        _refresh_working()
        return s

    def h_planner(s: dict) -> dict:
        _progress(5, "рџ§­ Planner")
        plan = run_planner_agent(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=None)
        s["plan_result"] = plan
        s["answer"] = plan.get("final") or plan.get("summary") or ""
        if plan:
            _wm("planner", "decision", str(plan)[:2500], score=0.85)
        _record_tool("planner_agent", True, "run_planner_agent")
        _refresh_working()
        return s

    def h_task_graph(s: dict) -> dict:
        _progress(5, "рџ•ё Task Graph")
        result = run_task_graph(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=None)
        s["task_graph_result"] = result
        final_answer = ""
        if isinstance(result, dict):
            final_answer = result.get("final") or result.get("answer") or result.get("summary") or ""
        if not final_answer and isinstance(result, dict):
            logs = result.get("execution_log", []) or result.get("steps", [])
            if logs:
                final_answer = "\n\n".join(str(x.get("output", ""))[:2000] for x in logs[-2:])
        s["answer"] = final_answer or s.get("answer", "")
        if result:
            _wm("task_graph", "finding", str(result)[:3000], score=0.9)
        _record_tool("task_graph", True, "run_task_graph")
        _refresh_working()
        return s

    def h_multi_agent(s: dict) -> dict:
        _progress(5, "рџ¤ќ Multi-Agent")
        result = run_multi_agent(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=None)
        s["multi_agent_result"] = result
        s["answer"] = (result or {}).get("final", "") or s.get("answer", "")
        if result:
            _wm("multi_agent", "finding", str(result)[:3000], score=0.92)
        _record_tool("multi_agent", True, "run_multi_agent")
        _refresh_working()
        return s

    def h_self_improve(s: dict) -> dict:
        _progress(5, "в™»пёЏ Self-Improving")
        result = run_self_improving_agent(
            task,
            model_name,
            memory_profile,
            num_ctx=num_ctx,
            max_iters=2,
            progress_callback=None,
            base_force_strategy="direct",
        )
        s["self_improve_result"] = result
        s["answer"] = (result or {}).get("answer", "") or s.get("answer", "")
        if result:
            _wm("self_improve", "finding", str(result)[:3000], score=0.9)
        _record_tool("self_improve", True, "run_self_improving_agent")
        _refresh_working()
        return s

    def h_reflection_v2(s: dict) -> dict:
        _progress(6, "рџЄћ Reflection v2")
        if s.get("selected_strategy") == "self_improve" and s.get("answer", "").strip():
            return s
        refl = reflection_v2(
            task=task,
            answer=s.get("answer", ""),
            model_name=model_name,
            memory_context=s.get("memory_context", ""),
            kb_context="\n\n".join(x for x in [s.get("kb_context", ""), s.get("working_context", "")] if x.strip()),
            profile_name=memory_profile,
            num_ctx=num_ctx,
        )
        false_count = _count_false_flags(refl)
        if false_count >= 3 or refl.get("needs_retry"):
            regenerated = regenerate_answer_from_context(
                task=task,
                model_name=model_name,
                memory_context="\n\n".join(x for x in [s.get("memory_context", ""), s.get("working_context", "")] if x.strip()),
                kb_context=s.get("kb_context", ""),
                prior_answer=s.get("answer", ""),
                reflection_notes=refl.get("notes", ""),
                num_ctx=num_ctx,
            )
            s["answer"] = regenerated
            refl["regenerated"] = True
        else:
            improved = refl.get("improved_answer", "").strip()
            if improved:
                s["answer"] = improved
            refl["regenerated"] = False
        s["reflection"] = refl
        _wm("reflection_v2", "decision", json.dumps(refl, ensure_ascii=False)[:2500], score=0.9)
        if s.get("answer", "").strip():
            _wm("reflection_v2", "finding", s["answer"][:2500], score=0.8)
        _refresh_working()
        return s

    def h_finalize(s: dict) -> dict:
        _progress(total_steps, "вњ… Final")
        if not s.get("answer", "").strip():
            fallback_prompt = f"""
РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ РѕС‚РІРµС‚ РїРѕ Р·Р°РґР°С‡Рµ.

Р—Р°РґР°С‡Р°:
{task}

РљРѕРЅС‚РµРєСЃС‚ РїР°РјСЏС‚Рё:
{s.get("memory_context", "")[:5000]}

РљРѕРЅС‚РµРєСЃС‚ KB:
{s.get("kb_context", "")[:4000]}

Р Р°Р±РѕС‡Р°СЏ РїР°РјСЏС‚СЊ:
{s.get("working_context", "")[:4000]}

РџРѕРґСЃРєР°Р·РєР° РїРѕ РёРЅСЃС‚СЂСѓРјРµРЅС‚Р°Рј:
{s.get("tool_hint", "")[:1500]}

РўСЂРµР±РѕРІР°РЅРёСЏ:
- РѕС‚РІРµС‚ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РєРѕРЅРєСЂРµС‚РЅС‹Рј,
- РЅРµ РІС‹РґСѓРјС‹РІР°Р№ С„Р°РєС‚С‹,
- РµСЃР»Рё РґР°РЅРЅС‹С… РјР°Р»Рѕ, С‚Р°Рє Рё СЃРєР°Р¶Рё,
- РґР°Р№ СЃР»РµРґСѓСЋС‰РёР№ РїСЂР°РєС‚РёС‡РµСЃРєРёР№ С€Р°Рі.
"""
            s["answer"] = ask_model(
                model_name=model_name,
                profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
                user_input=fallback_prompt,
                memory_context="\n\n".join(x for x in [s.get("memory_context", ""), s.get("working_context", "")] if x.strip()),
                use_memory=True,
                include_history=False,
                temp=0.15,
                num_ctx=num_ctx,
            )
        if s.get("answer", "").strip():
            _wm("finalize", "finding", s["answer"][:3000], score=0.95)
        _refresh_working()
        return s

    handlers = {
        "retrieve_memory": h_retrieve_memory,
        "retrieve_kb": h_retrieve_kb,
        "retrieve_working_memory": h_retrieve_working_memory,
        "tool_hint": h_tool_hint,
        "planner": h_planner,
        "task_graph": h_task_graph,
        "multi_agent": h_multi_agent,
        "self_improve": h_self_improve,
        "reflection_v2": h_reflection_v2,
        "finalize": h_finalize,
    }

    state = run_graph_with_retry_v8(graph, handlers, state, max_retries=2)

    status = "ok" if not state.get("failed_node") else "failed"
    try:
        record_task_run(
            task_text=task,
            route_mode=mode,
            graph_used=" -> ".join(graph),
            final_status=status,
            profile_name=memory_profile,
        )
    except Exception:
        pass

    latency = round(time.time() - run_started, 3)
    reflection = state.get("reflection", {}) or {}
    answer_ok = bool(state.get("answer", "").strip()) and not state.get("failed_node")
    quality_score = 1.0
    if reflection:
        quality_score = (
            0.2
            + 0.2 * float(bool(reflection.get("answered", True)))
            + 0.2 * float(bool(reflection.get("grounded", True)))
            + 0.2 * float(bool(reflection.get("complete", True)))
            + 0.2 * float(bool(reflection.get("actionable", True)))
        )
    try:
        record_v8_strategy_usage(
            strategy=selected_strategy,
            route_mode=mode,
            task_hint=task,
            ok=answer_ok,
            score=round(quality_score, 3),
            latency=latency,
            notes=str(strategy.get("reason", ""))[:1000],
            profile_name=memory_profile,
        )
    except Exception:
        pass

    try:
        from app.services.persona_service import observe_dialogue

        persona_meta = observe_dialogue(
            dialog_id=run_id,
            session_id=run_id,
            profile_name=memory_profile,
            model_name=model_name,
            user_input=task,
            answer_text=state.get("answer", ""),
            route=mode,
            reflection=reflection,
            outcome_ok=answer_ok,
        )
    except Exception:
        persona_meta = None

    return {
        "run_id": run_id,
        "mode": mode,
        "route": route,
        "strategy": strategy,
        "delegated_strategy": selected_strategy,
        "graph": graph,
        "answer": state.get("answer", ""),
        "reflection": state.get("reflection", {}),
        "task_graph_result": state.get("task_graph_result"),
        "plan_result": state.get("plan_result"),
        "multi_agent_result": state.get("multi_agent_result"),
        "self_improve_result": state.get("self_improve_result"),
        "errors": state.get("errors", []),
        "timeline": state.get("timeline", []),
        "failed_node": state.get("failed_node", ""),
        "memory_context": state.get("memory_context", ""),
        "kb_context": state.get("kb_context", ""),
        "working_context": state.get("working_context", ""),
        "tool_hint": state.get("tool_hint", ""),
        "latency_seconds": latency,
        "persona": persona_meta,
    }


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# SELF-IMPROVING AGENT
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def run_self_improving_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    max_iters: int = 2,
    progress_callback=None,
    base_force_strategy: str | None = None,
) -> Dict[str, Any]:
    from .memory import (
        build_memory_context,
        build_kb_context,
        build_working_memory_context,
        record_self_improve_run,
        record_tool_usage,
    )

    total_steps = max(2, int(max_iters) + 1)

    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    _progress(1, "рџљЂ Р‘Р°Р·РѕРІС‹Р№ Р·Р°РїСѓСЃРє V8")
    base = run_agent_v8(
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        progress_callback=None,
        force_strategy=base_force_strategy,
    )

    answer = (base.get("answer", "") or "").strip()
    reflection = base.get("reflection", {}) or {}
    iterations: List[Dict[str, Any]] = []
    run_id = base.get("run_id", "")
    working_context = base.get("working_context", "") or ""

    for idx in range(1, max(0, int(max_iters)) + 1):
        _progress(min(idx + 1, total_steps), f"рџЄћ Self-Improve {idx}")
        mem_ctx = build_memory_context(task, memory_profile, top_k=8)
        kb_ctx = build_kb_context(task, profile_name=memory_profile, top_k=4)
        if run_id:
            try:
                working_context = build_working_memory_context(run_id, profile_name=memory_profile, limit=12)
            except Exception:
                pass

        combined_context = (mem_ctx or "") + "\n\n" + (kb_ctx or "") + "\n\n" + (working_context or "")
        critique_prompt = f"""
РўС‹ self-improve critic.
Р’РµСЂРЅРё РўРћР›Р¬РљРћ JSON:
{{
  "improve": true,
  "score": 0.0,
  "issues": ["..."],
  "focus": "С‡С‚Рѕ СѓР»СѓС‡С€РёС‚СЊ"
}}

Р—РђР”РђР§Рђ:
{task}

РўР•РљРЈР©РР™ РћРўР’Р•Рў:
{answer[:9000]}

REFLECTION:
{json.dumps(reflection, ensure_ascii=False)}

РљРћРќРўР•РљРЎРў:
{combined_context[:9000]}
"""
        raw_crit = ask_model(
            model_name=model_name,
            profile_name="РђРЅР°Р»РёС‚РёРє",
            user_input=critique_prompt,
            memory_context=mem_ctx,
            use_memory=True,
            include_history=False,
            temp=0.05,
            num_ctx=min(num_ctx, 4096),
        )
        crit = safe_json_parse(clean_code_fence(raw_crit)) or {}
        should_improve = bool(crit.get("improve", idx == 1))
        if isinstance(reflection, dict) and (reflection.get("needs_retry") or not reflection.get("complete", True)):
            should_improve = True

        if not should_improve:
            item = {
                "iteration": idx,
                "changed": False,
                "answer": answer,
                "critique": crit,
                "reflection": reflection,
            }
            iterations.append(item)
            try:
                record_self_improve_run(task, idx, answer, crit, reflection, memory_profile)
            except Exception:
                pass
            break

        improve_prompt = f"""
РЈР»СѓС‡С€Рё РѕС‚РІРµС‚ РїРѕСЃР»Рµ self-improving loop.

РСЃС…РѕРґРЅР°СЏ Р·Р°РґР°С‡Р°:
{task}

РўРµРєСѓС‰РёР№ РѕС‚РІРµС‚:
{answer[:9000]}

РџСЂРѕР±Р»РµРјС‹ / focus:
{json.dumps(crit, ensure_ascii=False, indent=2)}

Reflection:
{json.dumps(reflection, ensure_ascii=False, indent=2)}

РљРѕРЅС‚РµРєСЃС‚ РїР°РјСЏС‚Рё:
{mem_ctx[:4000]}

РљРѕРЅС‚РµРєСЃС‚ KB:
{kb_ctx[:3000]}

Р Р°Р±РѕС‡Р°СЏ РїР°РјСЏС‚СЊ:
{working_context[:3000]}

РўСЂРµР±РѕРІР°РЅРёСЏ:
- РЎРґРµР»Р°Р№ РѕС‚РІРµС‚ С‚РѕС‡РЅРµРµ Рё РїСЂР°РєС‚РёС‡РЅРµРµ.
- РќРµ РІС‹РґСѓРјС‹РІР°Р№ С„Р°РєС‚С‹.
- Р•СЃР»Рё РґР°РЅРЅС‹С… РЅРµ С…РІР°С‚Р°РµС‚ вЂ” СЃРєР°Р¶Рё СЌС‚Рѕ СЏРІРЅРѕ.
- РЎРѕС…СЂР°РЅРё СЃРёР»СЊРЅС‹Рµ С‡Р°СЃС‚Рё РїСЂРѕС€Р»РѕРіРѕ РѕС‚РІРµС‚Р°.
"""
        improved = ask_model(
            model_name=model_name,
            profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
            user_input=improve_prompt,
            memory_context="\n\n".join(x for x in [mem_ctx, kb_ctx, working_context] if x.strip()),
            use_memory=True,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        ).strip() or answer

        reflection = reflection_v2(
            task=task,
            answer=improved,
            model_name=model_name,
            memory_context="\n\n".join(x for x in [mem_ctx, working_context] if x.strip()),
            kb_context=kb_ctx,
            profile_name=memory_profile,
            num_ctx=num_ctx,
        )
        answer = improved
        item = {
            "iteration": idx,
            "changed": True,
            "answer": answer,
            "critique": crit,
            "reflection": reflection,
        }
        iterations.append(item)
        try:
            record_self_improve_run(task, idx, answer, crit, reflection, memory_profile)
        except Exception:
            pass

        if isinstance(reflection, dict) and reflection.get("complete", True) and reflection.get("answered", True) and not reflection.get("needs_retry", False):
            break

    try:
        record_tool_usage(
            tool_name="self_improving_agent",
            task_hint=task,
            ok=bool(answer.strip()),
            score=1.5 if answer.strip() else 0.0,
            notes=f"iterations={len(iterations)}",
            profile_name=memory_profile,
        )
    except Exception:
        pass

    try:
        from app.services.persona_service import observe_dialogue

        persona_meta = observe_dialogue(
            dialog_id=run_id or f"self-improve-{memory_profile}",
            session_id=run_id or f"self-improve-{memory_profile}",
            profile_name=memory_profile,
            model_name=model_name,
            user_input=task,
            answer_text=answer,
            route="self_improve",
            reflection=reflection if isinstance(reflection, dict) else {},
            outcome_ok=bool(answer.strip()),
        )
    except Exception:
        persona_meta = None

    return {
        "run_id": run_id,
        "base": base,
        "answer": answer,
        "iterations": iterations,
        "final_reflection": reflection,
        "mode": base.get("mode", ""),
        "route": base.get("route", {}),
        "graph": base.get("graph", []),
        "timeline": base.get("timeline", []),
        "errors": base.get("errors", []),
        "memory_context": base.get("memory_context", ""),
        "kb_context": base.get("kb_context", ""),
        "working_context": working_context or base.get("working_context", ""),
        "persona": persona_meta,
    }


# Phase 4 override: legacy multi-agent path now delegates to Workflow Engine.
def run_multi_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
    project_context: str = "",
    file_context: str = "",
) -> Dict[str, Any]:
    from app.services.workflow_engine import run_legacy_multi_agent_workflow

    return run_legacy_multi_agent_workflow(
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        progress_callback=progress_callback,
        project_context=project_context,
        file_context=file_context,
    )

