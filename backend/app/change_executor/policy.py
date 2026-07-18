"""Shared approval policy for local agent work and isolated IT changes.

The module is stdlib-only and does not import ``app.*`` so the frozen change-executor
package can use the same decision model as the main code-agent runtime.  Evidence is
trusted only when it is constructed by runtime code; arbitrary tool arguments are never
interpreted as backup, rollback, or post-check proof.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


AUTO = "auto"
ASK = "ask"
BLOCK = "block"

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


def _high_risk_auto_safe(evidence: SafetyEvidence) -> bool:
    if not evidence.authoritative or not evidence.postcheck:
        return False
    if evidence.reversibility == "transactional":
        return True
    if evidence.reversibility == "staged_rollback":
        return evidence.rollback_verified
    if evidence.reversibility == "verified_backup":
        return evidence.backup_verified and evidence.restore_verified
    return False


def decide_approval(
    permission_mode: str,
    channel: str,
    evidence: SafetyEvidence,
    *,
    is_change: bool = True,
) -> str:
    """Return ``auto`` or ``ask`` from mode + channel + runtime-owned evidence.

    Forbidden actions stay blocked by the existing executor/tool gates.  This policy
    controls only whether an otherwise allowed change may skip a human approval.
    Unknown input fails closed to ``ask``.
    """
    mode = str(permission_mode or "").strip().lower()
    source = str(channel or "").strip().lower()
    if mode not in _MODES or source not in _CHANNELS:
        return ASK
    if not is_change:
        return AUTO
    if source == "remote":
        return ASK
    if mode == "ask":
        return ASK
    if evidence.impact not in _IMPACTS or evidence.reversibility not in _REVERSIBILITY:
        return ASK
    if mode == "accept_edits":
        return AUTO if evidence.impact == "low" and _high_risk_auto_safe(evidence) else ASK
    if evidence.impact == "unknown":
        return ASK
    if evidence.impact == "high":
        return AUTO if _high_risk_auto_safe(evidence) else ASK
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


def evidence_for_registered_target(target_id: str) -> SafetyEvidence:
    """Return the reviewed safety profile for an executor-owned target id.

    New registry targets default to high-impact without recovery proof, so they cannot
    silently inherit local bypass. Remote Telegram approval remains available.
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
    # Unknown side-effect tools do not silently become bypass-safe when added later.
    return SafetyEvidence()
