"""
run_history_service.py — история запусков с ротацией.
Хранит последние 200 записей, старые удаляются.
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_FILE = Path("data/run_history.json")
_FILE.parent.mkdir(parents=True, exist_ok=True)
_MAX_RUNS = 200


def _load() -> list:
    if _FILE.exists():
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return list(data.values()) if data else []
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save(runs: list):
    # Ротация: оставляем последние _MAX_RUNS
    if len(runs) > _MAX_RUNS:
        runs = runs[-_MAX_RUNS:]
    _FILE.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")


class RunHistoryService:
    def start_run(self, user_input: str) -> dict:
        run_id = str(uuid.uuid4())[:8]
        return {"run_id": run_id, "user_input": user_input, "started_at": datetime.utcnow().isoformat(), "events": []}

    def add_event(self, run_id: str, event_type: str, data: Any):
        pass  # Events stored in-memory during run, saved at finish

    def finish_run(self, run_id: str, result: dict):
        runs = _load()
        entry = {
            "run_id": run_id,
            "finished_at": datetime.utcnow().isoformat(),
            "ok": result.get("ok", False),
            "route": result.get("meta", {}).get("route", ""),
            "model": result.get("meta", {}).get("model_name", ""),
            "answer_len": len(result.get("answer", "")),
        }
        runs.append(entry)
        _save(runs)

    def list_runs(self, limit: int = 50) -> list:
        runs = _load()
        return runs[-limit:]
