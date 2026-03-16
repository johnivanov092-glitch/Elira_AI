from __future__ import annotations

import time
import uuid
from typing import Dict, List, Optional


class AgentSupervisorService:
    def __init__(self, execution_runtime=None, event_bus=None) -> None:
        self.execution_runtime = execution_runtime
        self.event_bus = event_bus
        self._agents: Dict[str, dict] = {}

    def register_agent(
        self,
        name: str,
        role: str,
        allowed_tools: Optional[List[str]] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        agent_id = str(uuid.uuid4())
        agent = {
            "id": agent_id,
            "name": name,
            "role": role,
            "allowed_tools": allowed_tools or [],
            "metadata": metadata or {},
            "status": "idle",
            "created_at": time.time(),
        }
        self._agents[agent_id] = agent
        return agent

    def list_agents(self) -> List[dict]:
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[dict]:
        return self._agents.get(agent_id)

    async def run_goal(self, goal: str, requested_by: str = "user") -> dict:
        plan = self._build_default_plan(goal)
        if self.event_bus:
            await self.event_bus.publish("supervisor.goal_received", {"goal": goal, "requested_by": requested_by})
        if not self.execution_runtime:
            return {
                "status": "failed",
                "goal": goal,
                "error": "execution_runtime is not configured",
            }
        return await self.execution_runtime.execute_plan(plan)

    def _build_default_plan(self, goal: str) -> dict:
        return {
            "goal": goal,
            "steps": [
                {
                    "name": "planner",
                    "type": "note",
                    "message": f"Plan goal: {goal}",
                },
                {
                    "name": "research",
                    "type": "tool",
                    "tool_name": "research_web",
                    "tool_args": {"query": goal},
                },
                {
                    "name": "review",
                    "type": "note",
                    "message": "Review gathered context and prepare answer or action.",
                },
            ],
        }
