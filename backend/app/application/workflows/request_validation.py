"""Validation for Workflow UI request resolutions."""
from __future__ import annotations

import json
from typing import Any, Callable


_MAX_RESOLUTION_BYTES = 64 * 1024


def _validate_object_values(schema: dict[str, Any], values: dict[str, Any]) -> None:
    if not schema:
        return
    if schema.get("type") not in (None, "object"):
        raise ValueError("workflow request schema must describe an object")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("workflow request schema properties must be an object")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(
        not isinstance(item, str) for item in required
    ):
        raise ValueError("workflow request schema required must be a string list")
    missing = [name for name in required if name not in values]
    if missing:
        raise ValueError(f"workflow request is missing required values: {missing}")
    if schema.get("additionalProperties") is False:
        unexpected = [name for name in values if name not in properties]
        if unexpected:
            raise ValueError(f"workflow request has unexpected values: {unexpected}")

    type_checks: dict[str, Callable[[Any], bool]] = {
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float))
        and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "array": lambda value: isinstance(value, list),
        "object": lambda value: isinstance(value, dict),
        "null": lambda value: value is None,
    }
    for name, value in values.items():
        rule = properties.get(name)
        if not isinstance(rule, dict) or "type" not in rule:
            continue
        expected = rule["type"]
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(
            isinstance(item, str)
            and (check := type_checks.get(item)) is not None
            and check(value)
            for item in expected_types
        ):
            raise ValueError(f"workflow request value '{name}' has invalid type")


def validate_resolution_values(
    request: dict[str, Any],
    values: dict[str, Any],
) -> None:
    try:
        encoded = json.dumps(values, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow request values must be JSON-serializable") from exc
    if len(encoded) > _MAX_RESOLUTION_BYTES:
        raise ValueError("workflow request values exceed 64 KiB")
    kind = str(request.get("kind", ""))
    if kind == "secret":
        secret_ref = str(values.get("secret_ref", "")).strip()
        if set(values) != {"secret_ref"} or not secret_ref or len(secret_ref) > 512:
            raise ValueError(
                "secret requests accept only a non-empty secret_ref; plaintext secrets "
                "must be stored by the write-only vault flow first"
            )
        return
    if kind == "elevation":
        allowed = {
            "elevated",
            "ok",
            "exit_code",
            "output",
            "native_bridge",
            "request_id",
        }
        if set(values) - allowed:
            raise ValueError("elevation result contains unsupported fields")
        if values.get("elevated") is not True or values.get("native_bridge") != "tauri-v1":
            raise ValueError("elevation must be completed by the native Tauri bridge")
        if str(values.get("request_id") or "") != str(request.get("request_id") or ""):
            raise ValueError("elevation result is bound to a different workflow request")
        exit_code = values.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise ValueError("elevation result requires an integer exit_code")
        if not isinstance(values.get("ok"), bool) or not isinstance(
            values.get("output", ""),
            str,
        ):
            raise ValueError("elevation result has invalid native fields")
        return
    _validate_object_values(request.get("schema", {}), values)
