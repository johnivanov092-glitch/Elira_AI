from __future__ import annotations

from typing import Any

from app.infrastructure.llm.openai_compatible import list_models as list_openai_compatible_models


def get_models() -> dict[str, Any]:
    errors: list[str] = []
    try:
        models = list_openai_compatible_models()
    except Exception as exc:
        models = []
        errors.append(str(exc))

    payload: dict[str, Any] = {
        "ok": bool(models),
        "models": models,
        "count": len(models),
    }
    if errors:
        payload["error" if not models else "warnings"] = "; ".join(errors)
    return payload


async def list_local_models() -> dict[str, Any]:
    return get_models()


async def list_models() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in list_openai_compatible_models():
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            rows.append({"name": name, "type": str(item.get("provider") or "llama_server")})
    return rows
