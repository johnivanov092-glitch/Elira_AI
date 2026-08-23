from __future__ import annotations

import json
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

from app.api.routes.event_bus_routes import router as event_bus_router  # noqa: E402
from app.api.routes.workflow_routes import router as workflow_router  # noqa: E402
from app.application.event_bus import runtime as event_bus  # noqa: E402
from app.application.workflows import db_path as workflow_db_path  # noqa: E402
from app.application.workflows.request_lifecycle import (  # noqa: E402
    finish_code_agent_workflow_run,
    recover_incomplete_requests,
    start_code_agent_workflow_run,
)
from app.application.workflows.store import (  # noqa: E402
    claim_workflow_request,
    update_workflow_run,
)


class WorkflowRequestApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_workflow_db = workflow_db_path.get_workflow_db_path()
        self._original_event_bus_db = event_bus.DB_PATH
        workflow_db_path.set_workflow_db_path(
            Path(self._tmpdir.name) / "workflow_engine.db"
        )
        event_bus.DB_PATH = Path(self._tmpdir.name) / "event_bus.db"
        event_bus._init_db()

        app = FastAPI()
        app.include_router(workflow_router)
        app.include_router(event_bus_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        workflow_db_path.set_workflow_db_path(self._original_workflow_db)
        event_bus.DB_PATH = self._original_event_bus_db
        self._tmpdir.cleanup()

    def _create_request_workflow(
        self,
        *,
        workflow_id: str,
        kind: str,
        save_as: str = "request_result",
    ) -> None:
        response = self.client.post(
            "/api/agent-os/workflows",
            json={
                "id": workflow_id,
                "name": f"{kind} request workflow",
                "graph": {
                    "entry_step": "collect",
                    "steps": [
                        {
                            "id": "collect",
                            "type": "request",
                            "save_as": save_as,
                            "next": None,
                            "config": {
                                "kind": kind,
                                "message": f"Resolve {kind}",
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project_root": {"type": "string"}
                                    },
                                },
                            },
                        }
                    ],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _create_tool_workflow(self, workflow_id: str) -> None:
        response = self.client.post(
            "/api/agent-os/workflows",
            json={
                "id": workflow_id,
                "name": workflow_id,
                "graph": {
                    "entry_step": "tool",
                    "steps": [
                        {
                            "id": "tool",
                            "type": "tool",
                            "tool_name": "search_memory",
                            "next": None,
                        }
                    ],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _start(self, workflow_id: str, permission_mode: str = "ask") -> dict:
        response = self.client.post(
            "/api/agent-os/workflow-runs",
            json={
                "workflow_id": workflow_id,
                "permission_mode": permission_mode,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_pending_input_request_is_replayed_and_resolution_resumes_once(self) -> None:
        self._create_request_workflow(
            workflow_id="test.workflow.input-request",
            kind="input",
            save_as="folder",
        )

        waiting_run = self._start("test.workflow.input-request")

        self.assertEqual(waiting_run["status"], "needs_input")
        self.assertEqual(waiting_run["current_step_id"], "collect")

        pending_response = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting_run['run_id']}/requests",
            params={"status": "pending"},
        )
        self.assertEqual(pending_response.status_code, 200, pending_response.text)
        pending = pending_response.json()
        self.assertEqual(pending["total"], 1)
        request = pending["requests"][0]
        self.assertEqual(request["type"], "item/request")
        self.assertEqual(request["kind"], "input")
        self.assertEqual(request["step_id"], "collect")
        self.assertFalse(request["sensitive"])

        global_pending = self.client.get(
            "/api/agent-os/workflow-requests",
            params={"status": "pending"},
        )
        self.assertEqual(global_pending.status_code, 200, global_pending.text)
        self.assertEqual(global_pending.json()["requests"][0]["request_id"], request["request_id"])

        resolve_body = {
            "action": "accept",
            "values": {"project_root": "D:/Work"},
        }
        resolved_response = self.client.post(
            f"/api/agent-os/workflow-requests/{request['request_id']}/resolve",
            json=resolve_body,
        )
        self.assertEqual(resolved_response.status_code, 200, resolved_response.text)
        resolved = resolved_response.json()
        self.assertEqual(resolved["request"]["status"], "resolved")
        self.assertNotIn("values", resolved["request"])
        self.assertEqual(resolved["run"]["status"], "completed")
        self.assertEqual(
            resolved["run"]["step_results"]["folder"]["values"],
            {"project_root": "D:/Work"},
        )

        duplicate_response = self.client.post(
            f"/api/agent-os/workflow-requests/{request['request_id']}/resolve",
            json=resolve_body,
        )
        self.assertEqual(duplicate_response.status_code, 200, duplicate_response.text)
        self.assertEqual(duplicate_response.json()["run"]["status"], "completed")

        completed_events = self.client.get(
            "/api/agent-os/events",
            params={"event_type": "item/completed", "limit": 20},
        ).json()
        matching = [
            event
            for event in completed_events["events"]
            if event["payload"].get("run_id") == waiting_run["run_id"]
            and event["payload"].get("step_id") == "collect"
        ]
        self.assertEqual(len(matching), 1)

        all_events = self.client.get(
            "/api/agent-os/events",
            params={"limit": 100},
        ).json()["events"]
        ordered_types = [
            event["event_type"]
            for event in reversed(all_events)
            if event["payload"].get("run_id") == waiting_run["run_id"]
        ]
        request_index = ordered_types.index("item/request")
        resolved_index = ordered_types.index("serverRequest/resolved")
        completed_index = ordered_types.index("item/completed")
        self.assertLess(request_index, resolved_index)
        self.assertLess(resolved_index, completed_index)

    def test_startup_recovery_replays_an_interrupted_request_claim(self) -> None:
        self._create_request_workflow(
            workflow_id="test.workflow.recover-request",
            kind="input",
        )
        waiting_run = self._start("test.workflow.recover-request")
        request = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting_run['run_id']}/requests"
        ).json()["requests"][0]
        db_path = workflow_db_path.get_workflow_db_path()
        _, claimed = claim_workflow_request(
            db_path=db_path,
            request_id=request["request_id"],
        )
        self.assertTrue(claimed)
        update_workflow_run(
            db_path=db_path,
            run_id=waiting_run["run_id"],
            status="running",
        )

        self.assertEqual(recover_incomplete_requests(db_path=db_path), 1)

        replayed = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting_run['run_id']}/requests",
            params={"status": "pending"},
        ).json()
        self.assertEqual(replayed["total"], 1)
        current = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting_run['run_id']}"
        ).json()
        self.assertEqual(current["status"], "needs_input")

    def test_startup_recovery_requires_reconciliation_for_uncertain_tool(self) -> None:
        self._create_tool_workflow("test.workflow.reconcile-tool")
        with patch(
            "app.application.tool_registry.service.run_tool",
            return_value={
                "ok": False,
                "status": "needs_input",
                "request": {"kind": "input", "message": "Choose value"},
            },
        ):
            waiting = self._start("test.workflow.reconcile-tool")
        request = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting['run_id']}/requests"
        ).json()["requests"][0]
        db_path = workflow_db_path.get_workflow_db_path()
        _, claimed = claim_workflow_request(
            db_path=db_path,
            request_id=request["request_id"],
        )
        self.assertTrue(claimed)
        update_workflow_run(
            db_path=db_path,
            run_id=waiting["run_id"],
            status="running",
        )

        self.assertEqual(recover_incomplete_requests(db_path=db_path), 1)

        actionable = self.client.get(
            "/api/agent-os/workflow-requests",
            params={"status": "actionable"},
        ).json()["requests"]
        recovered = next(
            item for item in actionable if item["request_id"] == request["request_id"]
        )
        self.assertEqual(recovered["status"], "needs_reconciliation")
        self.assertIn("могло уже выполниться", recovered["message"])
        current = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting['run_id']}"
        ).json()
        self.assertEqual(current["status"], "needs_reconciliation")

    def test_secret_request_rejects_plaintext_and_never_returns_resolution(self) -> None:
        self._create_request_workflow(
            workflow_id="test.workflow.secret-request",
            kind="secret",
            save_as="credential",
        )
        waiting_run = self._start("test.workflow.secret-request")
        self.assertEqual(waiting_run["status"], "needs_secret")

        pending = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting_run['run_id']}/requests"
        ).json()["requests"][0]
        self.assertTrue(pending["sensitive"])

        plaintext = "do-not-persist-this-secret"
        rejected = self.client.post(
            f"/api/agent-os/workflow-requests/{pending['request_id']}/resolve",
            json={"action": "accept", "values": {"token": plaintext}},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)

        accepted = self.client.post(
            f"/api/agent-os/workflow-requests/{pending['request_id']}/resolve",
            json={
                "action": "accept",
                "values": {"secret_ref": "secret:test-token"},
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        payload = accepted.json()
        self.assertEqual(payload["run"]["status"], "completed")
        self.assertNotIn("values", payload["request"])

        events = self.client.get(
            "/api/agent-os/events",
            params={"limit": 100},
        ).json()
        self.assertNotIn(plaintext, json.dumps(events, ensure_ascii=False))
        self.assertNotIn(plaintext, json.dumps(payload, ensure_ascii=False))

    def test_elevation_request_cannot_be_faked_without_native_bridge(self) -> None:
        self._create_request_workflow(
            workflow_id="test.workflow.elevation-request",
            kind="elevation",
            save_as="bootstrap",
        )
        waiting_run = self._start("test.workflow.elevation-request")
        self.assertEqual(waiting_run["status"], "needs_elevation")

        request = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting_run['run_id']}/requests"
        ).json()["requests"][0]
        resolved = self.client.post(
            f"/api/agent-os/workflow-requests/{request['request_id']}/resolve",
            json={
                "action": "accept",
                "values": {"elevated": True, "helper_id": "elira-helper"},
            },
        )
        self.assertEqual(resolved.status_code, 400, resolved.text)
        self.assertIn("elevation result contains unsupported fields", resolved.text)
        current = self.client.get(
            f"/api/agent-os/workflow-runs/{waiting_run['run_id']}"
        )
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["status"], "needs_elevation")

    def test_bypass_suppresses_approval_request(self) -> None:
        self._create_request_workflow(
            workflow_id="test.workflow.approval-request",
            kind="approval",
            save_as="approval",
        )

        run = self._start(
            "test.workflow.approval-request",
            permission_mode="bypass",
        )

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["permission_mode"], "bypass")
        requests = self.client.get(
            f"/api/agent-os/workflow-runs/{run['run_id']}/requests"
        ).json()
        self.assertEqual(requests["total"], 0)

    def test_workflow_threads_permission_mode_to_the_canonical_tool_executor(self) -> None:
        created = self.client.post(
            "/api/agent-os/workflows",
            json={
                "id": "test.workflow.permission-mode",
                "name": "Permission propagation",
                "graph": {
                    "entry_step": "tool",
                    "steps": [
                        {
                            "id": "tool",
                            "type": "tool",
                            "tool_name": "search_memory",
                            "input_map": {"query": "$.input.query"},
                            "save_as": "result",
                            "next": None,
                        }
                    ],
                },
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        seen: dict = {}

        def run_tool(*_args, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "items": []}

        with patch(
            "app.application.tool_registry.service.run_tool",
            side_effect=run_tool,
        ):
            run = self.client.post(
                "/api/agent-os/workflow-runs",
                json={
                    "workflow_id": "test.workflow.permission-mode",
                    "input": {"query": "test"},
                    "permission_mode": "bypass",
                },
            )

        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["status"], "completed")
        self.assertEqual(seen["permission_mode"], "bypass")
        self.assertEqual(seen["source"], "workflow")

    def test_workflow_threads_permission_mode_through_agent_steps(self) -> None:
        created = self.client.post(
            "/api/agent-os/workflows",
            json={
                "id": "test.workflow.agent-permission-mode",
                "name": "Agent permission propagation",
                "graph": {
                    "entry_step": "agent",
                    "steps": [
                        {
                            "id": "agent",
                            "type": "agent",
                            "agent_id": "builtin-universal",
                            "config": {"prompt_template": "{task}"},
                            "input_map": {"task": "$.input.task"},
                            "next": None,
                        }
                    ],
                },
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        seen: dict = {}

        def run_code_agent(**kwargs):
            seen.update(kwargs)
            return {
                "ok": True,
                "response": "done",
                "error": "",
                "steps": 1,
                "tool_calls": [],
                "stop_reason": "completed",
                "partial": False,
            }

        with patch(
            "app.application.chat.runtime.run_code_agent",
            side_effect=run_code_agent,
        ):
            run = self.client.post(
                "/api/agent-os/workflow-runs",
                json={
                    "workflow_id": "test.workflow.agent-permission-mode",
                    "input": {"task": "test"},
                    "permission_mode": "bypass",
                },
            )

        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["status"], "completed")
        self.assertEqual(seen["permission_mode"], "bypass")

    def test_tool_request_resolution_is_merged_into_the_retried_step(self) -> None:
        created = self.client.post(
            "/api/agent-os/workflows",
            json={
                "id": "test.workflow.dynamic-input",
                "name": "Dynamic input",
                "graph": {
                    "entry_step": "tool",
                    "steps": [
                        {
                            "id": "tool",
                            "type": "tool",
                            "tool_name": "search_memory",
                            "input_map": {"query": "$.input.query"},
                            "save_as": "result",
                            "next": None,
                        }
                    ],
                },
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        calls: list[dict] = []

        def run_tool(_name, args, **_kwargs):
            calls.append(dict(args))
            if not args.get("project_root"):
                return {
                    "ok": False,
                    "status": "needs_input",
                    "request": {
                        "kind": "input",
                        "message": "Choose project",
                        "schema": {
                            "type": "object",
                            "properties": {"project_root": {"type": "string"}},
                            "required": ["project_root"],
                        },
                    },
                }
            return {"ok": True, "project_root": args["project_root"]}

        with patch(
            "app.application.tool_registry.service.run_tool",
            side_effect=run_tool,
        ):
            waiting = self.client.post(
                "/api/agent-os/workflow-runs",
                json={
                    "workflow_id": "test.workflow.dynamic-input",
                    "input": {"query": "test"},
                },
            ).json()
            self.assertEqual(waiting["status"], "needs_input")
            request_id = self.client.get(
                f"/api/agent-os/workflow-runs/{waiting['run_id']}/requests"
            ).json()["requests"][0]["request_id"]
            resolved = self.client.post(
                f"/api/agent-os/workflow-requests/{request_id}/resolve",
                json={
                    "action": "accept",
                    "values": {"project_root": "D:/Project"},
                },
            )

        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["run"]["status"], "completed")
        self.assertEqual(calls, [
            {"query": "test"},
            {"query": "test", "project_root": "D:/Project"},
        ])

    def test_retried_step_can_emit_a_second_replayable_request(self) -> None:
        created = self.client.post(
            "/api/agent-os/workflows",
            json={
                "id": "test.workflow.request-chain",
                "name": "Request chain",
                "graph": {
                    "entry_step": "tool",
                    "steps": [
                        {
                            "id": "tool",
                            "type": "tool",
                            "tool_name": "search_memory",
                            "input_map": {"query": "$.input.query"},
                            "next": None,
                        }
                    ],
                },
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        calls: list[dict] = []

        def run_tool(_name, args, **_kwargs):
            calls.append(dict(args))
            if not args.get("project_root"):
                return {
                    "ok": False,
                    "status": "needs_input",
                    "request": {
                        "kind": "input",
                        "schema": {
                            "type": "object",
                            "properties": {"project_root": {"type": "string"}},
                            "required": ["project_root"],
                        },
                    },
                }
            if not args.get("secret_ref"):
                return {
                    "ok": False,
                    "status": "needs_secret",
                    "request": {"kind": "secret", "message": "Token required"},
                }
            return {"ok": True}

        with patch(
            "app.application.tool_registry.service.run_tool",
            side_effect=run_tool,
        ):
            waiting = self.client.post(
                "/api/agent-os/workflow-runs",
                json={
                    "workflow_id": "test.workflow.request-chain",
                    "input": {"query": "test"},
                },
            ).json()
            first = self.client.get(
                f"/api/agent-os/workflow-runs/{waiting['run_id']}/requests",
                params={"status": "pending"},
            ).json()["requests"][0]
            second_wait = self.client.post(
                f"/api/agent-os/workflow-requests/{first['request_id']}/resolve",
                json={"action": "accept", "values": {"project_root": "D:/Project"}},
            ).json()["run"]
            self.assertEqual(second_wait["status"], "needs_secret")
            second = self.client.get(
                f"/api/agent-os/workflow-runs/{waiting['run_id']}/requests",
                params={"status": "pending"},
            ).json()["requests"][0]
            self.assertNotEqual(second["request_id"], first["request_id"])
            completed = self.client.post(
                f"/api/agent-os/workflow-requests/{second['request_id']}/resolve",
                json={"action": "accept", "values": {"secret_ref": "vault:token"}},
            )

        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["run"]["status"], "completed")
        self.assertEqual(calls[-1], {
            "query": "test",
            "project_root": "D:/Project",
            "secret_ref": "vault:token",
        })

    def test_code_agent_workflow_preserves_partial_completion_status(self) -> None:
        db_path = workflow_db_path.get_workflow_db_path()
        start_code_agent_workflow_run(
            db_path=db_path,
            workflow_run_id="wf-partial",
            code_agent_run_id="agent-partial",
            permission_mode="bypass",
        )
        finished = finish_code_agent_workflow_run(
            db_path=db_path,
            workflow_run_id="wf-partial",
            done_event={
                "ok": True,
                "stop_reason": "answer",
                "completion_status": "unverified",
                "partial": True,
            },
        )

        self.assertEqual(finished["status"], "partial")


if __name__ == "__main__":
    unittest.main()
