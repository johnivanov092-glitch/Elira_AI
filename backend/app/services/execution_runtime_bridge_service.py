from __future__ import annotations

from typing import Any


class ExecutionRuntimeBridgeService:
    def __init__(
        self,
        execution_history_service,
        event_bus=None,
        autonomous_dev_engine=None,
        multi_agent_service=None,
        project_brain_engine=None,
    ) -> None:
        self.execution_history_service = execution_history_service
        self.event_bus = event_bus
        self.autonomous_dev_engine = autonomous_dev_engine
        self.multi_agent_service = multi_agent_service
        self.project_brain_engine = project_brain_engine

    async def start_execution(self, goal: str, mode: str = "autonomous_dev", metadata: dict | None = None) -> dict:
        execution = self.execution_history_service.start_execution(goal=goal, source=mode, metadata=metadata or {})
        execution_id = execution["id"]

        await self._emit("execution.started", {"execution_id": execution_id, "goal": goal, "mode": mode})
        self.execution_history_service.add_event(execution_id, "execution.started", {"goal": goal, "mode": mode})

        try:
            result = None

            if mode == "multi_agent" and self.multi_agent_service is not None:
                result = await self.multi_agent_service.run_pipeline(goal=goal, auto_apply=False, run_checks=True)
            elif mode == "project_brain" and self.project_brain_engine is not None:
                result = await self.project_brain_engine.create_refactor_plan(goal=goal)
            elif self.autonomous_dev_engine is not None:
                result = await self.autonomous_dev_engine.run_goal(
                    goal=goal,
                    auto_apply=False,
                    run_checks=False,
                    commit_changes=False,
                    requested_by="phase12",
                )
            else:
                result = {
                    "status": "ok",
                    "mode": "placeholder",
                    "goal": goal,
                    "message": "Connect autonomous_dev_engine / multi_agent_service / project_brain_engine here.",
                }

            self.execution_history_service.add_artifact(execution_id, "result", "json", result)
            self.execution_history_service.add_event(execution_id, "execution.completed", {"mode": mode})
            self.execution_history_service.finish_execution(
                execution_id,
                status="completed",
                summary=f"Execution finished in mode: {mode}",
            )
            await self._emit("execution.completed", {"execution_id": execution_id, "mode": mode})
            return self.execution_history_service.get_execution(execution_id)

        except Exception as exc:
            self.execution_history_service.add_event(execution_id, "execution.failed", {"error": str(exc)})
            self.execution_history_service.finish_execution(
                execution_id,
                status="failed",
                error=str(exc),
            )
            await self._emit("execution.failed", {"execution_id": execution_id, "error": str(exc)})
            return self.execution_history_service.get_execution(execution_id)

    async def _emit(self, event_name: str, payload: dict) -> None:
        if self.event_bus:
            try:
                await self.event_bus.publish(event_name, payload)
            except Exception:
                pass
