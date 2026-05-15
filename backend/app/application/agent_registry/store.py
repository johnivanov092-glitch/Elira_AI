from __future__ import annotations

import json
import uuid
from typing import Any, Callable


def init_db(*, conn_factory: Callable[[], Any], create_sql: str) -> None:
    with conn_factory() as con:
        con.executescript(create_sql)


def row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("capabilities_json", "tags_json", "config_json"):
        if key in data:
            try:
                data[key.replace("_json", "")] = json.loads(data[key])
            except (json.JSONDecodeError, TypeError):
                data[key.replace("_json", "")] = [] if "capabilities" in key or "tags" in key else {}
            del data[key]
    if "enabled" in data:
        data["enabled"] = bool(data["enabled"])
    return data


def register_agent(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    get_agent_func: Callable[[str], dict[str, Any] | None],
    update_agent_func: Callable[[str, dict[str, Any]], dict[str, Any]],
    agent_def: dict[str, Any],
) -> dict[str, Any]:
    agent_id = agent_def.get("id") or f"agent-{uuid.uuid4().hex[:8]}"
    now = now_func()

    with conn_factory() as con:
        existing = con.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if existing:
            return update_agent_func(agent_id, agent_def)

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
                now,
                now,
            ),
        )

    return get_agent_func(agent_id) or {"id": agent_id}


def get_agent(
    *,
    conn_factory: Callable[[], Any],
    row_to_dict_func: Callable[[Any], dict[str, Any]],
    agent_id: str,
) -> dict[str, Any] | None:
    with conn_factory() as con:
        row = con.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return row_to_dict_func(row) if row else None


def list_agents(
    *,
    conn_factory: Callable[[], Any],
    row_to_dict_func: Callable[[Any], dict[str, Any]],
    role: str | None = None,
    enabled_only: bool = True,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled_only:
        clauses.append("enabled = 1")
    if role:
        clauses.append("role = ?")
        params.append(role)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn_factory() as con:
        rows = con.execute(f"SELECT * FROM agents {where} ORDER BY name", params).fetchall()

    agents = [row_to_dict_func(row) for row in rows]
    if tag:
        agents = [agent for agent in agents if tag in agent.get("tags", [])]
    return agents


def delete_unknown_builtin_agents(
    *,
    conn_factory: Callable[[], Any],
    valid_agent_ids: set[str],
) -> int:
    valid_ids = {str(agent_id).strip() for agent_id in valid_agent_ids if str(agent_id).strip()}
    if not valid_ids:
        return 0

    with conn_factory() as con:
        rows = con.execute("SELECT id FROM agents WHERE id LIKE 'builtin-%'").fetchall()
        stale_ids = [
            str(row["id"])
            for row in rows
            if str(row["id"]) not in valid_ids
        ]
        if not stale_ids:
            return 0
        for agent_id in stale_ids:
            con.execute("DELETE FROM agent_runs WHERE agent_id = ?", (agent_id,))
            con.execute("DELETE FROM agent_state WHERE agent_id = ?", (agent_id,))
            con.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    return len(stale_ids)


def update_agent(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    get_agent_func: Callable[[str], dict[str, Any] | None],
    agent_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    allowed = {
        "name",
        "name_ru",
        "description",
        "description_ru",
        "role",
        "system_prompt",
        "model_preference",
        "enabled",
    }
    json_fields = {"capabilities": "capabilities_json", "tags": "tags_json", "config": "config_json"}

    sets: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if value is None:
            continue
        if key in allowed:
            if key == "enabled":
                value = 1 if value else 0
            sets.append(f"{key} = ?")
            params.append(value)
        elif key in json_fields:
            sets.append(f"{json_fields[key]} = ?")
            params.append(json.dumps(value, ensure_ascii=False))

    if not sets:
        return get_agent_func(agent_id) or {}

    sets.append("updated_at = ?")
    params.append(now_func())
    params.append(agent_id)
    with conn_factory() as con:
        con.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = ?", params)
    return get_agent_func(agent_id) or {}


def delete_agent(*, conn_factory: Callable[[], Any], now_func: Callable[[], str], agent_id: str) -> dict[str, Any]:
    with conn_factory() as con:
        con.execute("UPDATE agents SET enabled = 0, updated_at = ? WHERE id = ?", (now_func(), agent_id))
    return {"id": agent_id, "deleted": True}


def get_agent_state(*, conn_factory: Callable[[], Any], agent_id: str) -> dict[str, Any]:
    with conn_factory() as con:
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


def set_agent_state(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    get_agent_state_func: Callable[[str], dict[str, Any]],
    agent_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    now = now_func()
    with conn_factory() as con:
        con.execute(
            """INSERT INTO agent_state (agent_id, state_json, last_active_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET state_json = excluded.state_json,
               last_active_at = excluded.last_active_at""",
            (agent_id, json.dumps(state, ensure_ascii=False), now),
        )
    return get_agent_state_func(agent_id)


def record_agent_run(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    get_agent_state_func: Callable[[str], dict[str, Any]],
    set_agent_state_func: Callable[[str, dict[str, Any]], dict[str, Any]],
    run_data: dict[str, Any],
) -> dict[str, Any]:
    now = now_func()
    run_id = run_data.get("run_id") or uuid.uuid4().hex
    agent_id = run_data.get("agent_id", "")

    with conn_factory() as con:
        con.execute(
            """INSERT INTO agent_runs
               (agent_id, run_id, input_summary, output_summary, ok, route,
                model_used, duration_ms, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_id,
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

    current_state = get_agent_state_func(agent_id).get("state", {})
    set_agent_state_func(agent_id, current_state)
    return {"run_id": run_id, "recorded": True}


def get_agent_runs(
    *,
    conn_factory: Callable[[], Any],
    agent_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    with conn_factory() as con:
        total_row = con.execute(
            "SELECT COUNT(*) as cnt FROM agent_runs WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        total = total_row["cnt"] if total_row else 0
        rows = con.execute(
            """SELECT * FROM agent_runs WHERE agent_id = ?
               ORDER BY started_at DESC LIMIT ? OFFSET ?""",
            (agent_id, limit, offset),
        ).fetchall()

    runs: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["ok"] = bool(item.get("ok"))
        runs.append(item)
    return runs, total


def resolve_agent(
    *,
    get_agent_func: Callable[[str], dict[str, Any] | None],
    list_agents_func: Callable[..., list[dict[str, Any]]],
    agent_id: str | None = None,
    role: str | None = None,
) -> dict[str, Any] | None:
    if agent_id:
        return get_agent_func(agent_id)
    if role:
        agents = list_agents_func(role=role, enabled_only=True)
        return agents[0] if agents else None
    return None
