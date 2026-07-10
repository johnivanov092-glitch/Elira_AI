"""Scoped Read-Only SSH v1 — the itops diagnostic tool provider.

Exposes exactly one tool, ``itops_ssh_healthcheck(profile_id)``, that runs a
FIXED read-only command set (hostname; uname -a; uptime) over OS OpenSSH against
the alias STORED on a saved, verified connection profile. The model supplies only
a profile_id — never a host/alias — and the executor's operation-scope gate has
already pinned that profile_id to the run's read-only scope before dispatch.

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


def _ssh_argv(alias: str, remote: list[str]) -> list[str]:
    return [_SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes", alias, *remote]


def _clean(raw: bytes, cap: int) -> str:
    """Decode console bytes, redact any secret-shaped content, cap length."""
    from app.core.redaction import redact_secrets
    text = decode_console(raw).strip()
    text = str(redact_secrets(text))
    return text if len(text) <= cap else text[:cap] + "\n[truncated]"


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
        results.append(entry)
        # Persist evidence (already redacted+capped); never let a write break the run.
        try:
            store.record_evidence(
                run_id=run_id, target_identity=target_identity, scanner_vantage=_SCANNER_VANTAGE,
                operation=f"ssh_healthcheck:{cmd_id}",
                result={"alias": alias, "command": " ".join(remote), "stdout": out, "stderr": err},
                exit_status="" if code is None else str(code))
        except Exception:  # noqa: BLE001
            logger.warning("itops healthcheck: evidence write failed for %s/%s", pid, cmd_id)
        head = out if code == 0 else (err or f"exit {code}")
        lines.append(f"  $ {' '.join(remote)}  →  {'ok' if code == 0 else 'FAILED'}: {head.splitlines()[0] if head else ''}")

    ok = all(r["exit"] == 0 for r in results)
    return {"ok": ok, "text": "\n".join(lines), "results": results, "profile_id": pid}


_DISPATCH = {"itops_ssh_healthcheck": tool_itops_ssh_healthcheck}


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
