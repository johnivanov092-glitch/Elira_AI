"""Validate native tool arguments against the published schemas before dispatch."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any


def normalize_builtin_arguments(name: str, arguments: Any) -> Any:
    """Only explicit, unambiguous aliases; do not coerce types or discard fields."""
    if name == "memory" and isinstance(arguments, dict) and arguments.get("action") == "create":
        return {**arguments, "action": "add"}
    return arguments


@lru_cache(maxsize=1)
def _schemas() -> dict[str, dict[str, Any]]:
    from app.application.code_agent.tool_schemas import build_tool_schemas
    from app.application.code_agent.loop_helpers import _ASK_USER_SCHEMA, _WORKFLOW_REQUEST_SCHEMA

    return {item["function"]["name"]: item["function"]["parameters"]
            for item in (*build_tool_schemas(), _ASK_USER_SCHEMA, _WORKFLOW_REQUEST_SCHEMA)}


def _errors(value: Any, schema: dict[str, Any], path: str = "arguments") -> list[str]:
    expected = schema.get("type")
    valid_type = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": type(value) is int,
        "number": type(value) in (int, float), "boolean": type(value) is bool,
        "null": value is None,
    }
    if expected and not valid_type.get(expected, False):
        return [f"{path}: ожидается {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: допустимо {json.dumps(schema['enum'], ensure_ascii=False)}"]
    errors: list[str] = []
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: обязательное поле")
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path}: имена полей должны быть строками")
            elif key in properties:
                errors.extend(_errors(item, properties[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: неизвестное поле")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: минимум {schema['minItems']} элементов")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: максимум {schema['maxItems']} элементов")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(_errors(item, schema["items"], f"{path}[{index}]"))
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: минимум {schema['minLength']} символов")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: максимум {schema['maxLength']} символов")
    elif type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            errors.append(f"{path}: ожидается конечное число")
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: минимум {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: максимум {schema['maximum']}")
    return errors[:5]


def _example_value(schema: dict[str, Any], key: str = "") -> Any:
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {field: _example_value(schema["properties"][field], field)
                for field in schema.get("required", [])}
    if kind == "array":
        return [_example_value(schema.get("items", {}), key)]
    if kind in {"integer", "number"}:
        return 1
    if kind == "boolean":
        return False
    return {"path": "file.txt", "project_path": "result.txt", "command": "echo example",
            "resource_id": "<id вложения из чата>", "group": "memory",
            "url": "https://example.org", "urls": "https://example.org",
            "queries": "English query", "audience": "global"}.get(key, "пример")


def validate_builtin_arguments(name: str, arguments: Any) -> dict[str, Any] | None:
    """Return an ordinary tool error; foreign providers retain their own schemas."""
    schema = _schemas().get(name)
    if schema is None:
        return None
    errors = _errors(arguments, schema)
    if not errors:
        return None
    example = _example_value(schema)
    if name == "math":
        example = {"request": {"operation": "evaluate", "expression": "2+2"}}
    elif name == "memory":
        example = {"action": "search", "query": "предпочтения пользователя"}
    elif name == "web_search":
        example = {"queries": ["English query"], "audience": "global", "mode": "quick"}
    elif name == "run_server":
        example = {"action": "list"}
    return {"ok": False, "error": "invalid_tool_arguments", "argument_errors": errors,
            "text": f"ERROR: {name}: {'; '.join(errors)}. "
                    f"Пример аргументов {name}: {json.dumps(example, ensure_ascii=False)}"}
