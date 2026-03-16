from __future__ import annotations

import time
from typing import Any, Dict, Optional


class SupervisorRuntimeBridgeService:
    def __init__(self, supervisor, execution_runtime, event_bus, run_trace_service) -> None:
        self.supervisor = supervisor
        self.execution_runtime = execution_runtime
        self.event_bus = event_bus
        self.run_trace_service = run_trace_service

    async def run_goal_with_trace(self, goal: str, requested_by: str = "user") -> dict:
        trace = self.run_trace_service.create_run(goal=goal, source=requested_by)
        run_id = trace["id"]

        await self.event_bus.publish("trace.created", {"run_id": run_id, "goal": goal})
        self.run_trace_service.add_event(run_id, "trace.created", {"goal": goal, "requested_by": requested_by})
        self.run_trace_service.update_status(run_id, "running")

        plan = self.supervisor._build_default_plan(goal)
        self.run_trace_service.add_artifact(run_id, "plan", "json", plan)

        for index, step in enumerate(plan.get("steps", []), start=1):
            step_record = {
                "index": index,
                "name": step.get("name", f"step_{index}"),
                "type": step.get("type", "note"),
                "status": "running",
                "started_at": time.time(),
            }
            self.run_trace_service.add_step(run_id, step_record)
            self.run_trace_service.add_event(run_id, "step.started", {"index": index, "name": step_record["name"]})

            try:
                result = await self.execution_runtime._run_step(step)
                step_record["status"] = "completed"
                step_record["result"] = result
                step_record["finished_at"] = time.time()
                self._replace_step(run_id, index, step_record)
                self.run_trace_service.add_event(run_id, "step.completed", {"index": index, "result": result})
            except Exception as exc:
                step_record["status"] = "failed"
                step_record["error"] = str(exc)
                step_record["finished_at"] = time.time()
                self._replace_step(run_id, index, step_record)
                self.run_trace_service.add_event(run_id, "step.failed", {"index": index, "error": str(exc)})
                self.run_trace_service.update_status(run_id, "failed", error=str(exc))
                await self.event_bus.publish("trace.failed", {"run_id": run_id, "error": str(exc)})
                return self.run_trace_service.get_run(run_id)

        summary = f"Run completed for goal: {goal}"
        self.run_trace_service.update_status(run_id, "completed", summary=summary)
        self.run_trace_service.add_event(run_id, "trace.completed", {"summary": summary})
        await self.event_bus.publish("trace.completed", {"run_id": run_id})
        return self.run_trace_service.get_run(run_id)

    def _replace_step(self, run_id: str, index: int, new_step: dict) -> None:
        run = self.run_trace_service.get_run(run_id)
        if not run:
            return
        steps = run.get("steps", [])
        if 0 < index <= len(steps):
            steps[index - 1] = new_step
            run["steps"] = steps
            run["updated_at"] = time.time()
            self.run_trace_service._save(run)
