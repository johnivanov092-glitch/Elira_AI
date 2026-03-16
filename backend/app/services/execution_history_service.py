from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class ExecutionHistoryService:
    def __init__(self, storage_dir: str = "data/execution_history") -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def start_execution(self, goal: str, source: str = "phase12", metadata: Optional[dict] = None) -> dict:
        execution = {
            "id": str(uuid.uuid4()),
            "goal": goal,
            "source": source,
            "status": "running",
            "created_at": time.time(),
            "updated_at": time.time(),
            "metadata": metadata or {},
            "events": [],
            "artifacts": [],
            "summary": None,
            "error": None,
        }
        self._save(execution)
        return execution

    def list_executions(self, limit: int = 50) -> List[dict]:
        files = sorted(self.storage_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for path in files[:max(limit, 0)]:
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return result

    def get_execution(self, execution_id: str) -> Optional[dict]:
        path = self.storage_dir / f"{execution_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def add_event(self, execution_id: str, name: str, payload: Optional[dict] = None) -> Optional[dict]:
        execution = self.get_execution(execution_id)
        if not execution:
            return None
        execution["events"].append({
            "name": name,
            "timestamp": time.time(),
            "payload": payload or {},
        })
        execution["updated_at"] = time.time()
        self._save(execution)
        return execution

    def add_artifact(self, execution_id: str, title: str, kind: str, value: Any) -> Optional[dict]:
        execution = self.get_execution(execution_id)
        if not execution:
            return None
        execution["artifacts"].append({
            "title": title,
            "kind": kind,
            "value": value,
            "timestamp": time.time(),
        })
        execution["updated_at"] = time.time()
        self._save(execution)
        return execution

    def finish_execution(self, execution_id: str, status: str = "completed", summary: Optional[str] = None, error: Optional[str] = None) -> Optional[dict]:
        execution = self.get_execution(execution_id)
        if not execution:
            return None
        execution["status"] = status
        execution["summary"] = summary
        execution["error"] = error
        execution["updated_at"] = time.time()
        execution["finished_at"] = time.time()
        self._save(execution)
        return execution

    def _save(self, execution: dict) -> None:
        path = self.storage_dir / f"{execution['id']}.json"
        path.write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")
