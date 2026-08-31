from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._runtime_control import tool_runtime_control  # noqa: E402
from app.application.agent_kernel.execution_context import (  # noqa: E402
    reset_permission_mode,
    set_permission_mode,
)
from app.application.workflows import db_path as workflow_db_path  # noqa: E402
from app.application.projects.scope import project_scope_id  # noqa: E402


class RuntimeControlContractTest(unittest.TestCase):
    def test_inner_runtime_result_requires_boolean_ok(self) -> None:
        with patch(
            "app.application.code_agent.tools._runtime_control._runtime_status",
            return_value={"status": "completed"},
        ):
            result = tool_runtime_control(ROOT, operation="status")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["message"], "invalid_tool_result")

    def test_failure_is_typed(self) -> None:
        result = tool_runtime_control(ROOT, operation="not_supported")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["operation"], "not_supported")
        self.assertEqual(result["error"]["code"], "ValueError")
        self.assertFalse(result["error"]["retryable"])

    def test_missing_scalar_returns_needs_input(self) -> None:
        result = tool_runtime_control(ROOT, operation="plugin_info")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["request"]["kind"], "input")
        self.assertIn("name", result["request"]["schema"]["required"])

    def test_plaintext_credential_returns_needs_secret(self) -> None:
        result = tool_runtime_control(
            ROOT,
            operation="telegram_configure",
            config={"bot_token": "must-not-be-persisted"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_secret")
        self.assertTrue(result["request"]["sensitive"])
        self.assertNotIn("must-not-be-persisted", result["text"])

    def test_telegram_send_uses_the_typed_runtime(self) -> None:
        with (
            patch(
                "app.application.telegram.store.get_config_value",
                return_value="sref_telegram_bot",
            ),
            patch(
                "app.infrastructure.secrets.vault.status",
                return_value={"initialized": True, "locked": False},
            ),
            patch(
                "app.application.telegram.send_telegram_message",
                return_value={"ok": True, "message_id": 91, "chat_id": 100200300},
            ) as send,
        ):
            result = tool_runtime_control(
                ROOT,
                operation="telegram_send",
                chat_id=100200300,
                query="TELEGRAM_CANARY",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["message_id"], 91)
        send.assert_called_once_with(
            chat_id=100200300,
            text="TELEGRAM_CANARY",
            parse_mode="Markdown",
        )

    def test_telegram_messages_reads_the_durable_runtime_log(self) -> None:
        with patch(
            "app.application.telegram.get_telegram_log",
            return_value={
                "ok": True,
                "log": [{"chat_id": 100200300, "direction": "in", "text": "Привет"}],
                "count": 1,
            },
        ) as get_log:
            result = tool_runtime_control(
                ROOT,
                operation="telegram_messages",
                chat_id=100200300,
                config={"limit": 20},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["count"], 1)
        get_log.assert_called_once_with(limit=20, chat_id=100200300)

    def test_mcp_start_with_secret_ref_and_locked_vault_requests_ui_unlock(self) -> None:
        with (
            patch(
                "app.application.tool_providers.mcp_runtime.list_servers",
                return_value=[{
                    "id": "mikrotik",
                    "env_secret_refs": {"ROUTER_PASS": "sref_router_password"},
                }],
            ),
            patch(
                "app.infrastructure.secrets.vault.status",
                return_value={"initialized": True, "locked": True},
            ),
            patch("app.application.tool_providers.mcp_runtime.start_server") as start,
        ):
            result = tool_runtime_control(
                ROOT,
                operation="mcp_start",
                server_id="mikrotik",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_secret")
        self.assertEqual(result["request"]["kind"], "secret")
        self.assertEqual(
            result["request"]["schema"]["x-elira-existing-secret-ref"],
            "sref_router_password",
        )
        start.assert_not_called()

    def test_mcp_tools_reads_catalog_without_restarting_server(self) -> None:
        with patch(
            "app.application.tool_providers.mcp_runtime.discover_tools",
            return_value={
                "ok": True,
                "server_id": "unity",
                "available_tool_count": 47,
                "available_tool_names": ["unity__read_console"],
            },
        ) as discover:
            result = tool_runtime_control(
                ROOT,
                operation="mcp_tools",
                server_id="unity",
                query="read_console",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        discover.assert_called_once_with("unity")

    def test_memory_read_is_wrapped_as_completed(self) -> None:
        with patch(
            "app.application.memory.facade.fact_stats",
            return_value={"ok": True, "count": 4},
        ):
            result = tool_runtime_control(ROOT, operation="memory_stats")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["count"], 4)

    def test_memory_add_with_target_is_an_explicit_correction(self) -> None:
        with patch(
            "app.application.memory.facade.add_fact",
            return_value={"ok": True, "action": "corrected", "id": 42},
        ) as add_fact:
            result = tool_runtime_control(
                ROOT,
                operation="memory_add",
                memory_id=42,
                query="Моё имя Пётр.",
            )

        self.assertTrue(result["ok"])
        add_fact.assert_called_once_with(
            "Моё имя Пётр.",
            category="fact",
            source="user_correction",
            importance=5,
            profile=None,
            replaces_id=42,
        )

    def test_memory_add_accepts_the_model_friendly_config_fact_alias(self) -> None:
        with patch(
            "app.application.memory.facade.add_fact",
            return_value={"ok": True, "action": "created", "id": 54},
        ) as add_fact:
            result = tool_runtime_control(
                ROOT,
                operation="memory_add",
                config={"fact": "Пользователь предпочитает берёзовый чай."},
            )

        self.assertTrue(result["ok"])
        add_fact.assert_called_once_with(
            "Пользователь предпочитает берёзовый чай.",
            category="fact",
            source="runtime_control",
            importance=5,
            profile=None,
            replaces_id=None,
        )

    def test_lsp_upsert_accepts_model_friendly_name_and_kind_aliases(self) -> None:
        with (
            patch(
                "app.application.tool_providers.lsp_runtime.list_servers",
                return_value=[],
            ),
            patch(
                "app.application.tool_providers.lsp_runtime.save_servers",
                return_value=[{
                    "id": "pyright",
                    "language": "python",
                    "command": "pyright-langserver",
                    "args": ["--stdio"],
                    "enabled": True,
                }],
            ) as save_servers,
        ):
            result = tool_runtime_control(
                ROOT,
                operation="lsp_upsert",
                name="pyright",
                kind="python",
                config={
                    "command": "pyright-langserver",
                    "args": ["--stdio"],
                    "enabled": True,
                },
            )

        self.assertTrue(result["ok"])
        save_servers.assert_called_once_with([{
            "id": "pyright",
            "language": "python",
            "command": "pyright-langserver",
            "args": ["--stdio"],
            "enabled": True,
        }])

    def test_lsp_upsert_infers_language_from_pyright_when_kind_is_generic(self) -> None:
        with (
            patch(
                "app.application.tool_providers.lsp_runtime.list_servers",
                return_value=[],
            ),
            patch(
                "app.application.tool_providers.lsp_runtime.save_servers",
                return_value=[{
                    "id": "python-pyright",
                    "language": "python",
                    "command": "pyright-langserver",
                    "args": ["--stdio"],
                    "enabled": False,
                }],
            ) as save_servers,
        ):
            result = tool_runtime_control(
                ROOT,
                operation="lsp_upsert",
                name="python-pyright",
                kind="lsp",
                config={"command": "pyright-langserver", "args": ["--stdio"]},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(save_servers.call_args.args[0][0]["language"], "python")

    def test_memory_prune_covers_semantic_and_stale_volatile_memory(self) -> None:
        with (
            patch(
                "app.application.memory.facade.prune",
                return_value={"ok": True, "candidates": 2, "pruned": 2},
            ) as semantic,
            patch(
                "app.application.memory.facade.prune_volatile_facts",
                return_value={"ok": True, "candidates": 1, "pruned": 1},
            ) as volatile,
        ):
            result = tool_runtime_control(
                ROOT,
                operation="memory_prune",
                config={"max_age_days": 30, "volatile_max_age_days": 7, "dry_run": False},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["candidates"], 3)
        self.assertEqual(result["result"]["pruned"], 3)
        semantic.assert_called_once_with(max_age_days=30, max_importance=3, dry_run=False)
        volatile.assert_called_once_with(max_age_days=7, dry_run=False, profile=None)

    def test_memory_recall_normalizes_a_project_path_to_its_corpus_scope(self) -> None:
        with patch(
            "app.application.memory.facade.recall",
            return_value={"ok": True, "context": "project match"},
        ) as recall:
            result = tool_runtime_control(
                ROOT,
                operation="memory_recall",
                query="PROJECT_CORPUS_CANARY",
                config={"project": str(ROOT)},
            )

        self.assertTrue(result["ok"])
        recall.assert_called_once_with(
            "PROJECT_CORPUS_CANARY",
            profile=None,
            project=project_scope_id(ROOT),
            fact_limit=5,
            semantic_limit=3,
            max_chars=2000,
        )

    def test_project_index_uses_the_connected_project_by_default(self) -> None:
        with patch(
            "app.application.code_agent.indexing.index_project",
            return_value={"ok": True, "files_indexed": 2, "complete": True},
        ) as index_project:
            result = tool_runtime_control(
                ROOT,
                operation="project_index",
                config={"patterns": ["**/*.py"], "replace": False},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        index_project.assert_called_once_with(
            ROOT.resolve(),
            patterns=["**/*.py"],
            replace=False,
        )

    def test_project_status_accepts_a_relative_root_inside_the_connected_project(self) -> None:
        with patch(
            "app.application.code_agent.indexing.project_corpus_status",
            return_value={"ok": True, "files": 1, "chunks": 2},
        ) as project_status:
            result = tool_runtime_control(
                ROOT,
                operation="project_status",
                root_path="backend",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["chunks"], 2)
        project_status.assert_called_once_with((ROOT / "backend").resolve())

    def test_library_read_exposes_a_bounded_page(self) -> None:
        with patch(
            "app.application.library.runtime.read_library_file",
            return_value={
                "ok": True,
                "file_id": 7,
                "text": "страница",
                "has_more": True,
                "next_offset": 8000,
            },
        ) as read_file:
            result = tool_runtime_control(
                ROOT,
                operation="library_read",
                config={"file_id": 7, "offset": 0, "limit": 8000},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["next_offset"], 8000)
        read_file.assert_called_once_with(7, offset=0, limit=8000)

    def test_library_import_uses_an_attached_resource_id(self) -> None:
        with patch(
            "app.application.library.runtime.import_resource",
            return_value={"ok": True, "id": 8, "name": "report.pdf"},
        ) as import_resource:
            result = tool_runtime_control(
                ROOT,
                operation="library_import",
                config={"resource_id": "a" * 32, "active": True},
            )

        self.assertTrue(result["ok"])
        import_resource.assert_called_once_with("a" * 32, use_in_context=True)

    def test_library_add_accepts_a_directory_when_it_contains_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            source_file = source_dir / "library_canary.txt"
            source_file.write_text("LIBRARY_CANARY", encoding="utf-8")
            with patch(
                "app.application.library.runtime.add_file_contents",
                return_value={"ok": True, "id": 9, "name": source_file.name},
            ) as add_file:
                result = tool_runtime_control(
                    ROOT,
                    operation="library_add",
                    path=str(source_dir),
                )

        self.assertTrue(result["ok"])
        add_file.assert_called_once_with(
            filename="library_canary.txt",
            contents=b"LIBRARY_CANARY",
            content_type=None,
            use_in_context=True,
            source="runtime_control",
        )

    def test_library_add_accepts_a_full_path_in_filename_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "library_canary.txt"
            source_file.write_text("LIBRARY_CANARY", encoding="utf-8")
            with patch(
                "app.application.library.runtime.add_file_contents",
                return_value={"ok": True, "id": 10, "name": source_file.name},
            ) as add_file:
                result = tool_runtime_control(
                    ROOT,
                    operation="library_add",
                    filename=str(source_file),
                )

        self.assertTrue(result["ok"])
        add_file.assert_called_once_with(
            filename="library_canary.txt",
            contents=b"LIBRARY_CANARY",
            content_type=None,
            use_in_context=True,
            source="runtime_control",
        )

    def test_workflow_template_and_trigger_share_workflow_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            previous = workflow_db_path.get_workflow_db_path()
            workflow_db_path.set_workflow_db_path(Path(tmpdir) / "workflow.db")
            permission_token = set_permission_mode("bypass")
            try:
                created = tool_runtime_control(
                    ROOT,
                    operation="workflow_upsert",
                    workflow_id="test.runtime.workflow",
                    config={
                        "name": "Runtime workflow",
                        "graph": {
                            "entry_step": "input",
                            "steps": [
                                {
                                    "id": "input",
                                    "type": "request",
                                    "next": None,
                                    "config": {
                                        "kind": "input",
                                        "message": "Value",
                                        "schema": {},
                                    },
                                }
                            ],
                        },
                    },
                )
                trigger = tool_runtime_control(
                    ROOT,
                    operation="workflow_trigger_upsert",
                    workflow_id="test.runtime.workflow",
                    trigger_id="test.runtime.trigger",
                    config={"interval_minutes": 15, "permission_mode": "bypass"},
                )
                listed = tool_runtime_control(
                    ROOT,
                    operation="workflow_trigger_list",
                )
            finally:
                reset_permission_mode(permission_token)
                workflow_db_path.set_workflow_db_path(previous)

        self.assertEqual(created["status"], "completed")
        self.assertEqual(trigger["status"], "completed")
        self.assertEqual(trigger["result"]["trigger"]["permission_mode"], "bypass")
        self.assertEqual(listed["result"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
