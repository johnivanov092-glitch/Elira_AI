from __future__ import annotations
from typing import Any
import json
from pathlib import Path

from app.core.config import DEFAULT_MODEL, DEFAULT_PROFILE

SETTINGS_FILE = Path("settings.json")


def _safe_read_settings() -> dict[str, Any]:
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_settings(data: dict[str, Any]) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_settings_payload() -> dict[str, Any]:
    current = _safe_read_settings()
    return {
        "ok": True,
        "defaults": {
            "model_name": current.get("model_name", DEFAULT_MODEL),
            "profile_name": current.get("profile_name", DEFAULT_PROFILE),
        },
        "raw": current,
    }


def update_settings_payload(model_name: str, profile_name: str) -> dict[str, Any]:
    current = _safe_read_settings()
    current["model_name"] = model_name
    current["profile_name"] = profile_name
    _write_settings(current)
    return {
        "ok": True,
        "defaults": {
            "model_name": model_name,
            "profile_name": profile_name,
        },
        "raw": current,
    }
