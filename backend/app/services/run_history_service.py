from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class RunHistoryService:
    def __init__(self, storage_path: str | None = None):
        base_dir = Path(__file__).resolve().parents[3]
        self.storage_path = Path(storage_path) if storage_path else base_dir / "data" / "run_history.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.storage_path.exists():
            return []
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self) -> None:
        try:
            self.storage_path.write_text(
                json.dumps(self.runs[-200:], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def start_run(self, query: str) -> dict[str, Any]:
        run = {
            "run_id": str(uuid.uuid4()),
            "query": query,
            "status": "running",
            "timeline": [],
            "created_at": time.time(),
        }
        self.runs.append(run)
        self._save()
        return run

    def add_event(self, run_id: str, title: str, meta: dict[str, Any] | None = None) -> None:
        run = self.get_run(run_id)
        if run:
            run["timeline"].append({"title": title, "meta": meta or {}, "ts": time.time()})
            self._save()

    def finish_run(self, run_id: str, result: dict[str, Any]) -> None:
        run = self.get_run(run_id)
        if run:
            run["status"] = "finished"
            run["result"] = result
            run["finished_at"] = time.time()
            self._save()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        for run in self.runs:
            if run.get("run_id") == run_id:
                return run
        return None

    def list_runs(self) -> list[dict[str, Any]]:
        return list(reversed(self.runs[-100:]))
