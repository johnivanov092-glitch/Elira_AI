"""
agents.py — все агентные модули.

Ключевые улучшения v7.1:
  • execute_python_with_capture  — subprocess-изоляция + таймаут + matplotlib через файлы
  • self_heal_python_code        — авто-исправление до N попыток
  • run_build_loop               — изолированная temp-dir, улучшенный ok-check
  • run_multi_agent              — прогресс-бар, надёжный fallback плана
  • Browser / Terminal           — без изменений
"""
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


# ═══════════════════════════════════════════════════════════════════════════════
# PYTHON LAB — изолированный subprocess
# ═══════════════════════════════════════════════════════════════════════════════

_PYTHON_EXEC_TIMEOUT = 30   # секунд на выполнение кода

# Враппер который сохраняет matplotlib-фигуры как PNG в рабочую папку
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
    Запускает Python-код в изолированном subprocess.
    Возвращает: {ok, output, traceback, figures}
      figures — список bytes (PNG) для st.image()

    Изоляция гарантирует:
      - бесконечный цикл → убивается по таймауту
      - sys.exit() / os._exit() → не роняет Streamlit
      - импорт тяжёлых либ → отдельный процесс, не засоряет память
    """
    with tempfile.TemporaryDirectory(prefix="jarvis_exec_") as tmp:
        tmp_path  = Path(tmp)
        code_file = tmp_path / "_run.py"

        # Если extra_globals содержат данные — сериализуем и передаём
        prelude = _FIGURE_SAVER
        if extra_globals:
            serializable = {}
            for k, v in extra_globals.items():
                try:
                    json.dumps(v)   # только JSON-сериализуемые
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

            # Разделяем настоящие ошибки от предупреждений
            is_error = proc.returncode != 0 or "Traceback" in stderr

            # Собираем PNG-фигуры
            figures = []
            for fig_file in sorted(tmp_path.glob("fig_*.png")):
                try:
                    figures.append(fig_file.read_bytes())
                except Exception:
                    pass

            return {
                "ok":        not is_error,
                "output":    stdout or ("Код выполнен без вывода" if not is_error else ""),
                "traceback": stderr if is_error else "",
                "warnings":  stderr if not is_error and stderr else "",
                "figures":   figures,
            }

        except subprocess.TimeoutExpired:
            return {
                "ok":        False,
                "output":    "",
                "traceback": f"⏱ Превышен таймаут выполнения ({timeout} сек). "
                             f"Проверь нет ли бесконечного цикла.",
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
    Запускает код, при ошибке просит модель исправить, повторяет до max_retries раз.
    Возвращает: (итоговый_код, последний_результат, история_попыток)
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
            f"Ты исправляешь Python-код после ошибки выполнения.\n"
            f"Верни только исправленный Python-код без markdown и пояснений.\n\n"
            f"Путь к файлу данных:\n{file_path}\n\n"
            f"Задача:\n{task}\n\n"
            f"Схема данных:\n{schema_text}\n\n"
            f"Текущий код:\n{current_code}\n\n"
            f"STDOUT:\n{result['output']}\n\n"
            f"TRACEBACK:\n{result['traceback']}\n\n"
            f"Исправь код так, чтобы он выполнился успешно. "
            f"Не используй markdown, верни только чистый Python."
        )
        fixed = ask_model(
            model_name=model_name, profile_name="Аналитик",
            user_input=repair_prompt, temp=0.1,
            include_history=False, num_ctx=num_ctx,
        ).strip()
        current_code = clean_code_fence(fixed)

    return current_code, last_result, history


# ═══════════════════════════════════════════════════════════════════════════════
# CODE BUILDER LOOP — изолированная temp-директория
# ═══════════════════════════════════════════════════════════════════════════════

def generate_file_code(
    target_file: str, task: str, model_name: str,
    project_context: str, file_context: str, num_ctx: int = 4096,
) -> str:
    prompt = (
        f"Напиши полный рабочий код для файла {target_file}.\n"
        f"Верни только содержимое файла без markdown.\n\n"
        f"Задача:\n{task}\n\n"
        f"Контекст проекта:\n{project_context[:20000]}\n\n"
        f"Контекст файлов:\n{file_context[:8000]}"
    )
    code = ask_model(
        model_name, "Программист", prompt,
        project_context=project_context, file_context=file_context,
        include_history=False, num_ctx=num_ctx,
    )
    return clean_code_fence(code)


def _ok_check(stdout: str, stderr: str, returncode: int) -> bool:
    """
    Считает запуск успешным если:
      - returncode == 0
      - нет Traceback в stderr
      - нет 'Error:' в начале строки stderr
    Предупреждения (DeprecationWarning, UserWarning) НЕ считаются ошибкой.
    """
    if returncode != 0:
        return False
    if "Traceback (most recent call last)" in stderr:
        return False
    # Ошибки типа "ModuleNotFoundError: ..." без Traceback
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
    Генерирует код, запускает в изолированной temp-директории, при ошибке — чинит.

    Изоляция: код пишется в tempdir, не в APP_DIR.
    После успеха — копируется в GENERATED_DIR.
    """
    history = []

    # Генерируем начальный код
    code = generate_file_code(
        target_file, task, model_name,
        project_context, file_context, num_ctx,
    )

    with tempfile.TemporaryDirectory(prefix="jarvis_build_") as tmp:
        tmp_path    = Path(tmp)
        target_path = tmp_path / Path(target_file).name

        for attempt in range(1, max_retries + 2):
            # Пишем код в temp-директорию
            target_path.write_text(code, encoding="utf-8")

            # Запускаем команду в temp-директории
            output = _run_in_dir(run_command, cwd=tmp_path, timeout=60)

            # Парсим stdout / stderr из вывода run_terminal
            stdout_part = ""
            stderr_part = ""
            if "STDOUT:\n" in output:
                stdout_part = output.split("STDOUT:\n", 1)[1].split("\n\nSTDERR:\n")[0]
            if "STDERR:\n" in output:
                stderr_part = output.split("STDERR:\n", 1)[1]

            # Получаем returncode (run_terminal не возвращает его, эвристика)
            returncode = 0 if "Traceback" not in output and "Error" not in stderr_part else 1

            ok = _ok_check(stdout_part, stderr_part, returncode)

            history.append({
                "attempt":    attempt,
                "code":       code,
                "run_output": output,
                "ok":         ok,
            })

            if ok:
                # Копируем результат в GENERATED_DIR
                dest = GENERATED_DIR / Path(target_file).name
                shutil.copy2(target_path, dest)
                return code, output, history

            if attempt >= max_retries + 1:
                break

            # Просим модель починить
            fix_prompt = (
                f"Исправь код файла '{target_file}' после неудачного запуска.\n"
                f"Верни только новый код без markdown.\n\n"
                f"Задача:\n{task}\n\n"
                f"Текущий код:\n{code}\n\n"
                f"Команда запуска:\n{run_command}\n\n"
                f"STDOUT:\n{stdout_part}\n\n"
                f"STDERR:\n{stderr_part}"
            )
            code = clean_code_fence(
                ask_model(
                    model_name, "Программист", fix_prompt,
                    project_context=project_context, file_context=file_context,
                    temp=0.1, include_history=False, num_ctx=num_ctx,
                )
            )

    return code, history[-1]["run_output"] if history else "", history


def _run_in_dir(cmd: str, cwd: Path, timeout: int = 60) -> str:
    """Запускает команду в указанной директории."""
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(cwd),
        )
        return f"$ {cmd}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n\nКоманда остановлена по таймауту ({timeout} сек.)"
    except Exception as e:
        return f"Ошибка запуска: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-AGENT ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def run_multi_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,  # callable(step: int, total: int, label: str)
    project_context: str = "",
    file_context: str = "",
) -> Dict[str, Any]:
    """
    Запускает 5-шаговый Multi-Agent pipeline:
      1. Planner     → структурированный план (JSON)
      2. Researcher  → исследование и факты
      3. Coder       → код и реализация
      4. Reviewer    → проверка и критика
      5. Orchestrator→ финальный deliverable

    progress_callback вызывается после каждого шага — используй для st.progress().
    """
    from .memory import build_memory_context
    memory_context = build_memory_context(task, memory_profile, top_k=5)

    total_steps = 5
    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    # ── Шаг 1: Planner ────────────────────────────────────────────────────────
    _progress(1, "🗺 Planner: составляю план...")
    planner_prompt = (
        "Ты Planner агент. Разбей задачу на 4 блока и верни ТОЛЬКО JSON без пояснений:\n"
        '{"research":"<что исследовать>","coding":"<что написать>","review":"<что проверить>",'
        '"deliverable":"<итоговый результат>"}\n\n'
        f"Задача:\n{task}"
    )
    raw_plan = ask_model(
        model_name=model_name, profile_name="Оркестратор",
        user_input=planner_prompt, memory_context=memory_context,
        use_memory=True, temp=0.05, include_history=False, num_ctx=num_ctx,
    )
    plan = safe_json_parse(raw_plan)

    # Надёжный fallback: если план не распарсился — создаём из задачи
    if not isinstance(plan, dict) or not plan.get("research"):
        plan = {
            "research":    f"Исследуй тему: {task[:500]}",
            "coding":      f"Напиши код или решение для: {task[:500]}",
            "review":      f"Проверь корректность решения задачи: {task[:500]}",
            "deliverable": "Собери итог: краткий вывод + практические шаги + код если нужен",
        }

    # ── Шаг 2: Researcher ─────────────────────────────────────────────────────
    _progress(2, "🔬 Researcher: исследую...")
    research = ask_model(
        model_name=model_name, profile_name="Исследователь",
        user_input=plan["research"],
        memory_context=memory_context, use_memory=True,
        include_history=False, num_ctx=num_ctx,
    )

    # ── Шаг 3: Coder ──────────────────────────────────────────────────────────
    _progress(3, "👨‍💻 Coder: пишу решение...")
    coding_input = (
        f"{plan['coding']}\n\n"
        f"Контекст исследования:\n{research[:3000]}"
    )
    coding = ask_model(
        model_name=model_name, profile_name="Программист",
        user_input=coding_input,
        project_context=project_context,
        file_context=file_context,
        memory_context=memory_context, use_memory=True,
        include_history=False, num_ctx=num_ctx,
    )

    # ── Шаг 4: Reviewer ───────────────────────────────────────────────────────
    _progress(4, "🔍 Reviewer: проверяю...")
    review_input = (
        f"Проверь результаты Research и Coding агентов.\n\n"
        f"ИСХОДНАЯ ЗАДАЧА:\n{task}\n\n"
        f"PLAN: {plan['review']}\n\n"
        f"RESEARCH:\n{research[:2000]}\n\n"
        f"CODING:\n{coding[:2000]}\n\n"
        f"Укажи: что верно, что сомнительно, что нужно улучшить."
    )
    review = ask_model(
        model_name=model_name, profile_name="Аналитик",
        user_input=review_input,
        memory_context=memory_context, use_memory=True,
        include_history=False, num_ctx=num_ctx,
    )

    # ── Шаг 5: Orchestrator → финальный deliverable ───────────────────────────
    _progress(5, "🎯 Orchestrator: собираю итог...")
    final_input = (
        f"Собери финальный deliverable на основе работы всех агентов.\n\n"
        f"ЗАДАЧА:\n{task}\n\n"
        f"RESEARCH:\n{research[:1500]}\n\n"
        f"CODING:\n{coding[:1500]}\n\n"
        f"REVIEW:\n{review[:1000]}\n\n"
        f"ТРЕБОВАНИЕ К DELIVERABLE: {plan['deliverable']}\n\n"
        f"Структура ответа:\n"
        f"1. Краткий вывод (2-3 предложения)\n"
        f"2. Основная часть (код / анализ / решение)\n"
        f"3. Практические следующие шаги"
    )
    final_draft = ask_model(
        model_name=model_name, profile_name="Оркестратор",
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





# ═══════════════════════════════════════════════════════════════════════════════
# SELF-REFLECTION LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def reflect_and_improve_answer(
    task: str,
    draft: str,
    model_name: str,
    profile_name: str = "Оркестратор",
    extra_context: str = "",
    num_ctx: int = 4096,
) -> Dict[str, str]:
    critic_prompt = (
        "Ты reflection-critic. Коротко проверь черновик ответа на: полноту, фактическую опору на контекст, "
        "полезность и конкретность. Верни ТОЛЬКО JSON без markdown: "
        '{"score":0-10,"issues":["..."],"improve":"yes|no","brief":"..."}.\n\n'
        f"ЗАДАЧА:\n{task}\n\n"
        f"КОНТЕКСТ:\n{extra_context[:6000]}\n\n"
        f"ЧЕРНОВИК:\n{draft[:8000]}"
    )
    raw = ask_model(
        model_name=model_name,
        profile_name="Аналитик",
        user_input=critic_prompt,
        include_history=False,
        temp=0.05,
        num_ctx=min(num_ctx, 4096),
    )
    critique = safe_json_parse(clean_code_fence(raw))
    if not isinstance(critique, dict):
        critique = {"score": 7, "issues": ["Не удалось распарсить critique"], "improve": "yes", "brief": str(raw)[:500]}

    improve_flag = str(critique.get("improve", "yes")).lower() == "yes" or float(critique.get("score", 7)) < 8
    improved = draft
    if improve_flag:
        improve_prompt = (
            "Улучши ответ после self-reflection. Сохрани факты, убери слабые места, сделай ответ точнее и практичнее.\n\n"
            f"ЗАДАЧА:\n{task}\n\n"
            f"КОНТЕКСТ:\n{extra_context[:6000]}\n\n"
            f"ЧЕРНОВИК:\n{draft[:8000]}\n\n"
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
# ═══════════════════════════════════════════════════════════════════════════════
# PLANNER AGENT — orchestration поверх Browser / Terminal / Reasoning
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_first_url(text: str) -> str:
    if not text:
        return ""
    m = re.search(r'https?://[^\s<>"\'\]]+', text)
    return m.group(0).rstrip('.,);]') if m else ""


def _planner_safe_terminal_command(cmd: str) -> bool:
    low = (cmd or "").strip().lower()
    if not low or is_dangerous_command(low):
        return False
    allowed_prefixes = (
        "dir", "ls", "pwd", "where python", "python --version", "python -v",
        "pip list", "git status", "git branch", "git log --oneline",
        "type ", "cat "
    )
    return low.startswith(allowed_prefixes)


def _planner_default_steps(task: str) -> List[dict]:
    url = _extract_first_url(task)
    steps: List[dict] = []
    if url:
        steps.append({
            "tool": "browser",
            "goal": "Прочитай страницу и извлеки факты, полезные для исходной задачи.",
            "url": url,
        })
    elif any(word in task.lower() for word in ["найди", "поиск", "веб", "сайт", "документац", "интернет", "страниц"]):
        steps.append({
            "tool": "browser",
            "goal": task[:400],
            "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
        })
    steps.append({
        "tool": "reasoning",
        "goal": "Собери вывод и практические шаги на основе доступного контекста.",
    })
    return steps[:4]


def run_planner_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
    from .memory import build_memory_context

    total_steps = 3
    def _progress(step: int, label: str):
        if progress_callback:
            progress_callback(step, total_steps, label)

    memory_context = build_memory_context(task, memory_profile, top_k=8)

    _progress(1, "🧭 Planner: строю план...")
    planner_prompt = (
        "Ты Planner agent. Разбей задачу на 2-4 шага и верни ТОЛЬКО JSON-массив без пояснений.\n"
        "Формат шага:\n"
        '[{"tool":"browser|terminal|reasoning","goal":"...","url":"optional","command":"optional"}]\n'
        "Правила:\n"
        "- browser: только если нужен веб/страница/документация/поиск.\n"
        "- terminal: только для БЕЗОПАСНЫХ read-only команд локального анализа.\n"
        "- reasoning: финальный аналитический шаг.\n"
        "- Не предлагай опасные команды.\n"
        "- Если в задаче есть URL, используй его в browser step.\n"
        "- Последний шаг всегда reasoning.\n\n"
        f"Задача:\n{task}"
    )
    raw_plan = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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
        normalized_plan.append({"tool": "reasoning", "goal": "Собери финальный ответ по всем наблюдениям.", "url": "", "command": ""})
    normalized_plan = normalized_plan[:5]

    _progress(2, "🛠 Planner: выполняю шаги...")
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
                    "output": "Шаг пропущен: команда не прошла safe-check planner-а.",
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

    _progress(3, "🎯 Planner: собираю итог...")
    context_blob = "\n\n".join(gathered_contexts)[:24000]
    final_prompt = (
        "Ты Planner-Orchestrator. Собери финальный ответ пользователю на основе исходной задачи "
        "и результатов шагов. Если данных недостаточно, честно укажи это.\n\n"
        f"ЗАДАЧА:\n{task}\n\n"
        f"ПЛАН:\n{json.dumps(normalized_plan, ensure_ascii=False, indent=2)}\n\n"
        f"КОНТЕКСТ ШАГОВ:\n{context_blob}\n\n"
        "Структура:\n"
        "1. Краткий итог\n"
        "2. Что удалось узнать / сделать\n"
        "3. Практические следующие шаги"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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




# ═══════════════════════════════════════════════════════════════════════════════
# TASK GRAPH — orchestration graph поверх Browser / Terminal / Memory / Reasoning
# ═══════════════════════════════════════════════════════════════════════════════

def _task_graph_default(task: str) -> List[dict]:
    url = _extract_first_url(task)
    nodes: List[dict] = []

    if any(word in task.lower() for word in ["вспомни", "память", "что ты знаешь", "из памяти", "memory"]):
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
            "goal": "Прочитай страницу и извлеки факты, полезные для исходной задачи.",
            "url": url,
            "depends_on": [],
        })
    elif any(word in task.lower() for word in ["найди", "поиск", "веб", "сайт", "документац", "интернет", "страниц"]):
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
        "goal": "Собери финальный аналитический ответ по исходной задаче.",
        "depends_on": [n["id"] for n in nodes],
    })
    return nodes[:6]


def _normalize_task_graph(raw_graph: Any, task: str) -> List[dict]:
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
            "goal": "Собери финальный аналитический ответ по исходной задаче.",
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
    from .memory import build_memory_context

    memory_context = build_memory_context(task, memory_profile, top_k=8)
    planner_prompt = (
        "Ты строишь task graph для локальной AI-системы. "
        "Верни ТОЛЬКО JSON-массив узлов без пояснений. "
        "Каждый узел должен иметь формат:\n"
        '[{"id":"n1","tool":"browser|terminal|reasoning|memory_lookup","goal":"...","url":"optional","command":"optional","depends_on":["n0"]}]\n\n'
        "Правила:\n"
        "- Узлы должны быть короткими и практичными.\n"
        "- browser: только если нужен веб/сайт/документация/страница.\n"
        "- terminal: только для безопасных read-only команд локального анализа.\n"
        "- memory_lookup: когда нужно поднять релевантную память профиля.\n"
        "- reasoning: для аналитики и синтеза.\n"
        "- Последний узел всегда reasoning.\n"
        "- Максимум 6 узлов.\n"
        "- Не придумывай опасные команды.\n\n"
        f"Задача:\n{task}"
    )
    raw_graph = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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
    parts = []
    for dep in node.get("depends_on", []):
        res = node_results.get(dep)
        if not res:
            continue
        snippet = truncate_text(str(res.get("output", "")), 6000)
        parts.append(f"[{dep} · {res.get('tool', '')}]\n{snippet}")
    return "\n\n".join(parts)


def run_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
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
            _progress(step_idx, f"🕸 Выполняю {node['id']} · {node['tool']}")
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
                        "output": "Узел пропущен: команда не прошла safe-check Task Graph.",
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
                    "output": mem[:12000] if mem else "Релевантная память не найдена.",
                }

            else:  # reasoning
                reasoning_prompt = (
                    "Ты reasoning-node в task graph. Выполни только задачу этого узла, "
                    "опираясь на исходную задачу и контекст зависимостей. "
                    "Если контекста мало — скажи об этом прямо.\n\n"
                    f"ИСХОДНАЯ ЗАДАЧА:\n{task}\n\n"
                    f"ЗАДАЧА УЗЛА:\n{node['goal']}\n\n"
                    f"КОНТЕКСТ ЗАВИСИМОСТЕЙ:\n{dep_context or 'Нет контекста зависимостей.'}"
                )
                out = ask_model(
                    model_name=model_name,
                    profile_name="Аналитик",
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
                    "output": "Узел не выполнен: зависимости не разрешились или граф некорректен.",
                }
                execution_log.append(node_results[node["id"]])
            break

    # Auto-retry graph: если browser/terminal шаги упали — пробуем fallback reasoning/safe browser
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
                "output": "Terminal шаг не прошёл safe-check. Для сохранения прогресса граф переключён на reasoning fallback.",
            }
            retried.append(retry_node)
    execution_log.extend(retried)

    _progress(total_steps, "🎯 Task Graph: собираю итог...")
    state_blob = "\n\n".join(
        f"[{item['id']} · {item['tool']} · {'OK' if item.get('ok') else 'FAIL'}]\n"
        f"GOAL: {item.get('goal','')}\n"
        f"{truncate_text(str(item.get('output','')), 5000)}"
        for item in execution_log
    )[:30000]

    final_prompt = (
        "Ты Task Graph Orchestrator. Собери финальный ответ пользователю на основе состояния графа. "
        "Честно отмечай, если какой-то узел не сработал или данных не хватило.\n\n"
        f"ИСХОДНАЯ ЗАДАЧА:\n{task}\n\n"
        f"ГРАФ:\n{json.dumps(graph, ensure_ascii=False, indent=2)}\n\n"
        f"STATE:\n{state_blob}\n\n"
        "Структура ответа:\n"
        "1. Краткий итог\n"
        "2. Что удалось узнать / сделать\n"
        "3. Ограничения или сбои\n"
        "4. Практические следующие шаги"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE GENERATION — SDXL Turbo
# ═══════════════════════════════════════════════════════════════════════════════

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
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def prepare_image_prompt(
    prompt: str,
    model_name: str,
    auto_translate: bool = True,
    num_ctx: int = 2048,
) -> Dict[str, str]:
    """Готовит prompt для image model.

    Возвращает dict:
      {original_prompt, final_prompt, translated, log, ok}
    Никогда не бросает исключение наружу — при сбое возвращает исходный prompt.
    """
    original = (prompt or "").strip()
    if not original:
        return {
            "ok": False,
            "original_prompt": "",
            "final_prompt": "",
            "translated": "false",
            "log": "Пустой prompt.",
        }

    if not auto_translate:
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "false",
            "log": "Автоперевод отключён.",
        }

    if not _contains_cyrillic(original):
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "false",
            "log": "Кириллица не найдена — использую prompt как есть.",
        }

    try:
        translate_prompt = (
            "Преобразуй пользовательский запрос в короткий точный английский prompt "
            "для генерации изображения в SDXL Turbo. "
            "Сохрани смысл. Не добавляй пояснений, нумерации, markdown и кавычек. "
            "Верни только одну строку готового prompt.\n\n"
            f"Запрос пользователя:\n{original}"
        )
        translated = ask_model(
            model_name=model_name,
            profile_name="Аналитик",
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
            "log": f"RU → EN: {translated}",
        }
    except Exception as e:
        return {
            "ok": True,
            "original_prompt": original,
            "final_prompt": original,
            "translated": "fallback",
            "log": f"Перевод не сработал, использую исходный prompt. Ошибка: {e}",
        }


def stop_ollama_model(model_name: str) -> Dict[str, Any]:
    """Пытается выгрузить локальную модель Ollama из VRAM перед генерацией."""
    name = (model_name or "").strip()
    if not name:
        return {"ok": True, "message": "Модель не указана — пропускаю выгрузку."}
    if "cloud" in name.lower():
        return {"ok": True, "message": f"{name} — облачная модель, выгружать нечего."}

    try:
        proc = subprocess.run(
            ["ollama", "stop", name],
            capture_output=True, text=True, timeout=20, cwd=str(APP_DIR),
        )
        stdout = _strip_ansi(proc.stdout or "")
        stderr = _strip_ansi(proc.stderr or "")
        if proc.returncode == 0:
            msg = stdout or f"Локальная модель {name} остановлена."
            return {"ok": True, "message": msg}
        msg = stderr or stdout or f"Не удалось выгрузить модель {name}."
        return {"ok": False, "message": msg}
    except FileNotFoundError:
        return {"ok": False, "message": "Команда ollama не найдена в PATH."}
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
    """Генерация изображения через SDXL Turbo с аккуратной очисткой VRAM."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Промпт пуст.", "path": "", "log": ""}

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
                "Не удалось импортировать diffusers/torch. "
                "Установи: pip install diffusers transformers accelerate safetensors"
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
            "Для FLUX.1-schnell нужно принять условия модели на Hugging Face "
            "и авторизоваться локально через `huggingface-cli login`."
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
    """Генерация изображения через FLUX.1-schnell с щадящим пресетом для 8 GB VRAM."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Промпт пуст.", "path": "", "log": ""}

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
                "Не удалось импортировать FLUX/diffusers. "
                "Установи: pip install -U diffusers transformers accelerate safetensors sentencepiece protobuf"
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
            logs.append("CUDA cache очищен")

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
            logs.append("Tip: для 8 GB VRAM попробуй ещё ниже: 768x512 и max_seq=128.")
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


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER AGENT
# ═══════════════════════════════════════════════════════════════════════════════

def _browser_runtime_hint(exc: Exception | str) -> str:
    text = str(exc or "")
    low = text.lower()
    if isinstance(exc, NotImplementedError) or "_make_subprocess_transport" in low or "notimplementederror" in low:
        return (
            "Playwright не смог запустить subprocess браузера. "
            "На Windows для Streamlit нужно включить WindowsProactorEventLoopPolicy "
            "до импорта streamlit и затем полностью перезапустить приложение."
        )
    if "executable doesn't exist" in low or "browsertype.launch" in low:
        return (
            "Браузер Chromium для Playwright не установлен. "
            "Запусти: playwright install chromium"
        )
    return text


def _goal_keywords(goal: str) -> List[str]:
    words = re.findall(r"[\wа-яА-ЯёЁ-]+", (goal or "").lower())
    stop = {
        "и", "или", "в", "на", "с", "по", "для", "о", "об", "не", "это", "как", "что",
        "the", "and", "for", "with", "from", "into", "about", "read", "page", "website",
        "про", "страницу", "сайт", "сделай", "краткий", "анализ", "прочитай", "найди", "нужно",
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
        lines.append(f"Заголовок: {title}")
    lines.append(f"URL: {page.url}")
    if headings:
        lines.append("Подзаголовки:\n- " + "\n- ".join(headings[:10]))
    if body_text.strip():
        lines.append("Текст страницы:\n" + truncate_text(body_text, max_chars))
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
            "text": "Playwright не установлен.\nЗапусти: pip install playwright && playwright install",
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
            collected = ["=== Страница 1 ===\n" + _extract_page_payload(page, max_chars=10000)]

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
                    collected.append("\n\n=== Страница {idx} ===\n".format(idx=idx) + _extract_page_payload(sub, max_chars=8000))
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
        "Составь план действий браузера в JSON-массиве.\n"
        "Разрешённые действия: open, click, fill, extract, wait.\n"
        "Каждый шаг должен быть минимальным и безопасным.\n"
        "Для чтения контента чаще используй extract с селекторами: main, article, body, h1.\n"
        "Не используй неизвестные действия. Верни только JSON без пояснений.\n\n"
        "Пример:\n"
        '[{"action":"extract","selector":"main"},\n'
        ' {"action":"extract","selector":"body"}]\n\n'
        f"Цель:\n{goal}"
    )
    raw = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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
        return {"ok": False, "text": "Playwright не установлен.", "trace": []}
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
                "text": "\n\n".join(extracted) if extracted else "Действия выполнены, но текста для извлечения не было.",
                "trace": trace,
            }
    except Exception as e:
        return {"ok": False, "text": _browser_runtime_hint(e), "trace": trace}

# ═══════════════════════════════════════════════════════════════════════════════
# ТЕРМИНАЛ
# ═══════════════════════════════════════════════════════════════════════════════

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
        return f"$ {cmd}\n\nКоманда остановлена по таймауту ({timeout} сек.)"
    except Exception as e:
        return f"Ошибка терминала: {e}"


# ── Playwright sync helper ────────────────────────────────────────────────────
def sync_playwright_available() -> bool:
    return sync_playwright is not None



# ================================
# Browser → RAG helpers
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
    """
    Совместимый router для V8.
    Возвращает mode / agent / use_graph / confidence / source / reason.
    """
    t = (user_text or "").lower()

    if any(x in t for x in ["pdf", "docx", "txt", "csv", "excel", "xlsx", "файл", "документ", "таблица"]):
        return {"mode": "file", "agent": "file_agent", "use_graph": True, "confidence": 0.86, "source": "keyword", "reason": "file markers"}

    if any(x in t for x in ["найди", "поищи", "исследуй", "research", "browser", "web", "веб", "документац", "сайт"]):
        return {"mode": "research", "agent": "browser_agent", "use_graph": True, "confidence": 0.84, "source": "keyword", "reason": "research markers"}

    if any(x in t for x in ["python", "код", "fastapi", "api", "streamlit", "bug", "ошибка", "рефактор", "скрипт"]):
        return {"mode": "code", "agent": "coder_agent", "use_graph": True, "confidence": 0.85, "source": "keyword", "reason": "code markers"}

    if any(x in t for x in ["план", "по шагам", "архитектур", "pipeline", "стратег", "roadmap"]):
        return {"mode": "multi_step", "agent": "planner_agent", "use_graph": True, "confidence": 0.78, "source": "keyword", "reason": "planner markers"}

    return {"mode": "chat", "agent": "chat_agent", "use_graph": False, "confidence": 0.55, "source": "fallback", "reason": "default chat"}


TASK_GRAPH_TEMPLATES_V8 = {
    "chat": ["retrieve_memory", "finalize"],
    "research": ["retrieve_memory", "retrieve_kb", "tool_hint", "task_graph", "reflection_v2", "finalize"],
    "code": ["retrieve_memory", "retrieve_kb", "tool_hint", "task_graph", "reflection_v2", "finalize"],
    "file": ["retrieve_memory", "retrieve_kb", "tool_hint", "task_graph", "reflection_v2", "finalize"],
    "multi_step": ["retrieve_memory", "retrieve_kb", "tool_hint", "planner", "reflection_v2", "finalize"],
}


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
Ты evaluator.

Верни ТОЛЬКО JSON-объект формата:
{{
  "answered": true,
  "grounded": true,
  "complete": true,
  "actionable": true,
  "safe": true,
  "needs_retry": false,
  "notes": "короткое объяснение",
  "improved_answer": "улучшенная версия ответа"
}}

Правила:
- answered = ответил ли ответ на исходную задачу.
- grounded = опирается ли ответ на доступный контекст.
- complete = нет ли заметных пропусков.
- actionable = есть ли полезный следующий шаг или практический вывод.
- safe = нет ли явных галлюцинаций, опасных или необоснованных утверждений.
- needs_retry = true, если ответ нужно пересобрать заново.
- improved_answer = либо улучшенный ответ, либо исходный, если улучшение не требуется.

ЗАДАЧА:
{task}

КОНТЕКСТ:
{context[:7000]}

ОТВЕТ:
{(answer or '')[:9000]}
"""
    raw = ask_model(
        model_name=model_name,
        profile_name="Аналитик",
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
Пересобери ответ лучше.

Исходная задача:
{task}

Проблемы прошлого ответа:
{reflection_notes}

Прошлый ответ:
{prior_answer[:4000]}

Требования:
- дай более точный и полезный ответ,
- не выдумывай факты,
- если данных не хватает — скажи это явно,
- если уместно, дай следующий практический шаг.
"""
    return ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
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

    if any(x in text for x in ["что такое", "объясни", "кратко", "простыми словами", "short", "summary"]):
        scores["direct"] += 0.80

    if any(x in text for x in ["план", "roadmap", "по шагам", "шаги внедрения", "стратегия внедрения"]):
        scores["planner"] += 0.95

    if any(x in text for x in ["исследуй", "сравни", "документац", "практики", "best practices", "найди", "langgraph", "crewai"]):
        scores["task_graph"] += 0.80

    if any(x in text for x in ["архитектур", "рефактор", "проанализируй проект", "кодовые изменения", "review code"]):
        scores["multi_agent"] += 0.95

    if any(x in text for x in ["улучши ответ", "самоанализ", "self-improve", "перепроверь", "доработай ответ"]):
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
        _progress(1, "🧠 Память")
        s["memory_context"] = build_memory_context(task, memory_profile, top_k=8)
        if s["memory_context"].strip():
            _wm("retrieve_memory", "finding", s["memory_context"][:2000], score=0.9)
        _refresh_working()
        return s

    def h_retrieve_kb(s: dict) -> dict:
        _progress(2, "📚 KB")
        s["kb_context"] = build_kb_context(task, profile_name=memory_profile, top_k=4)
        if s["kb_context"].strip():
            _wm("retrieve_kb", "source", s["kb_context"][:2000], score=0.85)
        _refresh_working()
        return s

    def h_retrieve_working_memory(s: dict) -> dict:
        _progress(3, "🧩 Working memory")
        _refresh_working()
        return s

    def h_tool_hint(s: dict) -> dict:
        _progress(4, "🛠 Tool memory")
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
            s["tool_hint"] = "Предпочтительные инструменты по прошлому опыту:\n" + "\n".join(lines)
            _wm("tool_hint", "decision", s["tool_hint"][:1800], score=0.75)
        else:
            s["tool_hint"] = ""
        _refresh_working()
        return s

    def h_planner(s: dict) -> dict:
        _progress(5, "🧭 Planner")
        plan = run_planner_agent(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=None)
        s["plan_result"] = plan
        s["answer"] = plan.get("final") or plan.get("summary") or ""
        if plan:
            _wm("planner", "decision", str(plan)[:2500], score=0.85)
        _record_tool("planner_agent", True, "run_planner_agent")
        _refresh_working()
        return s

    def h_task_graph(s: dict) -> dict:
        _progress(5, "🕸 Task Graph")
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
        _progress(5, "🤝 Multi-Agent")
        result = run_multi_agent(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=None)
        s["multi_agent_result"] = result
        s["answer"] = (result or {}).get("final", "") or s.get("answer", "")
        if result:
            _wm("multi_agent", "finding", str(result)[:3000], score=0.92)
        _record_tool("multi_agent", True, "run_multi_agent")
        _refresh_working()
        return s

    def h_self_improve(s: dict) -> dict:
        _progress(5, "♻️ Self-Improving")
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
        _progress(6, "🪞 Reflection v2")
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
        _progress(total_steps, "✅ Final")
        if not s.get("answer", "").strip():
            fallback_prompt = f"""
Собери финальный ответ по задаче.

Задача:
{task}

Контекст памяти:
{s.get("memory_context", "")[:5000]}

Контекст KB:
{s.get("kb_context", "")[:4000]}

Рабочая память:
{s.get("working_context", "")[:4000]}

Подсказка по инструментам:
{s.get("tool_hint", "")[:1500]}

Требования:
- ответ должен быть конкретным,
- не выдумывай факты,
- если данных мало, так и скажи,
- дай следующий практический шаг.
"""
            s["answer"] = ask_model(
                model_name=model_name,
                profile_name="Оркестратор",
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
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SELF-IMPROVING AGENT
# ═══════════════════════════════════════════════════════════════════════════════

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

    _progress(1, "🚀 Базовый запуск V8")
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
        _progress(min(idx + 1, total_steps), f"🪞 Self-Improve {idx}")
        mem_ctx = build_memory_context(task, memory_profile, top_k=8)
        kb_ctx = build_kb_context(task, profile_name=memory_profile, top_k=4)
        if run_id:
            try:
                working_context = build_working_memory_context(run_id, profile_name=memory_profile, limit=12)
            except Exception:
                pass

        combined_context = (mem_ctx or "") + "\n\n" + (kb_ctx or "") + "\n\n" + (working_context or "")
        critique_prompt = f"""
Ты self-improve critic.
Верни ТОЛЬКО JSON:
{{
  "improve": true,
  "score": 0.0,
  "issues": ["..."],
  "focus": "что улучшить"
}}

ЗАДАЧА:
{task}

ТЕКУЩИЙ ОТВЕТ:
{answer[:9000]}

REFLECTION:
{json.dumps(reflection, ensure_ascii=False)}

КОНТЕКСТ:
{combined_context[:9000]}
"""
        raw_crit = ask_model(
            model_name=model_name,
            profile_name="Аналитик",
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
Улучши ответ после self-improving loop.

Исходная задача:
{task}

Текущий ответ:
{answer[:9000]}

Проблемы / focus:
{json.dumps(crit, ensure_ascii=False, indent=2)}

Reflection:
{json.dumps(reflection, ensure_ascii=False, indent=2)}

Контекст памяти:
{mem_ctx[:4000]}

Контекст KB:
{kb_ctx[:3000]}

Рабочая память:
{working_context[:3000]}

Требования:
- Сделай ответ точнее и практичнее.
- Не выдумывай факты.
- Если данных не хватает — скажи это явно.
- Сохрани сильные части прошлого ответа.
"""
        improved = ask_model(
            model_name=model_name,
            profile_name="Оркестратор",
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
    }
