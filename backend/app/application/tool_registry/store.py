from __future__ import annotations

import json
from typing import Any, Callable

from app.application.agent_kernel.tool_result import ensure_tool_result

_TOOLSPEC_NEW_COLUMNS = [
    ("permission",       "TEXT NOT NULL DEFAULT 'auto'"),
    ("side_effect",      "INTEGER NOT NULL DEFAULT 0"),
    ("scopes",           "TEXT NOT NULL DEFAULT '[]'"),
    ("timeout_seconds",  "INTEGER NOT NULL DEFAULT 30"),
    ("max_output_chars", "INTEGER NOT NULL DEFAULT 50000"),
    ("idempotent",       "INTEGER NOT NULL DEFAULT 0"),
    # Legacy inventory metadata. The unified executor does not use it as a gate.
    ("policy_classified", "INTEGER NOT NULL DEFAULT 0"),
]

def migrate_toolspec_columns(*, conn_factory: Callable[[], Any]) -> None:
    """Add legacy-compatible inventory columns without changing authorization."""
    with conn_factory() as con:
        existing = {row[1] for row in con.execute("PRAGMA table_info(tools)").fetchall()}
        for col_name, col_def in _TOOLSPEC_NEW_COLUMNS:
            if col_name not in existing:
                con.execute(f"ALTER TABLE tools ADD COLUMN {col_name} {col_def}")


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
    if "scopes" in data:
        try:
            data["scopes"] = json.loads(data["scopes"])
        except (json.JSONDecodeError, TypeError):
            data["scopes"] = []
    for _bool_col in ("side_effect", "idempotent", "policy_classified"):
        if _bool_col in data:
            data[_bool_col] = bool(data[_bool_col])
    if data.get("permission") not in {"auto", "require_approval"}:
        data["permission"] = "require_approval"
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
    permission: str = "auto",
    side_effect: bool = False,
    scopes: list[str] | None = None,
    timeout_seconds: int = 30,
    max_output_chars: int = 50000,
    idempotent: bool = False,
    enabled: bool = True,
    policy_classified: bool = True,
) -> dict[str, Any]:
    stored_permission = "auto" if permission == "auto" else "require_approval"
    handlers[name] = handler
    now = now_func()

    with conn_factory() as con:
        # `enabled` is capability lifecycle metadata and is deliberately not
        # overwritten on conflict. It is not an authorization decision.
        con.execute(
            """INSERT INTO tools
               (name, display_name, display_name_ru, description, description_ru,
                category, parameters_schema_json, source, enabled, version, created_at, updated_at,
                permission, side_effect, scopes, timeout_seconds, max_output_chars, idempotent,
                policy_classified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                display_name=excluded.display_name,
                display_name_ru=excluded.display_name_ru,
                description=excluded.description,
                description_ru=excluded.description_ru,
                category=excluded.category,
                parameters_schema_json=excluded.parameters_schema_json,
                source=excluded.source,
                permission=excluded.permission,
                side_effect=excluded.side_effect,
                scopes=excluded.scopes,
                timeout_seconds=excluded.timeout_seconds,
                max_output_chars=excluded.max_output_chars,
                idempotent=excluded.idempotent,
                policy_classified=excluded.policy_classified,
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
                1 if enabled else 0,
                now,
                now,
                stored_permission,
                1 if side_effect else 0,
                json.dumps(scopes or [], ensure_ascii=False),
                timeout_seconds,
                max_output_chars,
                1 if idempotent else 0,
                1 if policy_classified else 0,
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
    permission = tool_def.get("permission", "require_approval")
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
        permission=permission,
        side_effect=tool_def.get("side_effect", False),
        scopes=tool_def.get("scopes"),
        timeout_seconds=tool_def.get("timeout_seconds", 30),
        max_output_chars=tool_def.get("max_output_chars", 50000),
        idempotent=tool_def.get("idempotent", False),
        enabled=tool_def.get("enabled", True),
        policy_classified=tool_def.get("policy_classified", True),
    )


def register_dynamic_tool(
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
    source: str = "plugin",
    side_effect: bool = True,
    scopes: list[str] | None = None,
    timeout_seconds: int = 30,
    max_output_chars: int = 50000,
    idempotent: bool = False,
) -> dict[str, Any]:
    """Register a dynamically-discovered tool in the shared inventory.

    New tools are immediately visible and approval-required. The Workflow
    permission remains the only product authorization decision.

    The in-process handler is always (re)bound so dispatch points at current code.
    """
    handlers[name] = handler
    now = now_func()
    existing = get_tool_func(name)

    with conn_factory() as con:
        if existing is None:
            con.execute(
                """INSERT INTO tools
                   (name, display_name, display_name_ru, description, description_ru,
                    category, parameters_schema_json, source, enabled, version, created_at, updated_at,
                    permission, side_effect, scopes, timeout_seconds, max_output_chars, idempotent,
                    policy_classified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, 'require_approval', ?, ?, ?, ?, ?, 1)""",
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
                    1 if side_effect else 0,
                    json.dumps(scopes or [], ensure_ascii=False),
                    timeout_seconds,
                    max_output_chars,
                    1 if idempotent else 0,
                ),
            )
        else:
            con.execute(
                """UPDATE tools SET
                    display_name = ?,
                    display_name_ru = ?,
                    description = ?,
                    description_ru = ?,
                    category = ?,
                    parameters_schema_json = ?,
                    source = ?,
                    updated_at = ?
                   WHERE name = ?""",
                (
                    display_name,
                    display_name_ru,
                    description,
                    description_ru,
                    category,
                    json.dumps(parameters_schema or {}, ensure_ascii=False),
                    source,
                    now,
                    name,
                ),
            )
    return get_tool_func(name) or {"name": name}


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
    allowed = {
        "display_name", "display_name_ru", "description", "description_ru", "category", "enabled",
        "permission", "timeout_seconds", "max_output_chars",
    }
    bool_cols = {"enabled", "side_effect", "idempotent", "policy_classified"}
    sets: list[str] = []
    params: list[Any] = []

    for key, val in updates.items():
        if val is None:
            continue
        if key in allowed or key in bool_cols:
            if key in bool_cols:
                val = 1 if val else 0
            elif key == "permission":
                val = "auto" if val == "auto" else "require_approval"
            sets.append(f"{key} = ?")
            params.append(val)
        elif key == "parameters_schema":
            sets.append("parameters_schema_json = ?")
            params.append(json.dumps(val, ensure_ascii=False))
        elif key == "scopes":
            sets.append("scopes = ?")
            params.append(json.dumps(val if isinstance(val, list) else [], ensure_ascii=False))

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

    try:
        return ensure_tool_result(
            handler(payload),
            source=f"registered tool {name!r}",
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "text": f"ERROR: {exc}"}


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
