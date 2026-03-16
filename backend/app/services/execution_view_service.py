from __future__ import annotations

from typing import Optional


class ExecutionViewService:
    def __init__(self, execution_history_service) -> None:
        self.execution_history_service = execution_history_service

    def get_execution_view(self, execution_id: str) -> Optional[dict]:
        execution = self.execution_history_service.get_execution(execution_id)
        if not execution:
            return None

        return {
            "id": execution["id"],
            "goal": execution["goal"],
            "status": execution["status"],
            "source": execution.get("source"),
            "created_at": execution.get("created_at"),
            "updated_at": execution.get("updated_at"),
            "finished_at": execution.get("finished_at"),
            "summary": execution.get("summary"),
            "error": execution.get("error"),
            "events_count": len(execution.get("events", [])),
            "artifacts_count": len(execution.get("artifacts", [])),
            "events": execution.get("events", []),
            "artifacts": execution.get("artifacts", []),
            "metadata": execution.get("metadata", {}),
        }
