from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.application.code_agent.tools._runtime_control import tool_runtime_control
from app.application.it_ops import mikrotik_registry
from app.application.tool_providers import mcp_runtime
from app.infrastructure.it_ops import store


ROOT = Path(__file__).resolve().parents[2]


class MikrotikSshRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tempdir.name)
        self.previous_db = store._DB_PATH_OVERRIDE
        self.previous_mcp_config = mcp_runtime.CONFIG_PATH
        store._DB_PATH_OVERRIDE = str(self.data_dir / "it_ops.sqlite3")
        mcp_runtime.CONFIG_PATH = self.data_dir / "mcp_servers.json"
        self.registry_path = self.data_dir / "mikromcp" / "routers.yaml"

    def tearDown(self) -> None:
        mcp_runtime.stop_all_servers()
        store._DB_PATH_OVERRIDE = self.previous_db
        mcp_runtime.CONFIG_PATH = self.previous_mcp_config
        self.tempdir.cleanup()

    def test_upsert_persists_ssh_router_and_removes_legacy_mcp_artifacts(self) -> None:
        self.registry_path.parent.mkdir(parents=True)
        self.registry_path.write_text("routers: {}\n", encoding="utf-8")
        mcp_runtime.save_servers([{
            "id": "mikrotik",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "mikromcp", "serve"],
            "enabled": True,
        }])

        saved = mikrotik_registry.upsert_router(
            host="192.168.88.1",
            label="Домашний роутер",
            user="Elira",
            auth_ref="sref_legacy_password",
            ros_version="6.49.19",
            verify_connection=False,
            registry_path=self.registry_path,
        )

        self.assertEqual(saved["router"]["transport"], "ssh")
        self.assertEqual(saved["router"]["ros_version"], "6.49.19")
        self.assertTrue(saved["router"]["has_legacy_secret_ref"])
        profiles = store.list_connection_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["transport"], "ssh")
        self.assertEqual(profiles[0]["auth_ref"], "sref_legacy_password")
        self.assertFalse(any(item["id"] == "mikrotik" for item in mcp_runtime.list_servers()))
        self.assertFalse(self.registry_path.exists())

    def test_multiple_ssh_routers_survive_resync_and_remove(self) -> None:
        first = mikrotik_registry.upsert_router(
            host="192.168.88.1",
            label="home",
            user="operator",
            ros_version="6.49.19",
            verify_connection=False,
            registry_path=self.registry_path,
        )["router"]
        second = mikrotik_registry.upsert_router(
            host="10.20.30.1",
            label="branch",
            user="operator",
            ros_version="7.16.2",
            verify_connection=False,
            registry_path=self.registry_path,
        )["router"]

        self.assertEqual(
            {item["router_id"] for item in mikrotik_registry.list_routers()},
            {first["router_id"], second["router_id"]},
        )
        removed = mikrotik_registry.remove_router(
            router_id=first["router_id"],
            registry_path=self.registry_path,
        )
        self.assertTrue(removed["deleted"])
        self.assertEqual(
            [item["router_id"] for item in mikrotik_registry.list_routers()],
            [second["router_id"]],
        )

    def test_workflow_auto_discovers_routeros_version_over_typed_ssh(self) -> None:
        with (
            patch.object(
                mikrotik_registry,
                "discover_routeros_version",
                return_value={"ok": True, "ros_version": "6.49.19"},
            ) as discover,
            patch.object(
                mikrotik_registry,
                "default_registry_path",
                return_value=self.registry_path,
            ),
        ):
            result = tool_runtime_control(
                ROOT,
                operation="itops_mikrotik_upsert",
                config={"host": "192.168.88.1", "label": "home", "user": "Elira"},
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["router"]["ros_version"], "6.49.19")
        self.assertEqual(result["result"]["router"]["transport"], "ssh")
        discover.assert_called_once()

    def test_routeros_6_uses_ssh_without_mcp_or_secret_requirement(self) -> None:
        with patch.object(
            mikrotik_registry,
            "discover_routeros_version",
            return_value={"ok": True, "ros_version": "6.49.19"},
        ):
            result = tool_runtime_control(
                ROOT,
                operation="itops_mikrotik_upsert",
                config={
                    "host": "192.168.88.1",
                    "label": "home",
                    "user": "Elira",
                    "ros_version": "6.49.19",
                },
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["router"]["transport"], "ssh")
        self.assertFalse(any(item["id"] == "mikrotik" for item in mcp_runtime.list_servers()))
        compatibility = mikrotik_registry.ssh_compatibility_args("Elira@192.168.88.1")
        self.assertIn("MACs=+hmac-sha1", compatibility)

    def test_routeros_7_does_not_weaken_openssh_algorithms(self) -> None:
        mikrotik_registry.upsert_router(
            host="192.168.88.2",
            label="modern",
            user="Elira",
            ros_version="7.16.2",
            verify_connection=False,
            registry_path=self.registry_path,
        )

        self.assertEqual(
            mikrotik_registry.ssh_compatibility_args("Elira@192.168.88.2"),
            [],
        )

    def test_registered_identity_file_is_added_without_exposing_its_path(self) -> None:
        identity = self.data_dir / "router-key"
        identity.write_text("test key placeholder", encoding="utf-8")
        saved = mikrotik_registry.upsert_router(
            host="192.168.88.3",
            label="keyed",
            user="Elira",
            identity_file=str(identity),
            ros_version="7.16.2",
            verify_connection=False,
            registry_path=self.registry_path,
        )["router"]

        args = mikrotik_registry.ssh_compatibility_args("Elira@192.168.88.3")
        self.assertIn("-i", args)
        self.assertIn(str(identity.resolve()), args)
        self.assertTrue(saved["has_identity_file"])
        self.assertNotIn("identity_file", saved)

    def test_plaintext_password_is_rejected_without_echo_or_persistence(self) -> None:
        result = tool_runtime_control(
            ROOT,
            operation="itops_mikrotik_upsert",
            config={
                "host": "192.168.88.1",
                "user": "Elira",
                "password": "do-not-persist-this",
                "ros_version": "6.49.19",
            },
        )

        self.assertEqual(result["status"], "failed")
        self.assertNotIn("do-not-persist-this", result["text"])
        self.assertEqual(store.list_assets(), [])

    def test_legacy_mcp_profile_migrates_in_place_to_ssh_and_keeps_secret_ref(self) -> None:
        store.init_db()
        store.upsert_asset(
            asset_id="mikrotik:home",
            label="home",
            kind="network_device",
            endpoint="https://192.168.88.1:8729",
            tags=["mikrotik"],
            owner_scope="global",
            lifecycle_state="enabled",
        )
        store.put_connection_profile(
            profile_id="mikrotik:home:mcp",
            asset_id="mikrotik:home",
            transport="mcp",
            user="Elira",
            auth_ref="sref_saved_once",
            os_platform_meta={
                "vendor": "mikrotik",
                "runtime": "mikromcp",
                "router_id": "home",
                "host": "192.168.88.1",
                "ros_version": "7.0",
            },
        )

        migrated = mikrotik_registry.upsert_router(
            host="192.168.88.1",
            label="home",
            user="Elira",
            ros_version="6.49.19",
            verify_connection=False,
            registry_path=self.registry_path,
        )

        self.assertEqual(migrated["router"]["transport"], "ssh")
        profiles = store.list_connection_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["profile_id"], "mikrotik:home:ssh")
        self.assertEqual(profiles[0]["auth_ref"], "sref_saved_once")


if __name__ == "__main__":
    unittest.main()
