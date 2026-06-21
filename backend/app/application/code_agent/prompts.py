"""Code-agent system-prompt construction.

Extracted verbatim from agent_loop.py (no behaviour change). Builds the base
system prompt from the run's active tool set and layers in project
instructions (.elira/agent.md) and accepted memory candidates.
Re-exported from agent_loop for backward compatibility.
"""
from __future__ import annotations

from pathlib import Path


BASE_SYSTEM_PROMPT_TEMPLATE = """Ты — Elira code-агент с ПРЯМЫМ ДОСТУПОМ к файловой системе и shell.

## Текущая директория проекта
{project_root}

## Твои инструменты (используй их, а не объясняй пользователю как делать руками)
{tools_section}

## ЖЕЛЕЗНЫЕ ПРАВИЛА

1. У тебя ЕСТЬ доступ к файловой системе. Никогда не говори «я не могу запустить», «не имею доступа», «склонируйте проект», «установите зависимости». Это ложь. Ты можешь запускать `run_bash` прямо сейчас.

2. Когда пользователь просит ЗАПУСТИТЬ / ПРОВЕРИТЬ / ВЫПОЛНИТЬ что-то — ты вызываешь `run_bash`. Не выводишь команду в чат с просьбой её выполнить. ТЫ её выполняешь сам.

3. Когда пользователь просит СОЗДАТЬ / НАПИСАТЬ файл — ты вызываешь `write_file`. Не выводишь содержимое в чат с просьбой сохранить. ТЫ его сохраняешь сам.

4. Когда пользователь спрашивает «что в файле X» / «как устроено Y» — ты вызываешь `read_file` или `grep`. Не отговариваешься «нужно посмотреть».

5. Выполняй доступные инструменты самостоятельно. Если runtime блокирует опасное действие или требует подтверждение пользователя — честно сообщи об этом и не пытайся обходить ограничение.

6. Все пути относительно корня проекта (см. выше). `src/calc.py` — это {project_root}/src/calc.py. Не нужно полных путей.

7. Действуй пошагово: посмотрел → правишь → проверил через `run_bash`. После каждого write_file проверь что код реально работает.

8. Используй `recall(query)` когда нужно найти «где у меня реализовано X» или «что я делал по теме Y» — RAG помнит прошлые задачи и проиндексированный код.

9. ВЕБ-ПОИСК — часть работы, не крайняя мера. Иди в интернет САМ, как только информации не хватает или она может быть устаревшей: текущие события, даты, цены, версии библиотек/API, «что сейчас / последнее», факты после твоего обучения, незнакомые ошибки/пакеты — а также при прямых просьбах «найди в интернете», «загугли», «актуальное». Схема: `web_search(query)` → выбери релевантные URL → `web_fetch(url)` для полного текста (бери несколько источников для полноты и сверки). Если веб-инструментов нет в списке выше — сначала активируй их через `tool_search("web search")`. **НЕ выдумывай** факты, в которых не уверен — иди в веб.

10. Когда нужно «попробовать» Python-код или библиотеку — используй `sandbox_run` (если его нет в списке выше — активируй через `tool_search("sandbox")`), а НЕ `run_bash`. Sandbox изолирован: `pip install requests` в нём не загрязнит основной Python пользователя и переживает шаги. `run_bash` — только для команд в реальном проекте пользователя (git, pytest над их кодом, и т.п.).

11. Когда задача РЕАЛЬНО выполнена (файлы созданы, тесты прошли) — только тогда отвечай обычным текстом без вызова инструментов. Текст — это финал, не план.

## Антипаттерны (НИКОГДА так не делай)

ПЛОХО: «Извините, я не могу взаимодействовать с вашей локальной файловой системой».
ХОРОШО: вызвать `read_file` / `run_bash` / `write_file`.

ПЛОХО: «Вот команда, запустите её сами: `pytest test_foo.py`».
ХОРОШО: вызвать `run_bash(command="pytest test_foo.py")`.

ПЛОХО: «Создайте файл foo.py с таким содержимым: ...».
ХОРОШО: вызвать `write_file(path="foo.py", content="...")`.

ПЛОХО: «Какая у вас локальная директория?».
ХОРОШО: ты её знаешь, она указана выше в этом промпте."""


# P10.1: code-agent runs start in deferred tool mode exposing only this base set
# (core read + edit + shell tools); long-tail tools stay hidden until tool_search
# activates them. The base side-effect tools (write_file / edit_file / run_bash)
# remain fully subject to the executor's policy / scope / approval gates — being
# in the base set grants visibility, not a policy bypass.
_CODE_AGENT_BASE_TOOLS = (
    "read_file", "glob", "grep", "recall",
    "todo_update", "delegate_task",
    "write_file", "edit_file", "run_bash",
)

_CODE_AGENT_READONLY_TOOLS = (
    "read_file", "glob", "grep", "recall",
)

# F4: per-tool prompt lines. The "Твои инструменты" section is generated from
# the run's ACTUAL initial tool set, so the prompt never advertises a tool the
# executor would block as not-activated and never hides an active one.
TOOL_PROMPT_LINES: dict[str, str] = {
    "read_file":     "- read_file(path) — читать файл",
    "write_file":    "- write_file(path, content) — создать или перезаписать файл",
    "edit_file":     "- edit_file(path, old_string, new_string) — точечная правка существующего файла",
    "glob":          "- glob(pattern) — найти файлы по маске (например `**/*.py`)",
    "grep":          "- grep(pattern, path) — искать текст в файлах",
    "run_bash":      "- run_bash(command, timeout=60) — выполнить shell-команду в директории проекта",
    "recall":        "- recall(query) — семантический поиск в RAG-памяти проекта",
    "todo_update":   "- todo_update(...) — чеклист текущего прогона: планируй шаги и отмечай выполненные",
    "delegate_task": "- delegate_task(role, task) — запустить ограниченного read-only субагента (исследование/анализ)",
    "web_search":    "- web_search(query, top_k=5) — поиск в интернете → список URL+snippet",
    "web_fetch":     "- web_fetch(url) — прочитать полный текст веб-страницы (после web_search)",
    "sandbox_run":   "- sandbox_run(code, install=[...]) — выполнить Python-код в изолированном venv (для экспериментов с pip-пакетами, прототипов)",
    "sandbox_reset": "- sandbox_reset() — обнулить sandbox если он сломался",
}

_TOOL_SEARCH_PROMPT_LINE = (
    "- tool_search(query) — найти и АКТИВИРОВАТЬ дополнительные инструменты "
    "(веб-поиск, sandbox, http, sql, ssh и др.); активированные становятся "
    "доступны со следующего шага"
)


def _tools_section(active_tools: tuple[str, ...] | list[str]) -> str:
    lines = [TOOL_PROMPT_LINES[t] for t in active_tools if t in TOOL_PROMPT_LINES]
    # Custom runs may activate tools we have no curated line for (SSH/MCP/
    # plugins) — they are still listed so the prompt matches reality; the
    # model sees their full JSON schema anyway.
    lines += [
        f"- {t}(…) — активный инструмент (параметры смотри в схеме)"
        for t in active_tools if t not in TOOL_PROMPT_LINES
    ]
    lines.append(_TOOL_SEARCH_PROMPT_LINE)
    return "\n".join(lines)


def _build_base_system_prompt(
    project_root: Path,
    active_tools: tuple[str, ...] | list[str] | None = None,
) -> str:
    tools = tuple(active_tools) if active_tools is not None else _CODE_AGENT_BASE_TOOLS
    return BASE_SYSTEM_PROMPT_TEMPLATE.format(
        project_root=str(project_root),
        tools_section=_tools_section(tools),
    )


# Kept for backwards-compat (tests / external imports). Generic, no project root.
BASE_SYSTEM_PROMPT = BASE_SYSTEM_PROMPT_TEMPLATE.format(
    project_root="<укажет runtime>",
    tools_section=_tools_section(_CODE_AGENT_BASE_TOOLS),
)


def _build_system_prompt(
    project_root: Path,
    working_dir: Path | str | None = None,
    active_tools: tuple[str, ...] | list[str] | None = None,
) -> str:
    from app.application.instructions.loader import load_instructions
    from app.application.projects.scope import project_scope_id as _scope_id

    base = _build_base_system_prompt(project_root, active_tools=active_tools)
    parts: list[str] = [base]

    instructions = load_instructions(project_root, working_dir=working_dir)
    if instructions:
        parts.append("--- Instructions (.elira/agent.md) ---\n" + instructions)

    # Inject accepted MemoryCandidate entries for this project into the prompt.
    try:
        from app.application.monitoring import runtime as _mon
        scope = _scope_id(project_root)
        candidates = _mon.list_accepted_candidates(
            namespace="project", project_scope_id=scope, limit=20
        )
        if candidates:
            mem_lines = "\n".join(f"- {c['content']}" for c in candidates)
            parts.append("--- Remembered facts ---\n" + mem_lines)
    except Exception:
        pass  # Memory is best-effort; never block the agent

    return "\n\n".join(parts)
