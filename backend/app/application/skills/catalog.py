"""Skill manifest catalog — discovery and lazy content loading.

A SkillManifest is a lightweight descriptor that can be listed quickly.
Full skill content (system-prompt instructions, examples) is loaded only
when a skill is actually invoked, keeping discovery cheap.

Roles of this module:
  1. ``discover_skills`` — return manifests (no content) for all enabled skills.
  2. ``load_skill_content`` — return the full instruction text for one skill.
  3. Gradual replacement of keyword-based dispatch: callers can iterate
     manifests and pick the best match by trigger_words or capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillManifest:
    """Lightweight descriptor returned by discovery (no full content)."""

    id: str
    name: str
    description_short: str
    capabilities: list[str]
    trigger_words: list[str]
    enabled: bool = True
    # content is intentionally absent here — load with load_skill_content()


# ── Built-in skill registry ───────────────────────────────────────────────────
#
# Each entry: SkillManifest metadata + "content" key with the full prompt text.
# The "content" is loaded lazily via load_skill_content() so discovery stays O(1).

_BUILTIN_SKILLS: list[dict[str, Any]] = [
    {
        "id": "word_excel",
        "name": "Генерация Word/Excel",
        "description_short": "Создаёт документы Word (.docx) и таблицы Excel (.xlsx) по описанию.",
        "capabilities": ["generate_word", "generate_excel", "file_generation"],
        "trigger_words": ["word", "docx", "excel", "xlsx", "документ", "таблица", "сгенерируй файл"],
        "enabled": True,
        "content": (
            "Ты умеешь генерировать документы. Используй инструменты generate_word и generate_excel.\n"
            "generate_word(title, content, filename) — создаёт .docx. Поддерживает markdown: ##, ###, -, 1.\n"
            "generate_excel(title, data, headers, filename) — создаёт .xlsx. data — список строк-списков.\n"
            "Всегда возвращай download_url для скачивания."
        ),
    },
    {
        "id": "sql_query",
        "name": "SQL-запросы",
        "description_short": "Выполняет SQL SELECT/INSERT/UPDATE по базам данных SQLite в data/.",
        "capabilities": ["run_sql", "list_databases", "describe_db"],
        "trigger_words": ["sql", "запрос", "база данных", "sqlite", "select", "таблицы"],
        "enabled": True,
        "content": (
            "Ты выполняешь SQL-запросы к SQLite базам в data/.\n"
            "list_databases() — список доступных баз.\n"
            "describe_db(db_path) — структура таблиц.\n"
            "run_sql(db_path, query, params, max_rows) — выполнить запрос. DROP/DELETE/TRUNCATE/ALTER заблокированы.\n"
            "Всегда проверяй схему перед запросом."
        ),
    },
    {
        "id": "http_api",
        "name": "HTTP/API вызовы",
        "description_short": "Делает HTTP запросы к внешним API (GET/POST/PUT/DELETE).",
        "capabilities": ["http_request"],
        "trigger_words": ["http", "api", "запрос", "curl", "rest", "endpoint", "request"],
        "enabled": True,
        "content": (
            "Ты можешь делать HTTP запросы через http_request(url, method, headers, body, timeout).\n"
            "Поддерживаются GET, POST, PUT, DELETE. Таймаут 15с.\n"
            "Localhost и 127.0.0.1 заблокированы. Парсит JSON автоматически.\n"
            "Возвращает: ok, status, body, url, elapsed_ms."
        ),
    },
    {
        "id": "screenshot",
        "name": "Скриншот страницы",
        "description_short": "Делает скриншот веб-страницы через Playwright (headless Chromium).",
        "capabilities": ["screenshot_url"],
        "trigger_words": ["скриншот", "screenshot", "снимок", "сфотографируй сайт", "сделай скрин"],
        "enabled": True,
        "content": (
            "Ты можешь делать скриншоты веб-страниц.\n"
            "screenshot_url(url, width=1280, height=800, full_page=False) — скриншот URL.\n"
            "Требует Playwright (playwright install chromium). Сохраняет PNG в data/generated/.\n"
            "Возвращает download_url и view_url."
        ),
    },
    {
        "id": "web_research",
        "name": "Веб-поиск и исследование",
        "description_short": "Ищет актуальную информацию в интернете, читает страницы.",
        "capabilities": ["web_search", "web_fetch", "research"],
        "trigger_words": ["найди", "поиск", "google", "интернет", "загугли", "ищи", "последние новости",
                          "свежая информация", "research"],
        "enabled": True,
        "content": (
            "Для поиска: web_search(query) → список URL+snippet.\n"
            "Для чтения страницы: web_fetch(url) → текст. Приватные IP заблокированы (SSRF guard).\n"
            "Стратегия: сначала search, выбери релевантные URL, затем fetch для деталей.\n"
            "Не выдумывай факты — иди в веб."
        ),
    },
    {
        "id": "python_sandbox",
        "name": "Python Sandbox",
        "description_short": "Выполняет Python код в изолированном venv. pip install разрешён.",
        "capabilities": ["sandbox_run", "sandbox_reset", "code_execution"],
        "trigger_words": ["запусти код", "выполни python", "sandbox", "проверь скрипт",
                          "протестируй код", "запусти тест"],
        "enabled": True,
        "content": (
            "sandbox_run(code, install=[]) — выполняет Python в изолированном venv.\n"
            "install — список pip-пакетов для установки перед выполнением.\n"
            "sandbox_reset() — сброс venv если он сломался.\n"
            "Sandbox сохраняет состояние между вызовами в рамках одной сессии.\n"
            "Используй sandbox для экспериментов, run_bash для команд в проекте."
        ),
    },
    {
        "id": "project_patch",
        "name": "Редактирование проекта",
        "description_short": "Читает, редактирует и патчит файлы проекта с backup/rollback.",
        "capabilities": ["read_file", "write_file", "edit_file", "diff_preview", "backup", "rollback"],
        "trigger_words": ["исправь", "поправь", "отредактируй", "измени файл", "создай файл",
                          "напиши код", "patch", "rollback"],
        "enabled": True,
        "content": (
            "Инструменты редактирования файлов проекта:\n"
            "read_file(path) — прочитать файл. write_file(path, content) — создать/перезаписать.\n"
            "edit_file(path, old_string, new_string) — точечная правка существующего файла.\n"
            "preview_project_patch / apply_project_patch — diff-preview перед записью.\n"
            "rollback_project_patch(path, backup_id) — откат к предыдущей версии.\n"
            "write/edit требуют approval. read/preview — автоматически."
        ),
    },
    {
        "id": "memory",
        "name": "Память",
        "description_short": "Семантический поиск по памяти проекта, сохранение фактов.",
        "capabilities": ["search_memory", "recall", "memory_candidate"],
        "trigger_words": ["вспомни", "что ты знаешь о", "помни", "сохрани в памяти",
                          "recall", "memory", "запомни"],
        "enabled": True,
        "content": (
            "recall(query) — семантический поиск в RAG-памяти проекта.\n"
            "search_memory(query) — поиск по глобальной smart_memory.\n"
            "В prompt автоматически добавляются accepted MemoryCandidate для текущего проекта.\n"
            "Для сохранения: создай MemoryCandidate через API, пользователь accept/reject."
        ),
    },
]

_SKILL_INDEX: dict[str, dict[str, Any]] = {s["id"]: s for s in _BUILTIN_SKILLS}


def discover_skills(*, enabled_only: bool = True) -> list[SkillManifest]:
    """Return a list of skill manifests (without full content).

    Parameters
    ----------
    enabled_only:
        When True (default), only enabled skills are returned.

    Returns
    -------
    list[SkillManifest]
        Sorted alphabetically by name for stable ordering.
    """
    skills = list(_SKILL_INDEX.values())
    if enabled_only:
        skills = [s for s in skills if s.get("enabled", True)]
    return sorted(
        [
            SkillManifest(
                id=s["id"],
                name=s["name"],
                description_short=s["description_short"],
                capabilities=list(s.get("capabilities", [])),
                trigger_words=list(s.get("trigger_words", [])),
                enabled=bool(s.get("enabled", True)),
            )
            for s in skills
        ],
        key=lambda m: m.name,
    )


def load_skill_content(skill_id: str) -> str | None:
    """Return the full instruction text for *skill_id*, or None if not found."""
    entry = _SKILL_INDEX.get(skill_id)
    if not entry:
        return None
    return entry.get("content", "")


def match_skills_by_trigger(text: str, *, enabled_only: bool = True, top_k: int = 3) -> list[SkillManifest]:
    """Return skills whose trigger_words appear in *text* (case-insensitive).

    Useful for dispatcher logic: given user text, find relevant skills.
    """
    lowered = text.lower()
    scored: list[tuple[int, SkillManifest]] = []
    for manifest in discover_skills(enabled_only=enabled_only):
        hits = sum(1 for tw in manifest.trigger_words if tw.lower() in lowered)
        if hits:
            scored.append((hits, manifest))
    scored.sort(key=lambda t: -t[0])
    return [m for _, m in scored[:top_k]]
