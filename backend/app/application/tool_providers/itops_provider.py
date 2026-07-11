"""Scoped Read-Only SSH — the itops diagnostic tool provider.

Exposes read-only diagnostic adapters that run a FIXED command set over OS OpenSSH
against the alias STORED on a saved, verified connection profile:
  * ``itops_ssh_healthcheck(profile_id)`` — hostname; uname -a; uptime.
  * ``itops_linux_inventory(profile_id)`` — a curated Linux inventory set.
  * ``itops_windows_inventory(profile_id)`` — a curated Windows inventory set, run as
    STATIC PowerShell via -EncodedCommand (no quoting through cmd/sshd).
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

import base64
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

# Windows read-only inventory (adapter #2). Each command is a STATIC PowerShell
# script run via powershell.exe -EncodedCommand (UTF-16LE Base64) — NOT -Command and
# NO ExecutionPolicy Bypass — so nothing is quoted through cmd → sshd → PowerShell and
# nothing is model-supplied. (command_id, readable_label, static_script). The
# readable label (not the base64) is what lands in evidence.
_WINDOWS_COMMANDS: tuple[tuple[str, str], ...] = (
    ("os", "$o=Get-CimInstance Win32_OperatingSystem; \"$($o.Caption) | version $($o.Version) | build $($o.BuildNumber) | $($o.OSArchitecture)\""),
    ("hostname", "$env:COMPUTERNAME"),
    ("uptime", "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('u')"),
    ("disks", "Get-CimInstance Win32_LogicalDisk | ForEach-Object { '{0} {1}GB free of {2}GB' -f $_.DeviceID, [math]::Round($_.FreeSpace/1GB,1), [math]::Round($_.Size/1GB,1) }"),
    ("services", "Get-Service | Where-Object { $_.Status -eq 'Running' } | ForEach-Object { $_.Name + '  ' + $_.DisplayName }"),
    ("ipconfig", "Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object { $_.IPEnabled } | ForEach-Object { $_.Description + '  ' + ($_.IPAddress -join ', ') }"),
)
_WINDOWS_CMD_TIMEOUT = 15       # ≤15s per command (Windows cmdlet startup + CIM is slower)


def _ssh_argv(alias: str, remote: list[str]) -> list[str]:
    return [_SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes", alias, *remote]


def _ps_encode(script: str) -> str:
    """PowerShell -EncodedCommand payload: Base64 of the UTF-16LE bytes of a STATIC
    script. Sidesteps all cmd → sshd → PowerShell quoting."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _ps_wrap(script: str) -> str:
    """Make a PowerShell command FAIL on error. PowerShell's default
    $ErrorActionPreference is 'Continue', so a non-terminating error (Write-Error, a
    failing Get-CimInstance/WMI query, …) still exits 0 — which would let a real
    failure be recorded as a successful `ok` evidence row. Force Stop + try/catch so
    any error becomes a non-zero exit with the message on stderr."""
    return ("$ErrorActionPreference='Stop'; try { " + script +
            " } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }")


def _ps_argv(alias: str, script: str) -> list[str]:
    """SSH argv that runs a static PowerShell script (error-wrapped) via
    -EncodedCommand. No -Command, no ExecutionPolicy Bypass, no model input."""
    return _ssh_argv(alias, ["powershell.exe", "-NoProfile", "-NonInteractive",
                             "-EncodedCommand", _ps_encode(_ps_wrap(script))])


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
        asset = store.get_asset(str((prof or {}).get("asset_id") or "")) if prof else None
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": f"ERROR: store unavailable: {exc}", "error": "store_unavailable"}
    if not prof or prof.get("transport") != "ssh":
        return {"ok": False, "text": "ERROR: unknown ssh profile", "error": "unknown_profile"}
    # Defense in depth: the route + gate already require an ENABLED asset, but the handler
    # holds the same line so a scope bound another way can never run against a draft/unknown
    # asset (fail-closed BEFORE any SSH).
    if not asset or asset.get("lifecycle_state") != "enabled":
        return {"ok": False, "text": "ERROR: health check requires a verified/enabled asset",
                "error": "profile_not_enabled"}
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
    if asset.get("lifecycle_state") != "enabled":     # defense in depth (route/gate also require it)
        return {"ok": False, "text": "ERROR: linux inventory requires a verified/enabled asset",
                "error": "profile_not_enabled"}
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
    if len(text) > _INVENTORY_TOTAL_CAP:      # total reply cap (marker counted, so ≤ cap)
        text = text[:_INVENTORY_TOTAL_CAP - len(_TRUNC)] + _TRUNC
    out_dict: dict[str, Any] = {"ok": ok, "text": text, "results": results, "profile_id": pid}
    if not cmds_ok:
        out_dict["error"] = "inventory_command_failed"
    elif not evidence_ok:
        out_dict["error"] = "evidence_persist_failed"
    return out_dict


def tool_itops_windows_inventory(profile_id: str = "", **_ignored: Any) -> dict[str, Any]:
    """Windows read-only inventory on the profile's stored alias (adapter #2).

    Runs a fixed set of STATIC PowerShell scripts via -EncodedCommand (≤15s each).
    Requires a `windows` asset (defense-in-depth; the route also checks). The 12K
    TOTAL cap is a shared budget across all commands, applied to BOTH the reply AND
    the persisted evidence (which records the readable script, never the base64). Any
    command failure or a failed evidence write makes the whole inventory ok=false.
    run_id/profile_id are authoritative (context / scope-repinned), never model-trusted.
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
    if not asset or asset.get("kind") != "windows":
        return {"ok": False, "text": "ERROR: windows inventory requires a windows asset",
                "error": "not_windows_asset"}
    if asset.get("lifecycle_state") != "enabled":     # defense in depth (route/gate also require it)
        return {"ok": False, "text": "ERROR: windows inventory requires a verified/enabled asset",
                "error": "profile_not_enabled"}
    alias = str(prof.get("ssh_alias") or "").strip()
    if not ssh_enroll.alias_ok(alias):
        return {"ok": False, "text": "ERROR: stored alias is not a valid token", "error": "bad_alias"}
    target_identity = f"{asset.get('asset_id')}/{pid}"

    budget = _INVENTORY_TOTAL_CAP     # shared across commands: bounds reply AND evidence
    results: list[dict[str, Any]] = []
    lines: list[str] = [f"Windows inventory — {alias} (profile {pid}):"]
    cmds_ok = True
    evidence_ok = True
    for cmd_id, script in _WINDOWS_COMMANDS:
        try:
            proc = subprocess.run(_ps_argv(alias, script), capture_output=True,
                                  timeout=_WINDOWS_CMD_TIMEOUT)
            code, raw_out, raw_err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            code, raw_out, raw_err = None, b"", b"connection timed out"
        except (OSError, subprocess.SubprocessError) as exc:
            code, raw_out, raw_err = None, b"", f"ssh could not run: {exc}".encode()
        out = _clean(raw_out, max(0, min(_INVENTORY_PER_CMD_CAP, budget)))
        err = _clean(raw_err, max(0, min(_INVENTORY_PER_CMD_CAP, budget - len(out))))
        budget -= (len(out) + len(err))
        status = "ok" if code == 0 else "failed"
        if status == "failed":
            cmds_ok = False
        entry = {"command_id": cmd_id, "command": script, "exit": code, "status": status, "stdout": out}
        if err:
            entry["stderr"] = err
        try:
            store.record_evidence(
                run_id=run_id, target_identity=target_identity, scanner_vantage=_SCANNER_VANTAGE,
                operation=f"windows_inventory:{cmd_id}",
                # `command` is the READABLE script, never the base64 payload.
                result={"alias": alias, "command": script, "status": status,
                        "stdout": out, "stderr": err},
                exit_status="" if code is None else str(code))
            entry["evidence_persisted"] = True
        except Exception:  # noqa: BLE001
            entry["evidence_persisted"] = False
            evidence_ok = False
            logger.warning("itops windows_inventory: evidence write failed for %s/%s", pid, cmd_id)
        results.append(entry)
        _note = "" if entry["evidence_persisted"] else "  [evidence NOT persisted]"
        head = out if status == "ok" else (err or status)
        lines.append(f"  [{cmd_id}]  →  {status}: {head.splitlines()[0] if head else ''}{_note}")

    ok = cmds_ok and evidence_ok
    text = "\n".join(lines)
    if len(text) > _INVENTORY_TOTAL_CAP:
        text = text[:_INVENTORY_TOTAL_CAP - len(_TRUNC)] + _TRUNC
    out_dict: dict[str, Any] = {"ok": ok, "text": text, "results": results, "profile_id": pid}
    if not cmds_ok:
        out_dict["error"] = "inventory_command_failed"
    elif not evidence_ok:
        out_dict["error"] = "evidence_persist_failed"
    return out_dict


def tool_itops_network_inventory(**_ignored: Any) -> dict[str, Any]:
    """Read-only network inventory (Phase 3). Takes NO arguments — the CIDR and the
    server-owned port profile come ONLY from the run's bound network scope (the gate
    already refused any model arg). Does a bounded TCP-connect scan within hard caps,
    writes one evidence row per CONFIRMED OPEN host:port, and ALWAYS writes a
    server-owned summary in a finally (planned/attempted/completed/state counts, caps,
    stop_reason, status). complete → ok=true; partial/timed_out → ok=false
    scan_incomplete; a failed summary write OR a failed per-open write → ok=false
    evidence_persist_failed (no open may be reported without its own proof row). A
    killed process cannot write the summary — absence of a terminal summary is
    'unknown', never 'nothing found'.
    """
    from app.application.agent_kernel import operation_scope
    from app.application.code_agent.tools import get_current_run_id
    from app.application.it_ops import net_inventory as ni
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    scope = operation_scope.get_active_scope(run_id)
    if scope is None or scope.target_kind != "network" or scope.network is None:
        return {"ok": False, "text": "ERROR: no bound network scope", "error": "no_network_scope"}
    cidr = scope.network.cidr
    if scope.network.port_profile != ni.PROFILE_COMMON_V1.name:
        return {"ok": False, "text": "ERROR: unknown port profile", "error": "unknown_profile"}
    profile = ni.PROFILE_COMMON_V1
    try:
        hosts = ni.parse_cidr_v1(cidr)
    except ni.CidrError as exc:
        return {"ok": False, "text": f"ERROR: {exc.reason}", "error": exc.reason}
    # Defence-in-depth: the route authorizes the CIDR before binding the scope, but the
    # handler re-verifies against the SAME controls and fails closed — an unauthorized or
    # over-budget target must never scan just because a scope was created some other way.
    if not ni.cidr_authorized(cidr):
        return {"ok": False, "text": "ERROR: cidr not authorized", "error": "cidr_not_authorized"}
    try:
        ni.validate_profile(profile, len(hosts))
    except ni.CidrError as exc:
        return {"ok": False, "text": f"ERROR: {exc.reason}", "error": exc.reason}

    target_identity = f"net/{cidr}"
    result = None
    summary_ok = False
    opens_evidence_ok = True     # every open MUST leave its own durable proof row
    try:
        store.init_db()
        result = ni.run_scan(cidr, hosts, profile)
        for o in result.opens:
            try:
                store.record_evidence(
                    run_id=run_id, target_identity=target_identity, scanner_vantage=result.vantage,
                    operation="network_inventory:open",
                    result={"host": o["host"], "port": o["port"], "state": "open",
                            "vantage": result.vantage},
                    exit_status="0")
            except Exception:  # noqa: BLE001
                opens_evidence_ok = False
                logger.warning("itops network_inventory: open evidence write failed for %s", o)
    finally:
        # MANDATORY server-owned summary — written on any normal return (including an
        # incomplete scan), so a partial run is visible as `partial`, not "nothing found".
        if result is not None:
            try:
                store.record_evidence(
                    run_id=run_id, target_identity=target_identity, scanner_vantage=result.vantage,
                    operation="network_inventory:_summary",
                    result={"cidr": cidr, "port_profile": profile.name, "ports": list(profile.ports),
                            "vantage": result.vantage, "source_ip": result.source_ip,
                            "planned": result.planned, "attempted": result.attempted,
                            "completed": result.completed, "open_count": len(result.opens),
                            "counts": result.counts, "stop_reason": result.stop_reason,
                            "status": result.status,
                            "caps": {"rate_limit": profile.rate_limit, "total_timeout": profile.total_timeout,
                                     "per_connect_timeout": profile.per_connect_timeout,
                                     "in_flight": profile.in_flight, "max_hosts": profile.max_hosts}},
                    exit_status="0" if result.status == "complete" else "1")
                summary_ok = True
            except Exception:  # noqa: BLE001
                logger.warning("itops network_inventory: SUMMARY evidence write failed for %s", cidr)

    if result is None:
        return {"ok": False, "text": "ERROR: scan did not run", "error": "scan_failed"}
    if result.status != "complete":
        ok, err = False, "scan_incomplete"
    elif not summary_ok or not opens_evidence_ok:
        # Mirror the SSH/inventory adapters: a run can never claim success while any
        # confirmed-open lacks its own persisted proof row (or the summary is missing).
        ok, err = False, "evidence_persist_failed"
    else:
        ok, err = True, None
    text = (f"Network inventory {cidr} ({result.vantage}): status={result.status}; "
            f"{len(result.opens)} open / {result.attempted} of {result.planned} attempted; "
            f"states={result.counts}")
    out: dict[str, Any] = {"ok": ok, "text": text, "cidr": cidr, "status": result.status,
                           "opens": result.opens, "summary_persisted": summary_ok}
    if err:
        out["error"] = err
    return out


def tool_itops_systemd_service_inspect(**_ignored: Any) -> dict[str, Any]:
    """Read-only systemd SERVICE inspect (Phase 4a). Takes NO arguments — the enabled
    Linux profile (principal) and the ONE selected unit come ONLY from the run's bound
    systemd_service scope (the gate already refused any model arg). Runs a SINGLE fixed
    `systemctl show` for a fixed property set, projects the output to TYPED fields (never
    raw stdout), and writes exactly ONE evidence row; a failed evidence write → ok=false.
    NO systemctl status / journalctl / unit-file content, and NO start/stop/restart.
    """
    from app.application.agent_kernel import operation_scope
    from app.application.code_agent.tools import get_current_run_id
    from app.application.it_ops import ssh_enroll
    from app.application.it_ops import systemd_inspect as si
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    scope = operation_scope.get_active_scope(run_id)
    if scope is None or scope.target_kind != "systemd_service" or scope.systemd is None:
        return {"ok": False, "text": "ERROR: no bound systemd scope", "error": "no_systemd_scope"}
    pid = str(scope.profile_id or "").strip()
    unit = str(scope.systemd.unit or "").strip()
    if not si.unit_name_ok(unit):     # defense in depth: the route validated this too
        return {"ok": False, "text": "ERROR: invalid unit name", "error": "bad_unit"}
    try:
        store.init_db()
        prof = store.get_connection_profile(pid)
        asset = store.get_asset(str((prof or {}).get("asset_id") or "")) if prof else None
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": f"ERROR: store unavailable: {exc}", "error": "store_unavailable"}
    if not prof or prof.get("transport") != "ssh":
        return {"ok": False, "text": "ERROR: unknown ssh profile", "error": "unknown_profile"}
    if not asset or asset.get("kind") != "linux":
        return {"ok": False, "text": "ERROR: systemd inspect requires a linux asset",
                "error": "not_linux_asset"}
    if asset.get("lifecycle_state") != "enabled":
        # Defense in depth: the route + gate already require an ENABLED asset, but the
        # handler holds the same line so a scope bound another way can never inspect a
        # draft/unverified asset (fail-closed BEFORE any SSH).
        return {"ok": False, "text": "ERROR: systemd inspect requires a verified/enabled asset",
                "error": "profile_not_enabled"}
    alias = str(prof.get("ssh_alias") or "").strip()
    if not ssh_enroll.alias_ok(alias):
        return {"ok": False, "text": "ERROR: stored alias is not a valid token", "error": "bad_alias"}
    target_identity = f"{asset.get('asset_id')}/{pid}"

    # ONE fixed, read-only `systemctl show` (no shell; the unit is strictly validated).
    try:
        proc = subprocess.run(_ssh_argv(alias, si.show_remote_command(unit)),
                              capture_output=True, timeout=si.INSPECT_TIMEOUT)
        code, raw_out, raw_err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        code, raw_out, raw_err = None, b"", b"connection timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        code, raw_out, raw_err = None, b"", f"ssh could not run: {exc}".encode()

    if code == 0:
        fields = si.parse_show_output(decode_console(raw_out))
        ok, err = True, None
    else:
        # a failed inspect is honestly an error, not an empty/false result
        fields = {}
        ok, err = False, "inspect_failed"

    # TYPED evidence — never raw stdout; only the projected fields + the unit identity.
    result: dict[str, Any] = {"unit": unit, **fields}
    evidence_ok = True
    try:
        store.record_evidence(
            run_id=run_id, target_identity=target_identity, scanner_vantage=_SCANNER_VANTAGE,
            operation="systemd_service_inspect", result=result,
            exit_status="0" if code == 0 else (str(code) if code is not None else ""))
    except Exception:  # noqa: BLE001
        evidence_ok = False
        logger.warning("itops systemd_service_inspect: evidence write failed for %s/%s", pid, unit)
    if not evidence_ok:
        ok, err = False, "evidence_persist_failed"

    if code == 0:
        text = (f"systemd inspect {unit} ({alias}): "
                f"{fields.get('active_state', '?')}/{fields.get('sub_state', '?')} "
                f"main_pid={fields.get('main_pid', '?')} restarts={fields.get('n_restarts', '?')} "
                f"unit_file={fields.get('unit_file_state', '?')}")
    else:
        text = f"systemd inspect {unit} ({alias}): FAILED ({_clean(raw_err, 300) or ('exit ' + str(code))})"
    out: dict[str, Any] = {"ok": ok, "text": text, "unit": unit, "fields": fields,
                           "evidence_persisted": evidence_ok}
    if err:
        out["error"] = err
    return out


_DISPATCH = {
    "itops_ssh_healthcheck": tool_itops_ssh_healthcheck,
    "itops_linux_inventory": tool_itops_linux_inventory,
    "itops_windows_inventory": tool_itops_windows_inventory,
    "itops_network_inventory": tool_itops_network_inventory,
    "itops_systemd_service_inspect": tool_itops_systemd_service_inspect,
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
            {
                "type": "function",
                "function": {
                    "name": "itops_windows_inventory",
                    "description": (
                        "Read-only Windows inventory on a SAVED, verified WINDOWS profile: runs a "
                        "fixed set of harmless PowerShell queries (OS/version, hostname, uptime, "
                        "logical disks, running services, IP config) and returns their output. "
                        "Takes a profile_id (NOT a host/alias). Only runnable inside a bound "
                        "read-only diagnostic run for a windows asset; one call per run."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "profile_id": {
                                "type": "string",
                                "description": "The saved windows connection profile id to inventory.",
                            },
                        },
                        "required": ["profile_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_network_inventory",
                    "description": (
                        "Read-only network inventory: a bounded TCP-connect scan of the authorized "
                        "CIDR bound to this diagnostic run, on a fixed server-owned port set. Takes "
                        "NO arguments — the target and caps come only from the bound scope. Returns "
                        "the open host:port list and a summary. Only runnable inside a bound network "
                        "diagnostic run; one call per run."
                    ),
                    "parameters": {"type": "object", "properties": {}},   # NO args
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_systemd_service_inspect",
                    "description": (
                        "Read-only systemd service inspect: runs one fixed `systemctl show` for the "
                        "unit bound to this diagnostic run on the bound Linux profile, and returns its "
                        "state (active/sub state, main pid, last exit status, restart count, unit-file "
                        "state and path). Takes NO arguments — the profile and unit come only from the "
                        "bound scope. No status text, journal, unit-file content, or changes. Only "
                        "runnable inside a bound systemd diagnostic run; one call per run."
                    ),
                    "parameters": {"type": "object", "properties": {}},   # NO args
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
