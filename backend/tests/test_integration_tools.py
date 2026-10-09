"""The integration tools: mcp, telegram, memory, library, recall, itops_registry."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.skill_services.registry import tool_itops_registry  # noqa: E402
from app.application.code_agent.tools._memory import tool_library, tool_memory  # noqa: E402
from app.application.code_agent.tools._mcp import tool_mcp  # noqa: E402
from app.application.code_agent.tools._search import tool_recall  # noqa: E402
from app.application.skill_services.telegram_actions import tool_telegram  # noqa: E402


class McpControlTest(unittest.TestCase):
    def test_inner_runtime_result_requires_boolean_ok(self) -> None:
        with patch(
            "app.application.code_agent.tools._mcp._mcp_control",
            return_value={"status": "completed"},
        ):
            result = tool_mcp(ROOT, action="list")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["message"], "invalid_tool_result")

    def test_unknown_action_names_the_supported_ones(self) -> None:
        result = tool_mcp(ROOT, action="telegram_send")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unsupported_action")
        self.assertIn("start", result["text"])

    def test_list_shows_purpose_state_and_skill_without_config(self) -> None:
        with patch(
            "app.application.tool_providers.mcp_runtime.list_servers",
            return_value=[
                {"id": "homeassistant", "description": "Умный дом", "transport": "http", "url": "http://h/mcp",
                 "secret_header_refs": {"Authorization": "sref_ha"}, "enabled": True, "status": "running",
                 "last_error": None},
                {"id": "blender", "command": "blendmcp", "enabled": False, "status": "stopped", "last_error": None},
            ],
        ):
            result = tool_mcp(ROOT, action="list")

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["servers"], [
            {"id": "homeassistant", "description": "Умный дом", "status": "running", "skill": "homeassistant-mcp"},
            {"id": "blender", "description": "", "status": "switched_off", "skill": "blender-mcp"},
        ])
        self.assertNotIn("sref_ha", result["text"])

    def test_add_changes_only_given_fields_and_keeps_secret_refs(self) -> None:
        stored = {"id": "github", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                  "env": {}, "env_secret_refs": {"GITHUB_PERSONAL_ACCESS_TOKEN": "sref_github"},
                  "enabled": True, "status": "stopped", "last_error": None}
        with (
            patch("app.application.tool_providers.mcp_runtime.list_servers", return_value=[stored]),
            patch("app.application.tool_providers.mcp_runtime.save_servers",
                  side_effect=lambda servers: servers) as save,
        ):
            result = tool_mcp(ROOT, action="add", server_id="github",
                              config={"description": "GitHub: репозитории и PR"})

        self.assertTrue(result["ok"], result)
        saved = save.call_args.args[0][0]
        self.assertEqual(saved["description"], "GitHub: репозитории и PR")
        self.assertEqual(saved["env_secret_refs"], {"GITHUB_PERSONAL_ACCESS_TOKEN": "sref_github"})
        self.assertEqual(saved["args"], ["-y", "@modelcontextprotocol/server-github"])
        self.assertNotIn("sref_github", result["text"])

    def test_remove_of_unknown_server_is_correctable(self) -> None:
        with (
            patch("app.application.tool_providers.mcp_runtime.list_servers", return_value=[]),
            patch("app.application.tool_providers.mcp_runtime.save_servers") as save,
        ):
            result = tool_mcp(ROOT, action="remove", server_id="ghost")

        self.assertFalse(result["ok"])
        self.assertIn("not configured", result["error"]["message"])
        save.assert_not_called()

    def test_missing_mcp_id_returns_model_error_without_workflow_request(self) -> None:
        with patch("app.application.tool_providers.mcp_runtime.save_servers") as save:
            result = tool_mcp(
                ROOT, action="add",
                config={"command": "python", "args": ["stock_server.py"]},
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("server_id or config.id", result["error"]["message"])
        self.assertNotIn("request", result)
        save.assert_not_called()

    def test_mcp_upsert_keeps_nonsecret_environment_settings(self) -> None:
        settings = {"STOCK_BASE_URL": "http://127.0.0.1:54522", "MAX_TOKENS": "8192",
                    "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "6"}
        with (
            patch("app.application.tool_providers.mcp_runtime.list_servers", return_value=[]),
            patch("app.application.tool_providers.mcp_runtime.save_servers",
                  side_effect=lambda servers: servers) as save,
        ):
            result = tool_mcp(ROOT, action="add", server_id="stock",
                              config={"command": "python", "env": settings})
        self.assertTrue(result["ok"], result)
        self.assertNotIn("request", result)
        self.assertEqual(save.call_args.args[0][0]["env"], settings)

    def test_mcp_upsert_credential_environment_still_requests_secret(self) -> None:
        with patch("app.application.tool_providers.mcp_runtime.save_servers") as save:
            result = tool_mcp(ROOT, action="add", server_id="stock",
                              config={"command": "python", "env": {"STOCK_API_KEY": "canary-credential"}})
        self.assertEqual(result["status"], "needs_secret")
        self.assertNotIn("canary-credential", result["text"])
        save.assert_not_called()

    def test_invalid_mcp_replacement_does_not_drop_existing_server(self) -> None:
        with (
            patch("app.application.tool_providers.mcp_runtime.list_servers",
                  return_value=[{"id": "stock", "command": "python", "args": ["existing.py"]}]),
            patch("app.application.tool_providers.mcp_runtime.save_servers") as save,
        ):
            result = tool_mcp(ROOT, action="add", server_id="stock",
                              config={"command": "python", "env": ["malformed"]})
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("request", result)
        save.assert_not_called()

    def test_mcp_start_with_secret_ref_and_locked_vault_requests_ui_unlock(self) -> None:
        with (
            patch(
                "app.application.tool_providers.mcp_runtime.list_servers",
                return_value=[{"id": "mikrotik", "env_secret_refs": {"ROUTER_PASS": "sref_router_password"}}],
            ),
            patch("app.infrastructure.secrets.vault.status", return_value={"initialized": True, "locked": True}),
            patch("app.application.tool_providers.mcp_runtime.start_server") as start,
        ):
            result = tool_mcp(ROOT, action="start", server_id="mikrotik")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_secret")
        self.assertEqual(result["request"]["kind"], "secret")
        self.assertEqual(result["request"]["schema"]["x-elira-existing-secret-ref"], "sref_router_password")
        start.assert_not_called()

    def test_mcp_tools_reads_catalog_without_restarting_server(self) -> None:
        with patch(
            "app.application.tool_providers.mcp_runtime.discover_tools",
            return_value={"ok": True, "server_id": "unity", "available_tool_count": 47,
                          "available_tool_names": ["unity__read_console"]},
        ) as discover:
            result = tool_mcp(ROOT, action="tools", server_id="unity", query="read_console")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        discover.assert_called_once_with("unity")


class TelegramToolTest(unittest.TestCase):
    def test_send_uses_the_typed_runtime(self) -> None:
        with (
            patch("app.application.telegram.store.get_config_value", return_value="sref_telegram_bot"),
            patch("app.infrastructure.secrets.vault.status", return_value={"initialized": True, "locked": False}),
            patch("app.application.telegram.send_telegram_message",
                  return_value={"ok": True, "message_id": 91, "chat_id": 100200300}) as send,
        ):
            result = tool_telegram(action="send", chat_id=100200300, text="TELEGRAM_CANARY")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["message_id"], 91)
        send.assert_called_once_with(chat_id=100200300, text="TELEGRAM_CANARY", parse_mode="Markdown")

    def test_send_without_token_requests_a_secret(self) -> None:
        with patch("app.application.telegram.store.get_config_value", return_value=""):
            result = tool_telegram(action="send", chat_id=1, text="hi")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_secret")

    def test_messages_reads_the_durable_runtime_log(self) -> None:
        with patch(
            "app.application.telegram.get_telegram_log",
            return_value={"ok": True, "log": [{"chat_id": 100200300, "direction": "in", "text": "Привет"}], "count": 1},
        ) as get_log:
            result = tool_telegram(action="messages", chat_id=100200300, limit=20)

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["count"], 1)
        get_log.assert_called_once_with(limit=20, chat_id=100200300)


class MemoryAndLibraryToolTest(unittest.TestCase):
    def test_search_lists_ids_for_corrections(self) -> None:
        with patch(
            "app.application.memory.search_facts",
            return_value={"ok": True, "items": [{"id": 42, "text": "Имя — Пётр", "category": "user_fact",
                                                  "source": "user_message"}]},
        ):
            result = tool_memory(action="search", query="имя")

        self.assertTrue(result["ok"])
        self.assertIn("id=42", result["text"])

    def test_correction_requires_the_replaced_id(self) -> None:
        with patch("app.application.memory.add_fact") as add_fact:
            result = tool_memory(action="add", text="Моё имя Пётр.", correction=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "id_required")
        add_fact.assert_not_called()

    def test_add_with_id_is_an_explicit_correction(self) -> None:
        with patch(
            "app.application.memory.add_fact",
            return_value={"ok": True, "action": "corrected", "id": 42},
        ) as add_fact:
            result = tool_memory(action="add", text="Моё имя Пётр.", correction=True, id=42)

        self.assertTrue(result["ok"])
        self.assertEqual(add_fact.call_args.kwargs["replaces_id"], 42)
        self.assertEqual(add_fact.call_args.kwargs["importance"], 10)

    def test_delete_without_id_has_no_side_effect(self) -> None:
        with patch("app.application.memory.delete_fact") as delete:
            result = tool_memory(action="delete")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "id_required")
        delete.assert_not_called()

    def test_library_read_exposes_a_bounded_page(self) -> None:
        with patch(
            "app.application.library.runtime.read_library_file",
            return_value={"ok": True, "file_id": 7, "name": "прайс.pdf", "offset": 0, "content_chars": 20000,
                          "text": "страница", "has_more": True, "next_offset": 8000},
        ) as read_file:
            result = tool_library(action="read", id=7)

        self.assertTrue(result["ok"])
        self.assertIn("offset=8000", result["text"])
        read_file.assert_called_once_with(7, offset=0)

    def test_library_read_without_id_is_correctable(self) -> None:
        with patch("app.application.library.runtime.read_library_file") as read_file:
            result = tool_library(action="read")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "id_required")
        read_file.assert_not_called()

    def test_library_empty_inventory_ends_search(self) -> None:
        with patch("app.application.library.runtime.search_files", return_value={"ok": True, "items": []}) as search:
            result = tool_library(action="search")

        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], 0)
        self.assertIn("Библиотека пуста", result["text"])
        self.assertNotIn("Попробуй", result["text"])
        search.assert_called_once_with("", limit=20)

    def test_library_no_match_does_not_claim_inventory_is_empty(self) -> None:
        with patch("app.application.library.runtime.search_files", return_value={"ok": True, "items": []}) as search:
            result = tool_library(action="search", query="world war z")

        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], 0)
        self.assertNotIn("Библиотека пуста", result["text"])
        self.assertNotIn("Попробуй", result["text"])
        self.assertIn("Не повторяй тот же запрос", result["text"])
        search.assert_called_once_with("world war z", limit=20)

    def test_library_search_error_is_not_a_successful_empty_result(self) -> None:
        with patch("app.application.library.runtime.search_files",
                   return_value={"ok": False, "error": "storage_unavailable"}):
            result = tool_library(action="search", query="прайс")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "storage_unavailable")


class RecallAndItopsToolTest(unittest.TestCase):
    def test_recall_index_uses_the_connected_project_by_default(self) -> None:
        with patch(
            "app.application.code_agent.indexing.index_project",
            return_value={"ok": True, "files_indexed": 2, "complete": True},
        ) as index_project:
            result = tool_recall(ROOT, action="index")

        self.assertTrue(result["ok"])
        index_project.assert_called_once_with(ROOT.resolve(), replace=True)

    def test_recall_status_accepts_a_relative_path_inside_the_project(self) -> None:
        with patch(
            "app.application.code_agent.indexing.project_corpus_status",
            return_value={"ok": True, "files": 1, "chunks": 2},
        ) as project_status:
            result = tool_recall(ROOT, action="status", path="backend")

        self.assertTrue(result["ok"])
        project_status.assert_called_once_with((ROOT / "backend").resolve())

    def test_itops_registry_missing_asset_id_is_correctable(self) -> None:
        with patch("app.infrastructure.it_ops.store.init_db"), \
                patch("app.infrastructure.it_ops.store.delete_asset") as delete:
            result = tool_itops_registry(action="asset_remove")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("asset_id", result["error"]["message"])
        delete.assert_not_called()

    def test_itops_registry_plaintext_credential_requests_a_secret(self) -> None:
        with patch("app.infrastructure.it_ops.store.init_db"):
            result = tool_itops_registry(
                action="profile_upsert", profile_id="p1", asset_id="a1",
                config={"password": "must-not-be-persisted"},
            )

        self.assertEqual(result["status"], "needs_secret")
        self.assertNotIn("must-not-be-persisted", result["text"])


if __name__ == "__main__":
    unittest.main()
