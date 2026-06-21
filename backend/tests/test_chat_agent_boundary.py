from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class ChatAgentBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_elira_data_dir = os.environ.get("ELIRA_DATA_DIR")
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name

        import app.core.data_files as data_files
        import app.application.chat_agent.state as state
        import app.api.routes.chat_agent as routes

        self.data_files = importlib.reload(data_files)
        self.state = importlib.reload(state)
        self.routes = importlib.reload(routes)

    def tearDown(self) -> None:
        if self._old_elira_data_dir is None:
            os.environ.pop("ELIRA_DATA_DIR", None)
        else:
            os.environ["ELIRA_DATA_DIR"] = self._old_elira_data_dir
        import app.core.data_files as data_files
        import app.application.tool_registry.runtime as tool_registry_runtime

        importlib.reload(data_files)
        importlib.reload(tool_registry_runtime).seed_builtin_tools()
        self._tmp.cleanup()

    def test_chat_agent_uses_own_db_and_active_project(self) -> None:
        chat_project = Path(self._tmp.name) / "chat_project"
        code_project = Path(self._tmp.name) / "code_project"
        chat_project.mkdir()
        code_project.mkdir()
        (chat_project / "brief.md").write_text("chat workspace brief", encoding="utf-8")
        (code_project / "secret.py").write_text("code agent only", encoding="utf-8")
        self.state.upsert_project(str(chat_project), active=True)

        payload = self.routes.ChatAgentRequest(
            model_name="local-model",
            profile_name="default",
            user_input="analyze project",
        )
        with patch.object(
            self.routes,
            "run_chat",
            return_value={"ok": True, "answer": "ok", "warnings": [], "meta": {}},
        ) as run_chat_mock:
            response = self.routes.send(payload)

        self.assertTrue((Path(self._tmp.name) / "chat_agent.db").exists())
        self.assertFalse((Path(self._tmp.name) / "code_agent_sessions.db").exists())
        self.assertEqual(response.status_code, 200)
        task_context = run_chat_mock.call_args.kwargs["task_context"]
        self.assertIn(str(chat_project.resolve()), task_context)
        self.assertIn("brief.md", task_context)
        self.assertNotIn(str(code_project.resolve()), task_context)
        self.assertNotIn("secret.py", task_context)

    def test_chat_agent_does_not_create_default_project(self) -> None:
        self.assertEqual(self.state.active_project(), {})
        self.assertEqual(self.state.list_projects(), [])

    def test_chat_agent_can_remove_active_project_without_fallback_workspace(self) -> None:
        chat_project = Path(self._tmp.name) / "chat_project"
        chat_project.mkdir()
        project = self.state.upsert_project(str(chat_project), active=True)

        self.assertTrue(self.state.remove_project(str(project["id"])))
        self.assertEqual(self.state.active_project(), {})

    def test_chat_agent_adds_own_web_context_for_current_queries(self) -> None:
        payload = self.routes.ChatAgentRequest(
            model_name="local-model",
            profile_name="default",
            user_input="latest AI news today",
        )
        web_context = {
            "attempted": True,
            "used": True,
            "mode": "news",
            "context": "WEB SEARCH CONTEXT\n[1] AI news\nURL: https://example.test/news",
            "results": [{"title": "AI news", "url": "https://example.test/news"}],
            "errors": {},
        }
        with patch.object(self.routes.web_tools, "collect_web_context", return_value=web_context), patch.object(
            self.routes,
            "run_chat",
            return_value={"ok": True, "answer": "ok", "warnings": [], "meta": {}},
        ) as run_chat_mock:
            response = self.routes.send(payload)

        self.assertEqual(response.status_code, 200)
        task_context = run_chat_mock.call_args.kwargs["task_context"]
        self.assertIn("WEB SEARCH CONTEXT", task_context)
        data = json.loads(response.body)
        self.assertEqual(data["meta"]["tools"], ["web_search"])
        self.assertEqual(data["tool_results"][0]["tool"], "web_search")

    def test_chat_agent_saves_and_recalls_own_memory(self) -> None:
        save_payload = self.routes.ChatAgentRequest(
            model_name="local-model",
            profile_name="default",
            user_input="Меня зовут Алексей",
        )
        recall_payload = self.routes.ChatAgentRequest(
            model_name="local-model",
            profile_name="default",
            user_input="Как меня зовут?",
        )
        with patch.object(
            self.routes,
            "run_chat",
            return_value={"ok": True, "answer": "ok", "warnings": [], "meta": {}},
        ):
            save_response = self.routes.send(save_payload)

        self.assertEqual(save_response.status_code, 200)
        memories = self.state.list_memory()
        self.assertEqual(len(memories), 1)
        self.assertIn("Алексей", memories[0]["text"])

        with patch.object(
            self.routes,
            "run_chat",
            return_value={"ok": True, "answer": "ok", "warnings": [], "meta": {}},
        ) as run_chat_mock:
            recall_response = self.routes.send(recall_payload)

        self.assertEqual(recall_response.status_code, 200)
        task_context = run_chat_mock.call_args.kwargs["task_context"]
        self.assertIn("CHAT AGENT MEMORY CONTEXT", task_context)
        self.assertIn("Алексей", task_context)

    def test_chat_agent_context_window_is_capped_to_server_limit(self) -> None:
        payload = self.routes.ChatAgentRequest(
            model_name="local-model",
            profile_name="default",
            user_input="hello",
            num_ctx=32768,
        )
        with patch.object(
            self.routes,
            "get_models",
            return_value={"models": [{"name": "local-model", "context_window": 16384}]},
        ), patch.object(
            self.routes,
            "run_chat",
            return_value={"ok": True, "answer": "ok", "warnings": [], "meta": {}},
        ) as run_chat_mock:
            response = self.routes.send(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(run_chat_mock.call_args.kwargs["num_ctx"], 16384)

    def test_settings_context_window_is_capped_to_server_limit(self) -> None:
        payload = self.routes.SettingsRequest(
            context_window=32768,
            default_model="local-model",
            agent_profile="Универсальный",
        )
        with patch.object(
            self.routes,
            "get_models",
            return_value={"models": [{"name": "local-model", "context_window": 16384}]},
        ):
            result = self.routes.settings_put(payload)

        self.assertEqual(result["context_window"], 16384)
        self.assertEqual(result["server_context_window"], 16384)

    def test_chat_agent_request_has_no_code_agent_tool_flags(self) -> None:
        forbidden = {
            "direct_llm",
            "use_python_exec",
            "use_file_gen",
            "use_http_api",
            "use_sql",
            "use_screenshot",
            "use_plugins",
            "project_root",
            "max_steps",
            "approval_wait_seconds",
        }
        self.assertFalse(forbidden & set(self.routes.ChatAgentRequest.model_fields))

    def test_chat_agent_modules_do_not_import_code_agent_or_shared_tool_runtime(self) -> None:
        files = [
            BACKEND_ROOT / "app" / "api" / "routes" / "chat_agent.py",
            BACKEND_ROOT / "app" / "application" / "chat_agent" / "state.py",
            BACKEND_ROOT / "app" / "application" / "chat_agent" / "project_tools.py",
            BACKEND_ROOT / "app" / "application" / "chat_agent" / "web_tools.py",
        ]
        forbidden = [
            "app.application.code_agent",
            "app.application.advanced",
            "app.application.tool_registry",
            "app.application.rag_memory",
            "app.application.smart_memory",
            "SshToolProvider",
            "build_mcp_providers",
            "run_tool",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{needle} leaked into {path}")


if __name__ == "__main__":
    unittest.main()
