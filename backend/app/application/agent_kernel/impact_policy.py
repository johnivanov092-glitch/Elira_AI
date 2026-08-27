"""Impact classification for the single Workflow permission selector.

This module never blocks execution. It only decides whether ``accept_edits``
requires a Workflow UI request; ``bypass`` always returns ``auto``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


AUTO = "auto"
ASK = "ask"
_MODES = frozenset({"ask", "accept_edits", "bypass"})
_CHANNELS = frozenset({"local", "remote"})
_IMPACTS = frozenset({"low", "material", "high", "unknown"})
_REVERSIBILITY = frozenset({
    "none", "unknown", "staged_rollback", "transactional", "verified_backup",
})


@dataclass(frozen=True)
class SafetyEvidence:
    impact: str = "unknown"
    reversibility: str = "unknown"
    authoritative: bool = False
    postcheck: bool = False
    backup_verified: bool = False
    restore_verified: bool = False
    rollback_verified: bool = False

    @classmethod
    def low_risk_reversible(cls) -> "SafetyEvidence":
        return cls(
            impact="low",
            reversibility="staged_rollback",
            authoritative=True,
            postcheck=True,
            rollback_verified=True,
        )


def decide_approval(
    permission_mode: str,
    channel: str,
    evidence: SafetyEvidence,
    *,
    is_change: bool = True,
) -> str:
    """Return ``auto`` or ``ask`` for the Workflow permission selector."""
    mode = str(permission_mode or "").strip().lower()
    source = str(channel or "").strip().lower()
    if mode not in _MODES or source not in _CHANNELS:
        return ASK
    if not is_change:
        return AUTO
    if mode == "bypass":
        return AUTO
    if source == "remote":
        return ASK
    if mode == "ask":
        return ASK
    if evidence.impact not in _IMPACTS or evidence.reversibility not in _REVERSIBILITY:
        return ASK
    if mode == "accept_edits":
        # Accept Edits means ordinary local mutations run without interruption.
        # Only explicitly high/unknown-impact calls still ask in Workflow UI.
        return ASK if evidence.impact in {"high", "unknown"} else AUTO
    return AUTO


_HIGH_IMPACT_PREFIXES = (
    # Destructive filesystem / VCS / containers / process / packages.
    "rm ", "del ", "erase ", "rmdir", "remove-item",
    "git reset", "git clean", "git checkout --", "git push --force",
    "git push -f", "git push --f", "git branch -d", "git tag -d",
    "git stash drop", "git stash clear",
    "docker rm", "docker rmi", "docker volume rm", "docker network rm",
    "docker stop", "docker restart", "docker system prune", "docker image prune",
    "docker container prune",
    "docker compose down", "docker-compose down",
    "kill ", "pkill", "killall", "taskkill",
    "pip uninstall", "npm uninstall", "npm unpublish", "apt remove", "apt purge",
    "apt-get remove", "apt-get purge", "dnf remove", "yum remove", "zypper remove",
    "pacman -r", "rpm -e",
    "chmod -r", "chown -r",
    # Host power, identities, storage, and orchestration.
    "shutdown", "reboot", "halt", "poweroff", "init 0", "init 6",
    "systemctl reboot", "systemctl poweroff", "restart-computer", "stop-computer",
    "mkfs", "wipefs", "fdisk", "parted", "diskpart", "format-volume",
    "clear-disk", "initialize-disk", "remove-partition", "dd ",
    "userdel", "groupdel", "deluser", "delgroup", "remove-localuser",
    "sc delete", "remove-service", "reg delete",
    "kubectl delete", "kubectl drain", "kubectl scale", "helm uninstall", "helm delete",
    # Host availability, network reachability, and access control.
    "systemctl restart", "systemctl stop", "systemctl disable", "service ",
    "iptables", "ip6tables", "nft ", "ufw ", "firewall-cmd",
    "netsh advfirewall", "route add", "route delete", "route change",
    "ip route add", "ip route del", "ip route delete", "ip route replace",
    "ip route flush", "nmcli connection down", "nmcli connection delete",
    "new-netfirewallrule", "set-netfirewallrule", "remove-netfirewallrule",
    "new-netroute", "set-netroute", "remove-netroute",
    "new-netipaddress", "set-netipaddress", "remove-netipaddress",
)
_HIGH_IMPACT_SUBSTRINGS = (
    "drop table", "drop database", "truncate table", "delete from",
    "manage.py migrate", "alembic upgrade", "alembic downgrade",
    "prisma migrate", "sequelize db:migrate", "typeorm migration",
    "flyway migrate", "liquibase update",
    "/etc/ssh/", "sshd_config", "/etc/sudoers", "/etc/netplan/",
    "/etc/network/interfaces", "authorized_keys",
)
_HIGH_IMPACT_WRAPPERS = (
    "bash -c", "sh -c", "zsh -c", "cmd /c", "powershell ", "powershell.exe ",
    "pwsh ", "pwsh.exe ", "eval ", "invoke-expression",
)


def shell_command_is_high_impact(command: str) -> bool:
    """Conservative command classifier used only for the approval tier."""
    cmd = str(command or "").strip().lower()
    if not cmd:
        return False
    if any(fragment in cmd for fragment in _HIGH_IMPACT_SUBSTRINGS):
        return True
    for segment in re.split(r"&&|\|\||;|\||\n|\r|`|\$\(", cmd):
        normalized = segment.strip()
        # sudo is itself the privilege boundary. Do not try to fully parse its options
        # or a nested shell command; an opaque privileged call must fail closed to ask.
        if normalized.startswith("sudo "):
            return True
        if any(normalized.startswith(prefix) for prefix in _HIGH_IMPACT_WRAPPERS):
            return True
        if any(normalized.startswith(prefix) for prefix in _HIGH_IMPACT_PREFIXES):
            return True
    return False


_LOW_RISK_REVERSIBLE_TOOLS = frozenset({
    "write_file", "edit_file", "file_gen", "converter", "archiver",
    "sandbox_reset", "run_server", "git_commit",
    "resource_materialize", "resource_publish",
})
_REMOTE_WRITE_TOOLS = frozenset({"ssh_write", "ssh_replace"})
_REMOTE_COMMAND_TOOLS = frozenset({"ssh_run", "ssh_run_ps"})

_BLENDER_READ_ONLY_TOOLS = frozenset({
    "get_blender_status",
    "get_scene_info",
    "get_object_info",
    "get_viewport_screenshot",
    "get_polyhaven_categories",
    "search_polyhaven_assets",
    "get_polyhaven_status",
    "get_hyper3d_status",
    "get_sketchfab_status",
    "search_sketchfab_models",
    "get_sketchfab_model_preview",
    "poll_rodin_job_status",
    "get_hunyuan3d_status",
    "poll_hunyuan_job_status",
})
_UNITY_READ_ONLY_TOOLS = frozenset({
    "debug_request_context",
    "find_gameobjects",
    "find_in_file",
    "validate_script",
    "manage_script_capabilities",
    "get_sha",
    "read_console",
    "get_test_job",
    "unity_docs",
    "unity_reflect",
})
_UNITY_CAMERA_READ_ACTIONS = frozenset({
    "ping", "get_brain_status", "list_cameras", "screenshot", "screenshot_multiview",
})


def _creative_mcp_parts(tool_name: str) -> tuple[str, str] | None:
    name = str(tool_name or "").strip()
    if "__" not in name:
        return None
    server, original = name.split("__", 1)
    if server not in {"blender", "unity"}:
        return None
    return server, original


def creative_batch_contains_arbitrary_code(value: Any) -> bool:
    """Detect an arbitrary-code tool hidden anywhere in model-supplied batch JSON."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in {"tool", "name", "tool_name"} and str(item).casefold() in {
                "execute_code", "execute_blender_code",
            }:
                return True
            if creative_batch_contains_arbitrary_code(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(creative_batch_contains_arbitrary_code(item) for item in value)
    return False


def tool_call_is_change(tool_name: str, args: dict[str, Any] | None) -> bool:
    """Call-level read/change classification for mixed creative MCP surfaces.

    Only the two explicitly configured editor bridges receive this treatment.
    Unknown MCP tools remain changes and therefore fail closed to approval.
    """
    payload = args if isinstance(args, dict) else {}
    if str(tool_name or "").strip() == "capability_load":
        return False
    if str(tool_name or "").strip() == "runtime_control":
        return str(payload.get("operation") or "").strip().lower() not in {
            "status", "mcp_list", "lsp_list", "telegram_status",
            "telegram_test", "telegram_users", "itops_assets", "vault_status",
            "plugin_list", "plugin_info", "ssh_hosts",
            "workflow_list", "workflow_runs", "workflow_trigger_list",
            "workflow_scheduler_status", "memory_stats", "memory_profiles",
            "memory_list", "memory_search", "memory_recall", "library_list",
            "library_search", "library_context", "project_status",
        }
    parts = _creative_mcp_parts(tool_name)
    if parts is None:
        return True
    server, original = parts
    if server == "blender":
        return original not in _BLENDER_READ_ONLY_TOOLS
    if original in _UNITY_READ_ONLY_TOOLS:
        return False
    if original == "execute_code" and str(payload.get("action") or "").strip().casefold() == "get_history":
        return False
    if original == "manage_camera":
        return str(payload.get("action") or "").strip().casefold() not in _UNITY_CAMERA_READ_ACTIONS
    return True


def evidence_for_registered_target(target_id: str) -> SafetyEvidence:
    """Return the reviewed safety profile for an executor-owned target id.

    New registry targets default to high impact, so ``accept_edits`` asks. The
    explicit ``bypass`` mode remains blanket authorization.
    """
    if target_id == "ai-server-netdata":
        # Non-critical restart with executor-owned pre/post health checks.
        return SafetyEvidence(
            impact="material",
            reversibility="staged_rollback",
            authoritative=True,
            postcheck=True,
        )
    if target_id == "ai-server-netdata-config":
        return SafetyEvidence(
            impact="high",
            reversibility="staged_rollback",
            authoritative=True,
            postcheck=True,
            rollback_verified=True,
        )
    if target_id == "phase6-sqlite-canary":
        return SafetyEvidence(
            impact="high",
            reversibility="transactional",
            authoritative=True,
            postcheck=True,
        )
    return SafetyEvidence(impact="high")


def evidence_for_tool_call(tool_name: str, args: dict[str, Any] | None) -> SafetyEvidence:
    """Classify one tool call from trusted tool identity and command text only.

    Keys such as ``backup_verified`` in model-supplied arguments are deliberately ignored.
    Proof that permits high-risk auto-execution must come from a typed runtime adapter.
    """
    name = str(tool_name or "").strip()
    payload = args if isinstance(args, dict) else {}
    creative = _creative_mcp_parts(name)
    if creative is not None:
        server, original = creative
        if not tool_call_is_change(name, payload):
            return SafetyEvidence.low_risk_reversible()
        if (
            (server == "blender" and original == "execute_blender_code")
            or (
                server == "unity"
                and original == "execute_code"
                and str(payload.get("action") or "").strip().casefold() in {"execute", "replay"}
            )
            or (
                server == "unity"
                and original == "batch_execute"
                and creative_batch_contains_arbitrary_code(payload)
            )
        ):
            # Arbitrary editor code is intentionally not declared reversible here.
            # The provider takes a scene backup and captures a post-check, but Python/
            # C# can touch more than the scene, so accept_edits asks; bypass proceeds.
            return SafetyEvidence(impact="high")
        # Dedicated editor primitives are bounded to the open scene/project. Local
        # bypass may proceed; ask/accept_edits still pause under the shared policy.
        return SafetyEvidence(impact="material")
    if name in _LOW_RISK_REVERSIBLE_TOOLS:
        return SafetyEvidence.low_risk_reversible()
    if name == "itops_change_apply":
        return evidence_for_registered_target(str(payload.get("target_id") or "").strip())
    if name == "sql" or name in _REMOTE_WRITE_TOOLS:
        return SafetyEvidence(impact="high")
    if name == "resource_remote_process":
        # Bounded egress to an operator-configured worker: material, not a local
        # filesystem edit. accept_edits asks; local bypass may proceed.
        return SafetyEvidence(impact="material")
    if name == "run_bash":
        command = str(payload.get("command") or "")
        return SafetyEvidence(
            impact="high" if shell_command_is_high_impact(command) else (
                "material" if command.strip() else "unknown"
            )
        )
    if name in _REMOTE_COMMAND_TOOLS:
        command = str(payload.get("command") or payload.get("script") or "")
        return SafetyEvidence(
            impact="high" if shell_command_is_high_impact(command) else (
                "material" if command.strip() else "unknown"
            )
        )
    # Unknown side-effect tools ask in accept_edits; bypass is decided earlier.
    return SafetyEvidence()
