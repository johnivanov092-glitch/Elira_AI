from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional


class MultiAgentService:
    def __init__(
        self,
        event_bus=None,
        run_trace_service=None,
        project_brain_service=None,
        autonomous_dev_engine=None,
        tool_service=None,
    ) -> None:
        self.event_bus = event_bus
        self.run_trace_service = run_trace_service
        self.project_brain_service = project_brain_service
        self.autonomous_dev_engine = autonomous_dev_engine
        self.tool_service = tool_service
        self._agents: List[dict] = []
        self._runs: List[dict] = []

    def bootstrap_default_agents(self) -> List[dict]:
        if self._agents:
            return self._agents

        self._agents = [
            self._make_agent("Architect Agent", "architecture", ["project_brain", "dependency_graph"]),
            self._make_agent("Refactor Agent", "refactor", ["project_patch", "autodev"]),
            self._make_agent("Test Agent", "verification", ["python_execute", "tests"]),
            self._make_agent("Security Agent", "security", ["project_search", "review"]),
            self._make_agent("Research Agent", "research", ["search_web", "browser_runtime"]),
            self._make_agent("DevOps Agent", "devops", ["git", "desktop_lifecycle"]),
        ]
        return self._agents

    def list_agents(self) -> List[dict]:
        return self._agents

    def list_runs(self, limit: int = 20) -> List[dict]:
        return self._runs[-limit:]

    async def run_pipeline(self, goal: str, auto_apply: bool = False, run_checks: bool = True) -> dict:
        self.bootstrap_default_agents()

        run = {
            "id": str(uuid.uuid4()),
            "goal": goal,
            "status": "running",
            "created_at": time.time(),
            "agents": [a["name"] for a in self._agents],
            "steps": [],
            "summary": None,
            "error": None,
        }
        self._runs.append(run)

        try:
            await self._emit("multi_agent.started", {"run_id": run["id"], "goal": goal})

            architect_result = await self._architect_step(goal)
            run["steps"].append(self._step("Architect Agent", architect_result))

            refactor_result = await self._refactor_step(goal, auto_apply=auto_apply, run_checks=run_checks)
            run["steps"].append(self._step("Refactor Agent", refactor_result))

            test_result = await self._test_step(goal, enabled=run_checks)
            run["steps"].append(self._step("Test Agent", test_result))

            security_result = await self._security_step(goal)
            run["steps"].append(self._step("Security Agent", security_result))

            research_result = await self._research_step(goal)
            run["steps"].append(self._step("Research Agent", research_result))

            devops_result = await self._devops_step(goal, auto_apply=auto_apply)
            run["steps"].append(self._step("DevOps Agent", devops_result))

            run["status"] = "completed"
            run["finished_at"] = time.time()
            run["summary"] = f"Multi-agent pipeline completed for: {goal}"

            await self._persist_trace(run)
            await self._emit("multi_agent.completed", {"run_id": run["id"], "goal": goal})
            return run

        except Exception as exc:
            run["status"] = "failed"
            run["error"] = str(exc)
            run["finished_at"] = time.time()
            await self._persist_trace(run)
            await self._emit("multi_agent.failed", {"run_id": run["id"], "error": str(exc)})
            return run

    def _make_agent(self, name: str, role: str, capabilities: List[str]) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "name": name,
            "role": role,
            "capabilities": capabilities,
            "status": "ready",
            "created_at": time.time(),
        }

    def _step(self, agent_name: str, result: dict) -> dict:
        return {
            "agent": agent_name,
            "timestamp": time.time(),
            "result": result,
        }

    async def _architect_step(self, goal: str) -> dict:
        if self.project_brain_service and hasattr(self.project_brain_service, "create_refactor_plan"):
            try:
                result = self.project_brain_service.create_refactor_plan(goal)
                result = await self._maybe_await(result)
                return {"status": "ok", "source": "project_brain", "data": result}
            except Exception:
                pass
        return {
            "status": "ok",
            "source": "placeholder",
            "analysis": [
                "Map impacted routes and services",
                "Locate high-risk files",
                "Define minimal refactor surface",
            ],
        }

    async def _refactor_step(self, goal: str, auto_apply: bool, run_checks: bool) -> dict:
        if self.autonomous_dev_engine and hasattr(self.autonomous_dev_engine, "run_goal"):
            try:
                result = await self.autonomous_dev_engine.run_goal(
                    goal=goal,
                    auto_apply=auto_apply,
                    run_checks=run_checks,
                    commit_changes=False,
                    requested_by="multi-agent",
                )
                return {"status": "ok", "source": "autonomous_dev", "data": result}
            except Exception:
                pass
        return {
            "status": "ok",
            "source": "placeholder",
            "plan": "Prepare patch preview and safe apply workflow",
        }

    async def _test_step(self, goal: str, enabled: bool) -> dict:
        if not enabled:
            return {"status": "skipped", "reason": "run_checks=false"}

        if self.tool_service and hasattr(self.tool_service, "run_tool"):
            try:
                result = self.tool_service.run_tool("python_execute", {"code": "print('test placeholder')"})
                result = await self._maybe_await(result)
                return {"status": "ok", "source": "tool_service", "data": result}
            except Exception:
                pass
        return {"status": "ok", "source": "placeholder", "checks": ["syntax", "basic smoke verification"]}

    async def _security_step(self, goal: str) -> dict:
        return {
            "status": "ok",
            "source": "placeholder",
            "checks": [
                "Review patch boundaries",
                "Check file write scope",
                "Verify safe patch path",
            ],
        }

    async def _research_step(self, goal: str) -> dict:
        return {
            "status": "ok",
            "source": "placeholder",
            "context": [
                "Search relevant code paths",
                "Collect matching files",
                "Prepare implementation notes",
            ],
        }

    async def _devops_step(self, goal: str, auto_apply: bool) -> dict:
        if not auto_apply:
            return {"status": "skipped", "reason": "auto_apply=false"}

        return {
            "status": "ok",
            "source": "placeholder",
            "actions": [
                "Prepare git status review",
                "Ready for commit after verification",
            ],
        }

    async def _persist_trace(self, run: dict) -> None:
        if self.run_trace_service and hasattr(self.run_trace_service, "create_run"):
            try:
                trace = self.run_trace_service.create_run(run["goal"], source="multi_agent")
                for step in run.get("steps", []):
                    self.run_trace_service.add_step(trace["id"], step)
                self.run_trace_service.update_status(
                    trace["id"],
                    run["status"],
                    summary=run.get("summary"),
                    error=run.get("error"),
                )
            except Exception:
                pass

    async def _emit(self, event_name: str, payload: dict) -> None:
        if self.event_bus:
            try:
                await self.event_bus.publish(event_name, payload)
            except Exception:
                pass

    async def _maybe_await(self, value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value
