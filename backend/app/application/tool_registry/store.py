from __future__ import annotations

import json
from typing import Any, Callable


def now_utc_iso(now_func: Callable[[], str]) -> str:
    return now_func()


def init_db(
    *,
    conn_factory: Callable[[], Any],
    create_sql: str,
) -> None:
    with conn_factory() as con:
        con.executescript(create_sql)


def row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    if "parameters_schema_json" in data:
        try:
            data["parameters_schema"] = json.loads(data["parameters_schema_json"])
        except (json.JSONDecodeError, TypeError):
            data["parameters_schema"] = {}
        del data["parameters_schema_json"]
    if "enabled" in data:
        data["enabled"] = bool(data["enabled"])
    return data


def register_tool(
    *,
    conn_factory: Callable[[], Any],
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    now_func: Callable[[], str],
    get_tool_func: Callable[[str], dict[str, Any] | None],
    name: str,
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    display_name: str = "",
    display_name_ru: str = "",
    description: str = "",
    description_ru: str = "",
    category: str = "general",
    parameters_schema: dict[str, Any] | None = None,
    source: str = "builtin",
) -> dict[str, Any]:
    handlers[name] = handler
    now = now_func()

    with conn_factory() as con:
        con.execute(
            """INSERT INTO tools
               (name, display_name, display_name_ru, description, description_ru,
                category, parameters_schema_json, source, enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                display_name=excluded.display_name,
                display_name_ru=excluded.display_name_ru,
                description=excluded.description,
                description_ru=excluded.description_ru,
                category=excluded.category,
                parameters_schema_json=excluded.parameters_schema_json,
                source=excluded.source,
                updated_at=excluded.updated_at""",
            (
                name,
                display_name,
                display_name_ru,
                description,
                description_ru,
                category,
                json.dumps(parameters_schema or {}, ensure_ascii=False),
                source,
                now,
                now,
            ),
        )
    return get_tool_func(name) or {"name": name}


def register_tool_from_dict(
    *,
    register_tool_func: Callable[..., dict[str, Any]],
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    noop_handler: Callable[[dict[str, Any]], dict[str, Any]],
    tool_def: dict[str, Any],
    handler: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    name = tool_def["name"]
    if handler:
        handlers[name] = handler
    return register_tool_func(
        name=name,
        handler=handler or handlers.get(name, noop_handler),
        display_name=tool_def.get("display_name", ""),
        display_name_ru=tool_def.get("display_name_ru", ""),
        description=tool_def.get("description", ""),
        description_ru=tool_def.get("description_ru", ""),
        category=tool_def.get("category", "custom"),
        parameters_schema=tool_def.get("parameters_schema"),
        source=tool_def.get("source", "custom"),
    )


def get_tool(
    *,
    conn_factory: Callable[[], Any],
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    row_to_dict_func: Callable[[Any], dict[str, Any]],
    name: str,
) -> dict[str, Any] | None:
    with conn_factory() as con:
        row = con.execute("SELECT * FROM tools WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    data = row_to_dict_func(row)
    data["has_handler"] = name in handlers
    return data


def list_tools_with_schemas(
    *,
    conn_factory: Callable[[], Any],
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    row_to_dict_func: Callable[[Any], dict[str, Any]],
    category: str | None = None,
    source: str | None = None,
    enabled_only: bool = True,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled_only:
        clauses.append("enabled = 1")
    if category:
        clauses.append("category = ?")
        params.append(category)
    if source:
        clauses.append("source = ?")
        params.append(source)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn_factory() as con:
        rows = con.execute(
            f"SELECT * FROM tools {where} ORDER BY category, name",
            params,
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        data = row_to_dict_func(row)
        data["has_handler"] = data["name"] in handlers
        result.append(data)
    return result


def update_tool(
    *,
    conn_factory: Callable[[], Any],
    now_func: Callable[[], str],
    get_tool_func: Callable[[str], dict[str, Any] | None],
    name: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    allowed = {"display_name", "display_name_ru", "description", "description_ru", "category", "enabled"}
    sets: list[str] = []
    params: list[Any] = []

    for key, val in updates.items():
        if val is None:
            continue
        if key in allowed:
            if key == "enabled":
                val = 1 if val else 0
            sets.append(f"{key} = ?")
            params.append(val)
        elif key == "parameters_schema":
            sets.append("parameters_schema_json = ?")
            params.append(json.dumps(val, ensure_ascii=False))

    if not sets:
        return get_tool_func(name) or {}

    sets.append("updated_at = ?")
    params.append(now_func())
    params.append(name)

    with conn_factory() as con:
        con.execute(f"UPDATE tools SET {', '.join(sets)} WHERE name = ?", params)
    return get_tool_func(name) or {}


def delete_tool(
    *,
    conn_factory: Callable[[], Any],
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    name: str,
) -> dict[str, Any]:
    handlers.pop(name, None)
    with conn_factory() as con:
        con.execute("DELETE FROM tools WHERE name = ?", (name,))
    return {"name": name, "deleted": True}


def execute_tool(
    *,
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    get_tool_func: Callable[[str], dict[str, Any] | None],
    name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = args or {}
    handler = handlers.get(name)
    if not handler:
        return {"ok": False, "error": f"No handler for tool: {name}"}

    tool_meta = get_tool_func(name)
    if tool_meta and not tool_meta.get("enabled", True):
        return {"ok": False, "error": f"Tool '{name}' is disabled"}

    try:
        result = handler(payload)
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def validate_tool_args(
    *,
    get_tool_func: Callable[[str], dict[str, Any] | None],
    name: str,
    args: dict[str, Any],
) -> list[str]:
    tool = get_tool_func(name)
    if not tool:
        return [f"Tool '{name}' not found"]
    schema = tool.get("parameters_schema", {})
    required = schema.get("required", [])
    errors: list[str] = []
    for field in required:
        if field not in args:
            errors.append(f"Missing required field: {field}")
    return errors
