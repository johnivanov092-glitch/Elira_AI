from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.workflows import db_path as workflow_db_path  # noqa: E402
from app.api.routes.agent_monitor_routes import router as agent_monitor_router  # noqa: E402
from app.application.monitoring import runtime as agent_monitor  # noqa: E402
from app.application.agent_registry import runtime as agent_registry  # noqa: E402
from app.application.agent_registry import sandbox as agent_sandbox  # noqa: E402
from app.application.chat import runtime as agents_service  # noqa: E402
from app.application.event_bus import runtime as bus  # noqa: E402
from app.application.workflow_engine import runtime as workflow_engine  # noqa: E402


class AgentOsPhase5DbMixin(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._original_monitor_db = agent_monitor.DB_PATH
        self._original_registry_db = agent_registry.DB_PATH
        self._original_event_bus_db = bus.DB_PATH
        self._original_workflow_db = workflow_engine.DB_PATH
        self._original_workflow_db_path = workflow_db_path.get_workflow_db_path()
        self._original_limit_seed = agent_monitor._LIMIT_SEED_DONE
        self._original_agent_seed = agent_registry._BUILTIN_AGENTS_SEEDED
        self._original_workflow_seed = workflow_engine._BUILTIN_WORKFLOWS_SEEDED

        agent_monitor.DB_PATH = tmp_root / "agent_monitor.db"
        agent_registry.DB_PATH = tmp_root / "agent_registry.db"
        bus.DB_PATH = tmp_root / "event_bus.db"
        workflow_db = tmp_root / "workflow_engine.db"
        workflow_db_path.set_workflow_db_path(workflow_db)
        workflow_engine.DB_PATH = workflow_db

        agent_monitor._LIMIT_SEED_DONE = False
        agent_registry._BUILTIN_AGENTS_SEEDED = False
        workflow_engine._BUILTIN_WORKFLOWS_SEEDED = False

        agent_monitor._init_db()
        agent_registry._init_db()
        bus._init_db()
        workflow_engine._init_db()

    def tearDown(self) -> None:
        agent_monitor.DB_PATH = self._original_monitor_db
        agent_registry.DB_PATH = self._original_registry_db
        bus.DB_PATH = self._original_event_bus_db
        workflow_engine.DB_PATH = self._original_workflow_db
        workflow_db_path.set_workflow_db_path(self._original_workflow_db_path)
        agent_monitor._LIMIT_SEED_DONE = self._original_limit_seed
        agent_registry._BUILTIN_AGENTS_SEEDED = self._original_agent_seed
        workflow_engine._BUILTIN_WORKFLOWS_SEEDED = self._original_workflow_seed
        self._tmpdir.cleanup()
        super().tearDown()

    @staticmethod
    def _base_plan(tools: list[str] | None = None) -> dict:
        return {
            "route": "chat",
            "tools": list(tools or []),
            "temporal": {
                "mode": "none",
                "requires_web": False,
                "freshness_sensitive": False,
                "years": [],
            },
            "web_plan": {
                "is_multi_intent": False,
                "subqueries": [],
                "passes": [],
                "pass_count": 1,
                "overflow_applied": False,
                "uncovered_subqueries": [],
            },
        }

    @staticmethod
    def _simple_tool_workflow(workflow_id: str = "test.workflow.tool.phase5") -> dict:
        return {
            "id": workflow_id,
            "name": "Tool workflow",
            "name_ru": "Tool workflow",
            "description": "Workflow with one tool step",
            "graph": {
                "entry_step": "tool-step",
                "steps": [
                    {
                        "id": "tool-step",
                        "type": "tool",
                        "tool_name": "search_web",
                        "input_map": {"query": "$.input.query"},
                        "save_as": "tool_result",
                        "next": None,
                        "config": {"label": "Tool"},
                    }
                ],
            },
            "enabled": True,
            "version": 1,
            "source": "custom",
        }


class AgentMonitorServiceTest(AgentOsPhase5DbMixin):
    def test_seed_update_and_list_limits(self) -> None:
        created = agent_monitor.seed_default_limits()
        self.assertGreaterEqual(created, 1)

        items = agent_monitor.list_agent_limits()
        ids = {item["agent_id"] for item in items}
        self.assertIn(agent_monitor.WORKFLOW_ENGINE_AGENT_ID, ids)
        self.assertTrue(any(agent_id.startswith("builtin-") for agent_id in ids))

        updated = agent_monitor.update_agent_limit(
            "builtin-universal",
            {
                "max_runs_per_hour": 10,
                "max_context_tokens": 2048,
                "allowed_tools": ["web_search", "memory_search"],
            },
        )
        self.assertEqual(updated["max_runs_per_hour"], 10)
        self.assertEqual(updated["max_context_tokens"], 2048)
        self.assertEqual(updated["allowed_tools"], ["web_search", "memory_search"])

        events, _ = bus.list_events(event_type="agent.limit.updated", limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["agent_id"], "builtin-universal")

    def test_seed_removes_unknown_builtin_limits(self) -> None:
        stale_id = "builtin-stale-from-old-seed"
        agent_monitor.monitoring_store.upsert_limit(
            agent_monitor.DB_PATH,
            agent_monitor.monitoring_store.default_limit_payload(stale_id),
        )

        agent_monitor.seed_default_limits()

        ids = {item["agent_id"] for item in agent_monitor.list_agent_limits()}
        self.assertNotIn(stale_id, ids)
        self.assertIn("builtin-universal", ids)

    def test_context_limit_block_records_metric_and_event(self) -> None:
        agent_monitor.update_agent_limit(
            "builtin-universal",
            {
                "max_context_tokens": 32,
                "allowed_tools": agent_monitor.ensure_agent_limit("builtin-universal")["allowed_tools"],
            },
        )

        with self.assertRaises(agent_sandbox.SandboxPolicyError) as ctx:
            agent_sandbox.preflight_or_raise(
                agent_id="builtin-universal",
                num_ctx=128,
                selected_tools=[],
                run_id="run-limit",
                route="chat",
                streaming=False,
            )

        self.assertEqual(ctx.exception.reason, "context_limit_exceeded")
        blocked = agent_monitor.get_recent_blocked_runs()
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["details"]["reason"], "context_limit_exceeded")
        events, _ = bus.list_events(event_type="sandbox.policy.blocked", limit=10)
        self.assertEqual(len(events), 1)

    def test_tool_allowlist_and_rate_limit_blocks(self) -> None:
        current = agent_monitor.ensure_agent_limit("builtin-programmer")
        agent_monitor.update_agent_limit(
            "builtin-programmer",
            {
                "max_runs_per_hour": 1,
                "allowed_tools": ["memory_search"],
                "max_context_tokens": current["max_context_tokens"],
            },
        )

        with self.assertRaises(agent_sandbox.SandboxPolicyError) as tool_error:
            agent_sandbox.preflight_or_raise(
                agent_id="builtin-programmer",
                num_ctx=128,
                selected_tools=["web_search"],
                run_id="run-tool-block",
                route="chat",
            )
        self.assertEqual(tool_error.exception.reason, "tool_not_allowed")

        agent_monitor.record_agent_run_metric(
            agent_id="builtin-programmer",
            run_id="existing-run",
            route="chat",
            model_name="test-model",
            ok=True,
            duration_ms=50,
            streaming=False,
            num_ctx=32,
            tools=["memory_search"],
        )
        with self.assertRaises(agent_sandbox.SandboxPolicyError) as rate_error:
            agent_sandbox.preflight_or_raise(
                agent_id="builtin-programmer",
                num_ctx=64,
                selected_tools=["memory_search"],
                run_id="run-rate-block",
                route="chat",
            )
        self.assertEqual(rate_error.exception.reason, "rate_limit_exceeded")


class AgentMonitorRuntimeTest(AgentOsPhase5DbMixin):
    def test_run_agent_records_metric(self) -> None:
        with patch.object(agents_service.PlannerV2Service, "plan", return_value=self._base_plan()), \
             patch.object(agents_service, "_collect_context", return_value=""), \
             patch.object(agents_service, "run_chat", return_value={"ok": True, "answer": "hello from agent"}), \
             patch.object(agents_service, "observe_dialogue", return_value={"ok": True}), \
             patch.object(agents_service, "_get_and_clear_attachments", return_value=""), \
             patch.object(agents_service, "_maybe_generate_files", return_value=""), \
             patch.object(agents_service, "_maybe_auto_exec_python", side_effect=lambda user_input, answer, timeline, enabled=True: answer), \
             patch.object(agents_service, "pick_model_for_route", return_value="test-model"):
            result = agents_service.run_agent(
                model_name="test-model",
                profile_name="Universal",
                user_input="Hello",
                session_id="phase5-session",
                use_memory=False,
                use_library=False,
                use_web_search=False,
            )

        self.assertTrue(result["ok"])
        dashboard = agent_monitor.get_agent_os_dashboard()
        self.assertEqual(dashboard["total_agent_runs"], 1)
        self.assertEqual(dashboard["blocked_runs"], 0)

    def test_run_agent_stream_records_metric(self) -> None:
        with patch.object(agents_service.PlannerV2Service, "plan", return_value=self._base_plan()), \
             patch.object(agents_service, "_collect_context", return_value=""), \
             patch.object(agents_service, "run_chat_stream", return_value=iter(["hello", " world"])), \
             patch.object(agents_service, "observe_dialogue", return_value={"ok": True}), \
             patch.object(agents_service, "_get_and_clear_attachments", return_value=""), \
             patch.object(agents_service, "_maybe_generate_files", return_value=""), \
             patch.object(agents_service, "_maybe_auto_exec_python", side_effect=lambda user_input, answer, timeline, enabled=True: answer), \
             patch.object(agents_service, "pick_model_for_route", return_value="test-model"), \
             patch.object(agents_service, "should_cache", return_value=False):
            events = list(
                agents_service.run_agent_stream(
                    model_name="test-model",
                    profile_name="Universal",
                    user_input="Hello stream",
                    session_id="phase5-stream",
                    use_memory=False,
                    use_library=False,
                    use_web_search=False,
                )
            )

        self.assertTrue(events[-1]["done"])
        dashboard = agent_monitor.get_agent_os_dashboard()
        self.assertEqual(dashboard["total_agent_runs"], 1)

    def test_workflow_tool_step_blocked_for_workflow_engine(self) -> None:
        workflow_engine.create_workflow_template(self._simple_tool_workflow())
        current = agent_monitor.ensure_agent_limit(agent_monitor.WORKFLOW_ENGINE_AGENT_ID)
        agent_monitor.update_agent_limit(
            agent_monitor.WORKFLOW_ENGINE_AGENT_ID,
            {
                "max_runs_per_hour": current["max_runs_per_hour"],
                "max_execution_seconds": current["max_execution_seconds"],
                "max_context_tokens": current["max_context_tokens"],
                "allowed_tools": ["memory_search"],
            },
        )

        run = workflow_engine.start_workflow_run(
            workflow_id="test.workflow.tool.phase5",
            workflow_input={"query": "latest"},
            context={"num_ctx": 512},
            trigger_source="test",
        )

        self.assertEqual(run["status"], "failed")
        step = run["step_results"]["tool_result"]
        self.assertFalse(step["ok"])
        self.assertEqual(step["sandbox_reason"], "tool_not_allowed")

        dashboard = agent_monitor.get_agent_os_dashboard()
        self.assertEqual(dashboard["blocked_runs"], 1)
        self.assertEqual(dashboard["workflow_runs"], 1)

        bus_events, _ = bus.list_events(event_type="sandbox.policy.blocked", limit=10)
        self.assertEqual(len(bus_events), 1)


class AgentMonitorRoutesTest(AgentOsPhase5DbMixin):
    def setUp(self) -> None:
        super().setUp()
        app = FastAPI()
        app.include_router(agent_monitor_router)
        self.client = TestClient(app)

    def test_health_dashboard_and_limits_routes(self) -> None:
        health_response = self.client.get("/api/agent-os/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertIn("components", health_response.json())

        limits_response = self.client.get("/api/agent-os/limits")
        self.assertEqual(limits_response.status_code, 200)
        self.assertGreaterEqual(limits_response.json()["total"], 1)

        get_limit_response = self.client.get("/api/agent-os/limits/builtin-universal")
        self.assertEqual(get_limit_response.status_code, 200)
        self.assertEqual(get_limit_response.json()["agent_id"], "builtin-universal")

        update_limit_response = self.client.put(
            "/api/agent-os/limits/builtin-universal",
            json={"max_runs_per_hour": 25},
        )
        self.assertEqual(update_limit_response.status_code, 200)
        self.assertEqual(update_limit_response.json()["max_runs_per_hour"], 25)

        dashboard_response = self.client.get("/api/agent-os/dashboard")
        self.assertEqual(dashboard_response.status_code, 200)
        payload = dashboard_response.json()
        self.assertIn("total_agent_runs", payload)
        self.assertIn("limits_summary", payload)


if __name__ == "__main__":
    unittest.main()
