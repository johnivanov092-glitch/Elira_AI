"""Tests — Runs API (P2 Шаг 6).

Verifies that GET /api/agent-os/runs returns tool.executed events
with correct payload extraction and filtering.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class TestRunsRoute(unittest.TestCase):

    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.agent_monitor_routes import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def _mock_events(self, items: list[dict]) -> mock.MagicMock:
        """Return a context manager that patches event_bus.list_events."""
        return mock.patch(
            "app.application.event_bus.runtime.list_events",
            return_value=(items, len(items)),
        )

    def _make_event(self, tool_name: str, agent_id: str = "code-agent",
                    source: str = "code_agent", run_id: str = "run-1",
                    status: str = "ok", ok: bool = True) -> dict:
        return {
            "event_id": f"evt-{tool_name}",
            "event_type": "tool.executed",
            "created_at": "2026-06-01T12:00:00+00:00",
            "payload": {
                "tool_name": tool_name,
                "agent_id": agent_id,
                "source": source,
                "run_id": run_id,
                "project_scope_id": "scope:test",
                "workflow_id": "",
                "step_id": "",
                "status": status,
                "ok": ok,
                "error": None,
            },
        }

    def test_runs_empty(self):
        client = self._make_client()
        with self._mock_events([]):
            r = client.get("/api/agent-os/runs")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["items"], [])

    def test_runs_returns_items(self):
        evts = [
            self._make_event("run_bash", agent_id="code-agent"),
            self._make_event("glob", agent_id="code-agent"),
        ]
        client = self._make_client()
        with self._mock_events(evts):
            r = client.get("/api/agent-os/runs")
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["tool_name"], "run_bash")
        self.assertEqual(items[0]["event_id"], "evt-run_bash")
        self.assertIn("created_at", items[0])

    def test_runs_filter_by_agent_id(self):
        evts = [
            self._make_event("glob", agent_id="code-agent"),
            self._make_event("search_web", agent_id="chat"),
        ]
        client = self._make_client()
        with self._mock_events(evts):
            r = client.get("/api/agent-os/runs?agent_id=chat")
        items = r.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["agent_id"], "chat")

    def test_runs_filter_by_source(self):
        evts = [
            self._make_event("glob", source="code_agent"),
            self._make_event("search_web", source="chat"),
        ]
        client = self._make_client()
        with self._mock_events(evts):
            r = client.get("/api/agent-os/runs?source=code_agent")
        items = r.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "code_agent")

    def test_runs_payload_fields_extracted(self):
        evts = [self._make_event("write_file", status="waiting_approval", ok=False)]
        client = self._make_client()
        with self._mock_events(evts):
            r = client.get("/api/agent-os/runs")
        item = r.json()["items"][0]
        self.assertEqual(item["tool_name"], "write_file")
        self.assertEqual(item["status"], "waiting_approval")
        self.assertFalse(item["ok"])
        self.assertIsNone(item["error"])

    def test_runs_limit_query_param(self):
        evts = [self._make_event(f"tool_{i}") for i in range(10)]
        client = self._make_client()
        # Verify that limit is forwarded to list_events
        with mock.patch(
            "app.application.event_bus.runtime.list_events",
            return_value=(evts[:3], 10),
        ) as m:
            r = client.get("/api/agent-os/runs?limit=3")
        m.assert_called_once_with(event_type="tool.executed", limit=3, offset=0)
        self.assertEqual(r.json()["total"], 10)


if __name__ == "__main__":
    unittest.main()
