"""Scoped Read-Only SSH — the itops diagnostic tool provider.

Exposes read-only diagnostic adapters that run a FIXED command set over OS OpenSSH
against the alias STORED on a saved, verified connection profile:
  * ``itops_ssh_healthcheck(profile_id)`` — hostname; uname -a; uptime.
  * ``itops_linux_inventory(profile_id)`` — a curated Linux inventory set.
The model supplies only a profile_id — never a host/alias — and the executor's
operation-scope gate has already pinned that profile_id to the run's read-only
scope (bound to exactly ONE of these tools) before dispatch.

Hard properties:
- No raw shell, no model-supplied host, no ssh_acl allowlist, no generic SSH
  provider. Each command is a fixed argv (no shell), so nothing is injectable.
- Every command's output is redacted + capped INDIVIDUALLY, then persisted as
  evidence ({profile/asset, command id, exit, timestamp}).
- The provider is hidden entirely when the ``itops`` flag is off (zero schemas,
  never dispatches), mirroring SshToolProvider's flag-gating.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any

from app.infrastructure.encoding import decode_console

logger = logging.getLogger(__name__)

# Fixed read-only diagnostic set. (command_id, argv) — argv is passed verbatim as
# the remote command; there is NO shell and NO model input in it.
_HEALTH_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("hostname", ["hostname"]),
    ("uname", ["uname", "-a"]),
    ("uptime", ["uptime"]),
)
_PER_CMD_CAP = 4000          # redacted stdout/stderr chars kept per command (evidence + reply)
_SSH = "ssh"
_SCANNER_VANTAGE = "elira-host:openssh"

# Linux read-only inventory (adapter #1). (command_id, argv, required). Each is a
# single fixed argv (no shell/pipe). `required=False` commands may be reported
# `unsupported` ONLY on the exact systemd-absent signal; any other non-zero is an
# error. The 12K total cap below applies to BOTH the reply AND the stored evidence.
_INVENTORY_COMMANDS: tuple[tuple[str, list[str], bool], ...] = (
    ("hostname", ["hostname"], True),
    ("uname", ["uname", "-a"], True),
    ("os_release", ["cat", "/etc/os-release"], True),
    ("cpu", ["lscpu"], True),
    ("mem", ["free", "-h"], True),
    ("disk", ["df", "-h"], True),
    ("net", ["ip", "-brief", "address"], True),
    ("uptime", ["uptime"], True),
    ("block", ["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINTS"], True),
    ("failed_units", ["systemctl", "--failed", "--no-pager", "--no-legend"], False),
)
_INVENTORY_TOTAL_CAP = 12000    # shared budget across all commands' output (reply AND evidence)
_INVENTORY_PER_CMD_CAP = 2000
_INVENTORY_CMD_TIMEOUT = 10     # ≤10s per command


def _ssh_argv(alias: str, remote: list[str]) -> list[str]:
    return [_SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes", alias, *remote]


def _is_systemd_absent(code: int | None, err_text: str) -> bool:
    """True ONLY on the exact expected sign that systemd is absent: the binary is
    missing (exit 127 / 'command not found') or systemd is not PID 1 ('has not been
    booted with systemd'). Any OTHER non-zero is a real error, not 'unsupported'."""
    low = (err_text or "").lower()
    return code == 127 or "command not found" in low or "has not been booted with systemd" in low


_TRUNC = "\n[truncated]"


def _clean(raw: bytes, cap: int) -> str:
    """Decode console bytes, redact any secret-shaped content, cap length. The result
    is ALWAYS <= cap (the truncation marker is counted), so a shared budget summed
    across commands stays within its total."""
    from app.core.redaction import redact_secrets
    text = decode_console(raw).strip()
    text = str(redact_secrets(text))
    if len(text) <= cap:
        return text
    if cap <= len(_TRUNC):
        return text[:cap]
    return text[:cap - len(_TRUNC)] + _TRUNC


def tool_itops_ssh_healthcheck(profile_id: str = "", **_ignored: Any) -> dict[str, Any]:
    """Run the fixed read-only health check on the profile's stored alias.

    profile_id is the value the executor scope gate re-pinned to the run's bound
    scope. run_id is taken from the RUNTIME CONTEXT (the executor bound it from
    ToolExecutionRequest.run_id) — NEVER from model-supplied args, which are ignored.
    Returns {ok, text, results:[...]} and writes one evidence row per command.
    """
    from app.application.code_agent.tools import get_current_run_id
    from app.application.it_ops import ssh_enroll
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()   # authoritative — from runtime context, not args
    pid = str(profile_id or "").strip()
    if not pid:
        return {"ok": False, "text": "ERROR: no profile_id", "error": "no_profile_id"}
    try:
        store.init_db()
        prof = store.get_connection_profile(pid)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": f"ERROR: store unavailable: {exc}", "error": "store_unavailable"}
    if not prof or prof.get("transport") != "ssh":
        return {"ok": False, "text": "ERROR: unknown ssh profile", "error": "unknown_profile"}
    alias = str(prof.get("ssh_alias") or "").strip()
    if not ssh_enroll.alias_ok(alias):
        return {"ok": False, "text": "ERROR: stored alias is not a valid token", "error": "bad_alias"}
    asset_id = str(prof.get("asset_id") or "")
    target_identity = f"{asset_id}/{pid}"

    results: list[dict[str, Any]] = []
    lines: list[str] = [f"Read-only health check — {alias} (profile {pid}):"]
    evidence_ok = True
    for cmd_id, remote in _HEALTH_COMMANDS:
        try:
            proc = subprocess.run(_ssh_argv(alias, remote), capture_output=True, timeout=15)
            code = proc.returncode
            out = _clean(proc.stdout, _PER_CMD_CAP)
            err = _clean(proc.stderr, _PER_CMD_CAP)
        except subprocess.TimeoutExpired:
            code, out, err = None, "", "connection timed out"
        except (OSError, subprocess.SubprocessError) as exc:
            code, out, err = None, "", f"ssh could not run: {exc}"
        entry = {"command_id": cmd_id, "command": " ".join(remote), "exit": code, "stdout": out}
        if err:
            entry["stderr"] = err
        # Persist evidence (already redacted+capped). AUDIT REQUIREMENT: a command
        # whose evidence did NOT persist is reported as failed, so the run can never
        # claim success for a remote command with no proof in the journal.
        try:
            store.record_evidence(
                run_id=run_id, target_identity=target_identity, scanner_vantage=_SCANNER_VANTAGE,
                operation=f"ssh_healthcheck:{cmd_id}",
                result={"alias": alias, "command": " ".join(remote), "stdout": out, "stderr": err},
                exit_status="" if code is None else str(code))
            entry["evidence_persisted"] = True
        except Exception:  # noqa: BLE001
            entry["evidence_persisted"] = False
            evidence_ok = False
            logger.warning("itops healthcheck: evidence write failed for %s/%s", pid, cmd_id)
        results.append(entry)
        head = out if code == 0 else (err or f"exit {code}")
        _note = "" if entry["evidence_persisted"] else "  [evidence NOT persisted]"
        lines.append(f"  $ {' '.join(remote)}  →  {'ok' if code == 0 else 'FAILED'}: "
                     f"{head.splitlines()[0] if head else ''}{_note}")

    cmds_ok = all(r["exit"] == 0 for r in results)
    ok = cmds_ok and evidence_ok
    out_dict: dict[str, Any] = {"ok": ok, "text": "\n".join(lines), "results": results, "profile_id": pid}
    if not evidence_ok:
        out_dict["error"] = "evidence_persist_failed"
        out_dict["text"] += ("\nПРЕДУПРЕЖДЕНИЕ: команды выполнены, но запись доказательства в "
                             "журнал не удалась — результат помечен как неуспешный.")
    return out_dict


def tool_itops_linux_inventory(profile_id: str = "", **_ignored: Any) -> dict[str, Any]:
    """Linux read-only inventory on the profile's stored alias (adapter #1).

    Runs a fixed set of read-only commands (≤10s each). The 12K TOTAL cap is a shared
    budget across all commands, applied to BOTH the reply AND the persisted evidence.
    Requires a `linux` asset (defense-in-depth; the route also checks). Any command
    failure (non-zero that is NOT the exact systemd-absent signal) makes the whole
    inventory ok=false; a failed evidence write does too. run_id/profile_id are
    authoritative (context / scope-repinned), never model-trusted.
    """
    from app.application.code_agent.tools import get_current_run_id
    from app.application.it_ops import ssh_enroll
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    pid = str(profile_id or "").strip()
    if not pid:
        return {"ok": False, "text": "ERROR: no profile_id", "error": "no_profile_id"}
    try:
        store.init_db()
        prof = store.get_connection_profile(pid)
        asset = store.get_asset(str((prof or {}).get("asset_id") or "")) if prof else None
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": f"ERROR: store unavailable: {exc}", "error": "store_unavailable"}
    if not prof or prof.get("transport") != "ssh":
        return {"ok": False, "text": "ERROR: unknown ssh profile", "error": "unknown_profile"}
    if not asset or asset.get("kind") != "linux":
        return {"ok": False, "text": "ERROR: linux inventory requires a linux asset",
                "error": "not_linux_asset"}
    alias = str(prof.get("ssh_alias") or "").strip()
    if not ssh_enroll.alias_ok(alias):
        return {"ok": False, "text": "ERROR: stored alias is not a valid token", "error": "bad_alias"}
    target_identity = f"{asset.get('asset_id')}/{pid}"

    budget = _INVENTORY_TOTAL_CAP     # shared across commands: bounds reply AND evidence
    results: list[dict[str, Any]] = []
    lines: list[str] = [f"Linux inventory — {alias} (profile {pid}):"]
    cmds_ok = True
    evidence_ok = True
    for cmd_id, remote, required in _INVENTORY_COMMANDS:
        try:
            proc = subprocess.run(_ssh_argv(alias, remote), capture_output=True,
                                  timeout=_INVENTORY_CMD_TIMEOUT)
            code, raw_out, raw_err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            code, raw_out, raw_err = None, b"", b"connection timed out"
        except (OSError, subprocess.SubprocessError) as exc:
            code, raw_out, raw_err = None, b"", f"ssh could not run: {exc}".encode()
        # Cap to the shared remaining budget (applies to BOTH evidence and reply).
        out = _clean(raw_out, max(0, min(_INVENTORY_PER_CMD_CAP, budget)))
        err = _clean(raw_err, max(0, min(_INVENTORY_PER_CMD_CAP, budget - len(out))))
        budget -= (len(out) + len(err))
        # status: unsupported ONLY for an optional command on the exact systemd-absent
        # signal; any other non-zero is a failure that fails the whole inventory.
        if code == 0:
            status = "ok"
        elif not required and _is_systemd_absent(code, err):
            status = "unsupported"
        else:
            status = "failed"
            cmds_ok = False
        entry = {"command_id": cmd_id, "command": " ".join(remote), "exit": code, "status": status,
                 "stdout": out}
        if err:
            entry["stderr"] = err
        try:
            store.record_evidence(
                run_id=run_id, target_identity=target_identity, scanner_vantage=_SCANNER_VANTAGE,
                operation=f"linux_inventory:{cmd_id}",
                result={"alias": alias, "command": " ".join(remote), "status": status,
                        "stdout": out, "stderr": err},
                exit_status="" if code is None else str(code))
            entry["evidence_persisted"] = True
        except Exception:  # noqa: BLE001
            entry["evidence_persisted"] = False
            evidence_ok = False
            logger.warning("itops linux_inventory: evidence write failed for %s/%s", pid, cmd_id)
        results.append(entry)
        _note = "" if entry["evidence_persisted"] else "  [evidence NOT persisted]"
        head = out if status == "ok" else (err or status)
        lines.append(f"  $ {' '.join(remote)}  →  {status}: {head.splitlines()[0] if head else ''}{_note}")

    ok = cmds_ok and evidence_ok
    text = "\n".join(lines)
    if len(text) > _INVENTORY_TOTAL_CAP:      # total reply cap
        text = text[:_INVENTORY_TOTAL_CAP] + "\n[truncated]"
    out_dict: dict[str, Any] = {"ok": ok, "text": text, "results": results, "profile_id": pid}
    if not cmds_ok:
        out_dict["error"] = "inventory_command_failed"
    elif not evidence_ok:
        out_dict["error"] = "evidence_persist_failed"
    return out_dict


_DISPATCH = {
    "itops_ssh_healthcheck": tool_itops_ssh_healthcheck,
    "itops_linux_inventory": tool_itops_linux_inventory,
}


class ItopsToolProvider:
    """ToolProvider for the scoped read-only SSH diagnostic. Hidden (zero schemas,
    never dispatches) when the itops flag is off — the executor scope gate is the
    real authorization, this only controls visibility."""

    name = "itops"

    def is_enabled(self) -> bool:
        try:
            from app.application.feature_flags import flag_enabled
            return flag_enabled("itops")
        except Exception:  # noqa: BLE001
            return False

    def get_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "itops_ssh_healthcheck",
                    "description": (
                        "Read-only SSH diagnostic on a SAVED, verified connection profile: runs a "
                        "fixed set of harmless commands (hostname, uname -a, uptime) and returns "
                        "their output. Takes a profile_id (NOT a host/alias). Only runnable inside "
                        "a bound read-only diagnostic run; one call per run."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "profile_id": {
                                "type": "string",
                                "description": "The saved connection profile id to diagnose.",
                            },
                        },
                        "required": ["profile_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_linux_inventory",
                    "description": (
                        "Read-only Linux inventory on a SAVED, verified LINUX profile: runs a fixed "
                        "set of harmless commands (hostname, uname, /etc/os-release, lscpu, free, df, "
                        "ip addr, uptime, lsblk, failed systemd units) and returns their output. "
                        "Takes a profile_id (NOT a host/alias). Only runnable inside a bound read-only "
                        "diagnostic run for a linux asset; one call per run."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "profile_id": {
                                "type": "string",
                                "description": "The saved linux connection profile id to inventory.",
                            },
                        },
                        "required": ["profile_id"],
                    },
                },
            },
        ]

    def owns(self, tool_name: str) -> bool:
        return tool_name in _DISPATCH

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = _DISPATCH.get(tool_name)
        if handler is None:
            return {"ok": False, "text": f"ERROR: unknown itops tool '{tool_name}'"}
        try:
            return handler(**args)
        except TypeError as exc:
            return {"ok": False, "text": f"ERROR: bad arguments to {tool_name}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.exception("itops tool %s crashed", tool_name)
            return {"ok": False, "text": f"ERROR: {exc}"}
