from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional


class ExecutionRuntimeService:
    def __init__(self, event_bus=None, max_runs: int = 200) -> None:
        self.event_bus = event_bus
        self.max_runs = max_runs
        self.runs: List[dict] = []

    def list_runs(self, limit: int = 50) -> List[dict]:
        if limit <= 0:
            return []
        return self.runs[-limit:]

    def get_run(self, run_id: str) -> Optional[dict]:
        for run in reversed(self.runs):
            if run["id"] == run_id:
                return run
        return None

    async def execute_plan(self, plan: dict) -> dict:
        run = {
            "id": str(uuid.uuid4()),
            "goal": plan.get("goal", ""),
            "created_at": time.time(),
            "status": "running",
            "steps": [],
        }
        self.runs.append(run)
        self.runs = self.runs[-self.max_runs :]

        if self.event_bus:
            await self.event_bus.publish("execution.started", {"run_id": run["id"], "goal": run["goal"]})

        for index, step in enumerate(plan.get("steps", []), start=1):
            started = time.time()
            step_record = {
                "index": index,
                "name": step.get("name", f"step_{index}"),
                "type": step.get("type", "note"),
                "status": "running",
                "started_at": started,
            }
            run["steps"].append(step_record)

            try:
                result = await self._run_step(step)
                step_record["status"] = "completed"
                step_record["result"] = result
                step_record["finished_at"] = time.time()
            except Exception as exc:
                step_record["status"] = "failed"
                step_record["error"] = str(exc)
                step_record["finished_at"] = time.time()
                run["status"] = "failed"
                run["finished_at"] = time.time()
                if self.event_bus:
                    await self.event_bus.publish("execution.failed", {"run_id": run["id"], "step": step_record})
                return run

        run["status"] = "completed"
        run["finished_at"] = time.time()
        if self.event_bus:
            await self.event_bus.publish("execution.completed", {"run_id": run["id"]})
        return run

    async def _run_step(self, step: dict) -> dict:
        step_type = step.get("type", "note")

        if step_type == "sleep":
            await asyncio.sleep(float(step.get("seconds", 0)))
            return {"slept": float(step.get("seconds", 0))}

        if step_type == "tool":
            # Generic placeholder step for current Jarvis tool layer.
            return {
                "tool_name": step.get("tool_name"),
                "tool_args": step.get("tool_args", {}),
                "note": "Tool execution bridge placeholder. Connect this to tool_service.run_tool(...) if needed.",
            }

        if step_type == "project_brain":
            return {
                "action": step.get("action", "analyze"),
                "note": "ProjectBrain bridge placeholder. Connect to project_brain_service if needed.",
            }

        return {"message": step.get("message", "ok")}
