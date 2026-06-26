from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes.event_bus_routes import router as event_bus_router  # noqa: E402
from app.application.event_bus import runtime as bus  # noqa: E402


class EventBusDbMixin(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_db_path = bus.DB_PATH
        bus.DB_PATH = Path(self._tmpdir.name) / "event_bus.db"
        bus._init_db()

    def tearDown(self) -> None:
        bus.DB_PATH = self._original_db_path
        self._tmpdir.cleanup()
        super().tearDown()


class EventBusServiceTest(EventBusDbMixin):
    def test_emit_and_list_events(self) -> None:
        created = bus.emit_event(
            event_type="agent.run.started",
            payload={"run_id": "run-1"},
            source_agent_id="builtin-researcher",
        )

        self.assertEqual(created["event_type"], "agent.run.started")
        self.assertEqual(created["payload"]["run_id"], "run-1")

        events, total = bus.list_events(event_type="agent.run.started")
        self.assertEqual(total, 1)
        self.assertEqual(events[0]["event_id"], created["event_id"])

    def test_subscribe_list_and_unsubscribe(self) -> None:
        created = bus.subscribe(
            subscriber_id="builtin-analyst",
            event_type="workflow.step.completed",
            handler_name="on_workflow_step",
        )
        self.assertEqual(created["subscriber_id"], "builtin-analyst")

        subscriptions, total = bus.list_subscriptions(subscriber_id="builtin-analyst")
        self.assertEqual(total, 1)
        self.assertEqual(subscriptions[0]["handler_name"], "on_workflow_step")

        removed = bus.unsubscribe("builtin-analyst", "workflow.step.completed")
        self.assertTrue(removed["removed"])

        subscriptions, total = bus.list_subscriptions(subscriber_id="builtin-analyst")
        self.assertEqual(total, 0)
        self.assertEqual(subscriptions, [])

    def test_send_list_and_mark_messages(self) -> None:
        created = bus.send_message(
            from_agent="builtin-researcher",
            to_agent="builtin-analyst",
            content={"text": "Need summary"},
        )
        self.assertFalse(created["read"])

        messages, total = bus.get_agent_messages("builtin-analyst")
        self.assertEqual(total, 1)
        self.assertEqual(messages[0]["content"]["text"], "Need summary")

        updated = bus.mark_message_read(created["message_id"], read=True)
        self.assertIsNotNone(updated)
        self.assertTrue(updated["read"])

        unread, unread_total = bus.get_agent_messages("builtin-analyst", unread_only=True)
        self.assertEqual(unread_total, 0)
        self.assertEqual(unread, [])


class EventBusRoutesTest(EventBusDbMixin):
    def setUp(self) -> None:
        super().setUp()
        app = FastAPI()
        app.include_router(event_bus_router)
        self.client = TestClient(app)

    def test_events_and_messages_routes(self) -> None:
        event_response = self.client.post(
            "/api/agent-os/events",
            json={
                "event_type": "agent.run.started",
                "payload": {"run_id": "run-abc"},
                "source_agent_id": "builtin-programmer",
            },
        )
        self.assertEqual(event_response.status_code, 200)
        event_payload = event_response.json()
        self.assertEqual(event_payload["payload"]["run_id"], "run-abc")

        list_response = self.client.get("/api/agent-os/events", params={"event_type": "agent.run.started"})
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["total"], 1)

        message_response = self.client.post(
            "/api/agent-os/messages",
            json={
                "from_agent": "builtin-programmer",
                "to_agent": "builtin-analyst",
                "content": {"text": "Check results"},
            },
        )
        self.assertEqual(message_response.status_code, 200)
        message = message_response.json()
        self.assertEqual(message["to_agent"], "builtin-analyst")

        inbox_response = self.client.get("/api/agent-os/agents/builtin-analyst/messages")
        self.assertEqual(inbox_response.status_code, 200)
        self.assertEqual(inbox_response.json()["total"], 1)

        read_response = self.client.patch(
            f"/api/agent-os/messages/{message['message_id']}/read",
            json={"read": True},
        )
        self.assertEqual(read_response.status_code, 200)
        self.assertTrue(read_response.json()["read"])

    def test_subscription_routes(self) -> None:
        create_response = self.client.post(
            "/api/agent-os/subscriptions",
            json={
                "subscriber_id": "builtin-universal",
                "event_type": "workflow.step.completed",
                "handler_name": "notify",
            },
        )
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get("/api/agent-os/subscriptions")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["total"], 1)

        delete_response = self.client.delete(
            "/api/agent-os/subscriptions",
            params={"subscriber_id": "builtin-universal", "event_type": "workflow.step.completed"},
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["removed"])


if __name__ == "__main__":
    unittest.main()
