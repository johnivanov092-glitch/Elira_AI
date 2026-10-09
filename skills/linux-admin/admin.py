"""Explicit scenarios, executed through the existing shell tool."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from cli import main

if __name__ == "__main__":
    raise SystemExit(main({'ssh': 'app.application.skill_services.ssh:execute_ssh', 'hosts': 'app.application.skill_services.ssh:tool_ssh_list_hosts', 'registry': 'app.application.skill_services.registry:tool_itops_registry', 'ssh_healthcheck': 'app.application.skill_services.itops:tool_itops_ssh_healthcheck', 'linux_inventory': 'app.application.skill_services.itops:tool_itops_linux_inventory', 'windows_inventory': 'app.application.skill_services.itops:tool_itops_windows_inventory', 'network_inventory': 'app.application.skill_services.itops:tool_itops_network_inventory', 'systemd_service_inspect': 'app.application.skill_services.itops:tool_itops_systemd_service_inspect', 'config_inspect': 'app.application.skill_services.itops:tool_itops_config_inspect', 'database_inspect': 'app.application.skill_services.itops:tool_itops_database_inspect', 'mikrotik_inventory': 'app.application.skill_services.mikrotik:tool_itops_mikrotik_inventory'}))
