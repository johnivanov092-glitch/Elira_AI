from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from app.application.it_ops import mikrotik_registry, mikrotik_runtime
from app.application.tool_providers import mcp_runtime
from app.infrastructure.it_ops import store


def test_typed_inventory_uses_fixed_read_only_ssh_command_and_records_evidence() -> None:
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp)
        previous_db = store._DB_PATH_OVERRIDE
        previous_mcp = mcp_runtime.CONFIG_PATH
        store._DB_PATH_OVERRIDE = str(data_dir / "it_ops.sqlite3")
        mcp_runtime.CONFIG_PATH = data_dir / "mcp_servers.json"
        try:
            mikrotik_registry.upsert_router(
                host="192.168.88.1",
                label="home",
                user="Elira",
                ros_version="6.49.19",
                verify_connection=False,
                registry_path=data_dir / "routers.yaml",
            )
            calls: list[dict[str, object]] = []

            def runner(**kwargs):
                calls.append(kwargs)
                return {
                    "ok": True,
                    "text": "version: 6.49.19\nname: ether1\n",
                    "exit_code": 0,
                }

            with patch(
                "app.application.code_agent.tools.get_current_run_id",
                return_value="mikrotik-ssh-test",
            ):
                result = mikrotik_runtime.tool_itops_mikrotik_inventory(
                    router_id="home",
                    runner=runner,
                )

            assert result["ok"] is True
            assert result["transport"] == "ssh"
            assert calls[0]["host"] == "Elira@192.168.88.1"
            command = str(calls[0]["command"])
            assert command == mikrotik_runtime.READ_ONLY_INVENTORY_COMMAND
            assert " add " not in command
            assert " set " not in command
            assert result["evidence_persisted"] is True
        finally:
            mcp_runtime.stop_all_servers()
            store._DB_PATH_OVERRIDE = previous_db
            mcp_runtime.CONFIG_PATH = previous_mcp


def test_typed_inventory_requires_registered_ssh_router() -> None:
    with tempfile.TemporaryDirectory() as temp:
        previous_db = store._DB_PATH_OVERRIDE
        store._DB_PATH_OVERRIDE = str(Path(temp) / "it_ops.sqlite3")
        try:
            result = mikrotik_runtime.tool_itops_mikrotik_inventory(router_id="missing")
            assert result["ok"] is False
            assert result["error"] == "mikrotik_ssh_target_not_registered"
        finally:
            store._DB_PATH_OVERRIDE = previous_db
