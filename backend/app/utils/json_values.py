"""JSON value helpers shared by stores with an empty-object null default."""
from __future__ import annotations

import json
from typing import Any


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
