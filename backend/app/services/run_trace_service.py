from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class RunTraceService:
    def __init__(self, storage_dir: str = "data/run_history") -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def create_run(self, goal: str, source: str = "supervisor") -> dict:
        run = {
            "id": str(uuid.uuid4()),
            "goal": goal,
            "source": source,
            "status": "created",
            "created_at": time.time(),
            "updated_at": time.time(),
            "steps": [],
            "events": [],
            "artifacts": [],
            "summary": None,
            "error": None,
        }
        self._save(run)
        return run

    def list_runs(self, limit: int = 50) -> List[dict]:
        files = sorted(self.storage_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result: List[dict] = []
        for path in files[: max(limit, 0)]:
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return result

    def get_run(self, run_id: str) -> Optional[dict]:
        path = self.storage_dir / f"{run_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def add_step(self, run_id: str, step: dict) -> Optional[dict]:
        run = self.get_run(run_id)
        if not run:
            return None
        run["steps"].append(step)
        run["updated_at"] = time.time()
        self._save(run)
        return run

    def add_event(self, run_id: str, event_name: str, payload: Optional[dict] = None) -> Optional[dict]:
        run = self.get_run(run_id)
        if not run:
            return None
        run["events"].append({
            "name": event_name,
            "timestamp": time.time(),
            "payload": payload or {},
        })
        run["updated_at"] = time.time()
        self._save(run)
        return run

    def add_artifact(self, run_id: str, title: str, kind: str, value: Any) -> Optional[dict]:
        run = self.get_run(run_id)
        if not run:
            return None
        run["artifacts"].append({
            "title": title,
            "kind": kind,
            "value": value,
            "timestamp": time.time(),
        })
        run["updated_at"] = time.time()
        self._save(run)
        return run

    def update_status(self, run_id: str, status: str, summary: Optional[str] = None, error: Optional[str] = None) -> Optional[dict]:
        run = self.get_run(run_id)
        if not run:
            return None
        run["status"] = status
        run["summary"] = summary
        run["error"] = error
        run["updated_at"] = time.time()
        if status in {"completed", "failed", "cancelled"}:
            run["finished_at"] = time.time()
        self._save(run)
        return run

    def delete_run(self, run_id: str) -> bool:
        path = self.storage_dir / f"{run_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def _save(self, run: dict) -> None:
        path = self.storage_dir / f"{run['id']}.json"
        path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
