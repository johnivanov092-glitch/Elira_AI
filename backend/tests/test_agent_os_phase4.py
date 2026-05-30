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
from app.application.workflow_engine import runtime as workflow_engine_runtime  # noqa: E402
from app.api.routes.workflow_routes import router as workflow_router  # noqa: E402
from app.application.autopipeline import runtime as autopipeline_service  # noqa: E402
from app.application.event_bus import runtime as bus  # noqa: E402

# Workflow engine is imported above as workflow_engine_runtime — the legacy
# app.services.workflow_engine compat shim has been removed.
workflow_engine = workflow_engine_runtime


class WorkflowEngineFacadeTest(unittest.TestCase):
    def test_runtime_exposes_workflow_ids(self) -> None:
        self.assertTrue(workflow_engine_runtime.MULTI_AGENT_DEFAULT_WORKFLOW_ID)

    def test_legacy_constants_are_canonical(self) -> None:
        self.assertEqual(
            workflow_engine.MULTI_AGENT_DEFAULT_WORKFLOW_ID,
            workflow_engine_runtime.MULTI_AGENT_DEFAULT_WORKFLOW_ID,
        )
        self.assertEqual(
            workflow_engine.MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
            workflow_engine_runtime.MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
        )


class WorkflowDbMixin(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_workflow_db = workflow_engine.DB_PATH
        self._original_workflow_db_path = workflow_db_path.get_workflow_db_path()
        self._original_event_bus_db = bus.DB_PATH
        self._original_seeded = workflow_engine._BUILTIN_WORKFLOWS_SEEDED
        workflow_db = Path(self._tmpdir.name) / "workflow_engine.db"
        workflow_db_path.set_workflow_db_path(workflow_db)
        workflow_engine.DB_PATH = workflow_db
        bus.DB_PATH = Path(self._tmpdir.name) / "event_bus.db"
        workflow_engine._BUILTIN_WORKFLOWS_SEEDED = False
        workflow_engine._init_db()
        bus._init_db()

    def tearDown(self) -> None:
        workflow_engine.DB_PATH = self._original_workflow_db
        workflow_db_path.set_workflow_db_path(self._original_workflow_db_path)
        bus.DB_PATH = self._original_event_bus_db
        workflow_engine._BUILTIN_WORKFLOWS_SEEDED = self._original_seeded
        self._tmpdir.cleanup()
        super().tearDown()

    @staticmethod
    def _simple_agent_template(workflow_id: str = "test.workflow.simple", pause_after: bool = False) -> dict:
        return {
            "id": workflow_id,
            "name": "Simple workflow",
            "name_ru": "Простой workflow",
            "description": "Agent chain for tests",
            "graph": {
                "entry_step": "step-one",
                "steps": [
                    {
                        "id": "step-one",
                        "type": "agent",
                        "agent_id": "builtin-universal",
                        "input_map": {"query": "$.input.query"},
                        "save_as": "first",
                        "next": "step-two",
                        "pause_after": pause_after,
                        "config": {"profile_name": "Универсальный", "prompt_template": "First: {query}", "label": "First"},
                    },
                    {
                        "id": "step-two",
                        "type": "agent",
                        "agent_id": "builtin-analyst",
                        "input_map": {"query": "$.input.query", "first": "$.steps.first.answer"},
                        "save_as": "second",
                        "next": None,
                        "config": {"profile_name": "Аналитик", "prompt_template": "Second: {query}\n{first}", "label": "Second"},
                    },
                ],
            },
        }


class WorkflowEngineServiceTest(WorkflowDbMixin):
    def _agent_result(self, *args, **kwargs):
        prompt = kwargs.get("user_input", "")
        return {"ok": True, "answer": prompt, "timeline": [], "tool_results": [], "meta": {}}

    def test_create_list_and_get_workflow_template(self) -> None:
        created = workflow_engine.create_workflow_template(self._simple_agent_template())
        self.assertEqual(created["id"], "test.workflow.simple")

        workflows, total = workflow_engine.list_workflow_templates(include_disabled=True)
        self.assertEqual(total, 1)
        self.assertEqual(workflows[0]["id"], "test.workflow.simple")

        fetched = workflow_engine.get_workflow_template("test.workflow.simple")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["graph"]["entry_step"], "step-one")

    def test_start_workflow_run_completes_and_emits_events(self) -> None:
        workflow_engine.create_workflow_template(self._simple_agent_template())

        with patch("app.application.chat.runtime.run_agent", side_effect=self._agent_result):
            run = workflow_engine.start_workflow_run(
                workflow_id="test.workflow.simple",
                workflow_input={"query": "hello"},
                context={"model_name": "test-model"},
                trigger_source="test",
            )

        self.assertEqual(run["status"], "completed")
        self.assertIn("first", run["step_results"])
        self.assertIn("second", run["step_results"])

        events, total = bus.list_events(limit=20)
        self.assertGreaterEqual(total, 4)
        event_types = [event["event_type"] for event in events]
        self.assertIn("workflow.run.started", event_types)
        self.assertIn("workflow.step.started", event_types)
        self.assertIn("workflow.step.completed", event_types)
        self.assertIn("workflow.run.completed", event_types)

    def test_pause_resume_and_cancel_workflow_run(self) -> None:
        workflow_engine.create_workflow_template(self._simple_agent_template(workflow_id="test.workflow.pause", pause_after=True))

        with patch("app.application.chat.runtime.run_agent", side_effect=self._agent_result):
            paused = workflow_engine.start_workflow_run(
                workflow_id="test.workflow.pause",
                workflow_input={"query": "pause"},
                context={"model_name": "test-model"},
            )

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["current_step_id"], "step-two")

        with patch("app.application.chat.runtime.run_agent", side_effect=self._agent_result):
            resumed = workflow_engine.resume_workflow_run(paused["run_id"])
        self.assertEqual(resumed["status"], "completed")

        with patch("app.application.chat.runtime.run_agent", side_effect=self._agent_result):
            paused_again = workflow_engine.start_workflow_run(
                workflow_id="test.workflow.pause",
                workflow_input={"query": "cancel"},
                context={"model_name": "test-model"},
            )
        cancelled = workflow_engine.cancel_workflow_run(paused_again["run_id"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_tool_step_uses_adapter_and_emits_tool_event(self) -> None:
        workflow_engine.create_workflow_template(
            {
                "id": "test.workflow.tool",
                "name": "Tool workflow",
                "graph": {
                    "entry_step": "tool-step",
                    "steps": [
                        {
                            "id": "tool-step",
                            "type": "tool",
                            "tool_name": "search_memory",
                            "input_map": {"query": "$.input.query"},
                            "save_as": "tool_result",
                            "next": None,
                            "config": {"label": "Tool"},
                        }
                    ],
                },
            }
        )

        with patch("app.application.tool_registry.service.run_tool", return_value={"ok": True, "items": [{"text": "fact"}]}):
            run = workflow_engine.start_workflow_run(
                workflow_id="test.workflow.tool",
                workflow_input={"query": "memory"},
            )

        self.assertEqual(run["status"], "completed")
        self.assertTrue(run["step_results"]["tool_result"]["ok"])
        events, _ = bus.list_events(limit=20)
        self.assertIn("tool.executed", [event["event_type"] for event in events])


class WorkflowRoutesTest(WorkflowDbMixin):
    def setUp(self) -> None:
        super().setUp()
        app = FastAPI()
        app.include_router(workflow_router)
        self.client = TestClient(app)

    def test_workflow_template_and_run_routes(self) -> None:
        create_response = self.client.post("/api/agent-os/workflows", json=self._simple_agent_template("test.workflow.route"))
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get("/api/agent-os/workflows")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["total"], 1)

        with patch("app.application.chat.runtime.run_agent", return_value={"ok": True, "answer": "ok", "timeline": [], "tool_results": [], "meta": {}}):
            run_response = self.client.post(
                "/api/agent-os/workflow-runs",
                json={"workflow_id": "test.workflow.route", "input": {"query": "hi"}, "context": {"model_name": "test-model"}},
            )
        self.assertEqual(run_response.status_code, 200)
        self.assertEqual(run_response.json()["status"], "completed")


class WorkflowAutopipelineTest(WorkflowDbMixin):
    def test_autopipeline_workflow_task_type(self) -> None:
        with patch("app.application.workflows.runtime.start_workflow_run", return_value={"run_id": "wfr-123", "status": "completed", "error": {}}):
            result = autopipeline_service._execute_task(
                "workflow",
                {"workflow_id": "builtin.workflow.multi_agent.default", "input": {"query": "hello"}},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["run_id"], "wfr-123")


if __name__ == "__main__":
    unittest.main()
