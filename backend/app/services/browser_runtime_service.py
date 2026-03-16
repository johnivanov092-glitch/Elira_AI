from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional


class BrowserRuntimeService:
    def __init__(self, browser_agent=None, run_trace_service=None, event_bus=None) -> None:
        self.browser_agent = browser_agent
        self.run_trace_service = run_trace_service
        self.event_bus = event_bus

    async def execute(self, url: str, actions: Optional[List[dict]] = None) -> dict:
        actions = actions or []
        run = {
            "id": str(uuid.uuid4()),
            "url": url,
            "status": "running",
            "created_at": time.time(),
            "actions": [],
            "result": None,
            "error": None,
        }

        try:
            if self.browser_agent and hasattr(self.browser_agent, "run"):
                result = self.browser_agent.run(url=url, actions=actions)
                result = await self._maybe_await(result)
            else:
                result = {
                    "url": url,
                    "actions_count": len(actions),
                    "note": "Connect real browser_agent / Playwright runtime here.",
                }

            run["actions"] = actions
            run["result"] = result
            run["status"] = "completed"
            run["finished_at"] = time.time()

            await self._persist(run)
            return run

        except Exception as exc:
            run["status"] = "failed"
            run["error"] = str(exc)
            run["finished_at"] = time.time()
            await self._persist(run)
            return run

    async def _persist(self, run: dict) -> None:
        if self.run_trace_service and hasattr(self.run_trace_service, "create_run"):
            try:
                trace = self.run_trace_service.create_run(run["url"], source="browser_runtime")
                self.run_trace_service.add_artifact(trace["id"], "browser_runtime", "json", run)
                self.run_trace_service.update_status(
                    trace["id"],
                    run["status"],
                    summary=f"Browser runtime executed for: {run['url']}",
                    error=run.get("error"),
                )
            except Exception:
                pass

        if self.event_bus:
            try:
                await self.event_bus.publish("browser_runtime.completed", {"url": run["url"], "status": run["status"]})
            except Exception:
                pass

    async def _maybe_await(self, value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value
