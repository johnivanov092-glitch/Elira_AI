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

from app.api.routes.event_bus_routes import router as event_bus_router  # noqa: E402
from app.services import agents_service  # noqa: E402
from app.services import event_bus as bus  # noqa: E402


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


class EventBusIntegrationTest(EventBusDbMixin):
    def _base_plan(self) -> dict:
        return {
            "route": "chat",
            "tools": [],
            "temporal": {"mode": "none", "requires_web": False, "freshness_sensitive": False, "years": []},
            "web_plan": {"is_multi_intent": False, "subqueries": [], "passes": [], "pass_count": 1, "overflow_applied": False, "uncovered_subqueries": []},
        }

    def test_run_agent_emits_started_and_completed(self) -> None:
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
                session_id="session-1",
                use_memory=False,
                use_library=False,
                use_web_search=False,
            )

        self.assertTrue(result["ok"])
        events, total = bus.list_events(limit=10)
        self.assertEqual(total, 2)
        event_types = [event["event_type"] for event in events]
        self.assertIn("agent.run.started", event_types)
        self.assertIn("agent.run.completed", event_types)
        completed = next(event for event in events if event["event_type"] == "agent.run.completed")
        self.assertTrue(completed["payload"]["ok"])
        self.assertEqual(completed["payload"]["model_used"], "test-model")

    def test_run_agent_emits_failed_completion_event(self) -> None:
        with patch.object(agents_service.PlannerV2Service, "plan", return_value=self._base_plan()), \
             patch.object(agents_service, "_collect_context", return_value=""), \
             patch.object(agents_service, "run_chat", return_value={"ok": False, "warnings": ["boom"]}), \
             patch.object(agents_service, "pick_model_for_route", return_value="test-model"):
            result = agents_service.run_agent(
                model_name="test-model",
                profile_name="Universal",
                user_input="Hello",
                session_id="session-2",
                use_memory=False,
                use_library=False,
                use_web_search=False,
            )

        self.assertFalse(result["ok"])
        events, total = bus.list_events(limit=10)
        self.assertEqual(total, 2)
        completed = next(event for event in events if event["event_type"] == "agent.run.completed")
        self.assertFalse(completed["payload"]["ok"])
        self.assertIn("boom", completed["payload"]["error"])

    def test_run_agent_stream_emits_started_and_completed(self) -> None:
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
                    user_input="Stream hello",
                    session_id="session-stream",
                    use_memory=False,
                    use_library=False,
                    use_web_search=False,
                )
            )

        self.assertTrue(events[-1]["done"])
        bus_events, total = bus.list_events(limit=10)
        self.assertEqual(total, 2)
        completed = next(event for event in bus_events if event["event_type"] == "agent.run.completed")
        self.assertTrue(completed["payload"]["ok"])


if __name__ == "__main__":
    unittest.main()
