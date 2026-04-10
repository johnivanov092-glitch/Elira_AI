"""Agent Registry — реестр агентов с персистентным состоянием (Agent OS Phase 1).

Хранит определения агентов, их состояние между вызовами и историю запусков.
БД: data/agent_registry.db
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH: Path = sqlite_data_file("agent_registry.db")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_ru TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    description_ru TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'general',
    system_prompt TEXT NOT NULL DEFAULT '',
    model_preference TEXT NOT NULL DEFAULT '',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id),
    state_json TEXT NOT NULL DEFAULT '{}',
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    input_summary TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    route TEXT NOT NULL DEFAULT '',
    model_used TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_time ON agent_runs(started_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def _init_db() -> None:
    with _conn() as con:
        con.executescript(_CREATE_SQL)


_init_db()


# ── CRUD агентов ──────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("capabilities_json", "tags_json", "config_json"):
        if key in d:
            try:
                d[key.replace("_json", "")] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key.replace("_json", "")] = [] if "capabilities" in key or "tags" in key else {}
            del d[key]
    if "enabled" in d:
        d["enabled"] = bool(d["enabled"])
    return d


def register_agent(agent_def: dict) -> dict:
    """Регистрирует нового агента или обновляет существующего (upsert по id)."""
    agent_id = agent_def.get("id") or f"agent-{uuid.uuid4().hex[:8]}"
    now = _now()

    with _conn() as con:
        existing = con.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if existing:
            return update_agent(agent_id, agent_def)

        con.execute(
            """INSERT INTO agents
               (id, name, name_ru, description, description_ru, role,
                system_prompt, model_preference, capabilities_json,
                tags_json, config_json, enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_id,
                agent_def.get("name", agent_id),
                agent_def.get("name_ru", ""),
                agent_def.get("description", ""),
                agent_def.get("description_ru", ""),
                agent_def.get("role", "general"),
                agent_def.get("system_prompt", ""),
                agent_def.get("model_preference", ""),
                json.dumps(agent_def.get("capabilities", []), ensure_ascii=False),
                json.dumps(agent_def.get("tags", []), ensure_ascii=False),
                json.dumps(agent_def.get("config", {}), ensure_ascii=False),
                1 if agent_def.get("enabled", True) else 0,
                agent_def.get("version", 1),
                now, now,
            ),
        )

    return get_agent(agent_id)  # type: ignore[return-value]


def get_agent(agent_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_agents(
    role: str | None = None,
    enabled_only: bool = True,
    tag: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if enabled_only:
        clauses.append("enabled = 1")
    if role:
        clauses.append("role = ?")
        params.append(role)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM agents {where} ORDER BY name", params
        ).fetchall()

    agents = [_row_to_dict(r) for r in rows]

    if tag:
        agents = [a for a in agents if tag in a.get("tags", [])]

    return agents


def update_agent(agent_id: str, updates: dict) -> dict:
    """Частичное обновление полей агента."""
    allowed = {
        "name", "name_ru", "description", "description_ru", "role",
        "system_prompt", "model_preference", "enabled",
    }
    json_fields = {"capabilities": "capabilities_json", "tags": "tags_json", "config": "config_json"}

    sets: list[str] = []
    params: list = []

    for key, val in updates.items():
        if val is None:
            continue
        if key in allowed:
            if key == "enabled":
                val = 1 if val else 0
            sets.append(f"{key} = ?")
            params.append(val)
        elif key in json_fields:
            sets.append(f"{json_fields[key]} = ?")
            params.append(json.dumps(val, ensure_ascii=False))

    if not sets:
        return get_agent(agent_id) or {}

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(agent_id)

    with _conn() as con:
        con.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = ?", params)

    return get_agent(agent_id) or {}


def delete_agent(agent_id: str) -> dict:
    """Мягкое удаление — ставит enabled=0."""
    with _conn() as con:
        con.execute("UPDATE agents SET enabled = 0, updated_at = ? WHERE id = ?", (_now(), agent_id))
    return {"id": agent_id, "deleted": True}


# ── Состояние агента ──────────────────────────────────────────

def get_agent_state(agent_id: str) -> dict:
    with _conn() as con:
        row = con.execute(
            "SELECT state_json, last_active_at FROM agent_state WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
    if not row:
        return {"agent_id": agent_id, "state": {}, "last_active_at": None}
    try:
        state = json.loads(row["state_json"])
    except (json.JSONDecodeError, TypeError):
        state = {}
    return {"agent_id": agent_id, "state": state, "last_active_at": row["last_active_at"]}


def set_agent_state(agent_id: str, state: dict) -> dict:
    now = _now()
    with _conn() as con:
        con.execute(
            """INSERT INTO agent_state (agent_id, state_json, last_active_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET state_json = excluded.state_json,
               last_active_at = excluded.last_active_at""",
            (agent_id, json.dumps(state, ensure_ascii=False), now),
        )
    return get_agent_state(agent_id)


# ── История запусков ──────────────────────────────────────────

def record_agent_run(run_data: dict) -> dict:
    """Записать результат запуска агента."""
    now = _now()
    run_id = run_data.get("run_id") or uuid.uuid4().hex
    with _conn() as con:
        con.execute(
            """INSERT INTO agent_runs
               (agent_id, run_id, input_summary, output_summary, ok, route,
                model_used, duration_ms, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_data.get("agent_id", ""),
                run_id,
                run_data.get("input_summary", "")[:500],
                run_data.get("output_summary", "")[:500],
                1 if run_data.get("ok") else 0,
                run_data.get("route", ""),
                run_data.get("model_used", ""),
                run_data.get("duration_ms", 0),
                run_data.get("started_at", now),
                run_data.get("finished_at", now),
            ),
        )
    # Обновить last_active_at
    set_agent_state(
        run_data.get("agent_id", ""),
        get_agent_state(run_data.get("agent_id", "")).get("state", {}),
    )
    return {"run_id": run_id, "recorded": True}


def get_agent_runs(
    agent_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    with _conn() as con:
        total_row = con.execute(
            "SELECT COUNT(*) as cnt FROM agent_runs WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        rows = con.execute(
            """SELECT * FROM agent_runs WHERE agent_id = ?
               ORDER BY started_at DESC LIMIT ? OFFSET ?""",
            (agent_id, limit, offset),
        ).fetchall()

    runs = []
    for r in rows:
        d = dict(r)
        d["ok"] = bool(d.get("ok"))
        runs.append(d)

    return runs, total


# ── Seed встроенных агентов ────────────────────────────────────

_BUILTIN_AGENTS_SEEDED = False


def seed_builtin_agents() -> int:
    """Создаёт встроенных агентов из AGENT_PROFILES если их нет в БД."""
    global _BUILTIN_AGENTS_SEEDED
    if _BUILTIN_AGENTS_SEEDED:
        return 0

    from app.core.config import AGENT_PROFILES, AGENT_PROFILE_UI

    # Маппинг по подстроке ключа (обход проблем кодировки в некоторых файлах)
    _role_defs = [
        ("ниверсальн", "general", "Universal", "builtin-universal"),
        ("сследовател", "researcher", "Researcher", "builtin-researcher"),
        ("рограммист", "programmer", "Programmer", "builtin-programmer"),
        ("налитик", "analyst", "Analyst", "builtin-analyst"),
        ("ократ", "teacher", "Socrat", "builtin-socrat"),
    ]

    def _match_role(name_ru: str):
        lower = name_ru.lower()
        for substr, role, name_en, aid in _role_defs:
            if substr in lower:
                return role, name_en, aid
        return "custom", name_ru, f"builtin-{name_ru.lower()[:20]}"

    created = 0
    for name_ru, prompt in AGENT_PROFILES.items():
        role, name_en, agent_id = _match_role(name_ru)
        ui = AGENT_PROFILE_UI.get(name_ru, {})

        existing = get_agent(agent_id)
        if existing:
            continue

        register_agent({
            "id": agent_id,
            "name": name_en,
            "name_ru": name_ru,
            "description": ui.get("short", ""),
            "description_ru": ui.get("short", ""),
            "role": role,
            "system_prompt": prompt,
            "tags": ui.get("tags", []),
            "config": {"icon": ui.get("icon", "")},
        })
        created += 1

    extra_builtins = [
        {
            "id": "builtin-orchestrator",
            "name": "Orchestrator",
            "name_ru": "Оркестратор",
            "description": "Plans and synthesizes multi-step workflows.",
            "description_ru": "Планирует и собирает итог многошаговых workflow.",
            "role": "orchestrator",
            "system_prompt": (
                "Ты Оркестратор. Разбивай сложные задачи на шаги, собирай итоговые выводы, "
                "держи структуру ответа и помогай агентам работать согласованно."
            ),
            "tags": ["workflow", "planning", "coordination"],
            "config": {"icon": "◎"},
        },
        {
            "id": "builtin-reviewer",
            "name": "Reviewer",
            "name_ru": "Ревьюер",
            "description": "Critiques intermediate and final results.",
            "description_ru": "Проверяет промежуточные и финальные результаты.",
            "role": "reviewer",
            "system_prompt": (
                "Ты Ревьюер. Проверяй ответы на логические пробелы, слабые места, риски и "
                "недостающие улучшения. Пиши конкретно и полезно."
            ),
            "tags": ["review", "quality", "critique"],
            "config": {"icon": "◌"},
        },
    ]

    for agent_def in extra_builtins:
        if get_agent(agent_def["id"]):
            continue
        register_agent(agent_def)
        created += 1

    _BUILTIN_AGENTS_SEEDED = True
    return created


# ── Resolve agent для интеграции с agents_service ──────────────

def resolve_agent(agent_id: str | None = None, role: str | None = None) -> dict | None:
    """Ищет агента по id или роли. Для интеграции с run_agent()."""
    if agent_id:
        return get_agent(agent_id)
    if role:
        agents = list_agents(role=role, enabled_only=True)
        return agents[0] if agents else None
    return None
