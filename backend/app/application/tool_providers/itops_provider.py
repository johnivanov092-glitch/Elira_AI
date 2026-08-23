"""IT-Ops tool provider: workflow diagnostics and local changes.

Exposes read-only diagnostic adapters that run a FIXED command set over OS OpenSSH
against an explicit target or an optional saved connection shortcut:
  * ``itops_ssh_healthcheck(target)`` — hostname; uname -a; uptime.
  * ``itops_linux_inventory(target)`` — a curated Linux inventory set.
  * ``itops_windows_inventory(target)`` — a curated Windows inventory set, run as
    STATIC PowerShell via -EncodedCommand (no quoting through cmd/sshd).
  * ``itops_config_inspect()`` — one named config target from a typed scope, projected
    through a strict parser without returning raw content.
  * ``itops_database_inspect()`` — one local named SQLite target, opened read-only and
    projected to schema metadata, migration/backup state and fixed aggregate counts.
SSH targets are not required to be registered as assets or profiles. A saved
``profile_id`` remains a discovery shortcut only. The Workflow permission is the
only product authorization boundary.

Runtime properties:
- No second SSH client: adapters reuse saved OpenSSH profiles.
  provider. Each command is a fixed argv (no shell), so nothing is injectable.
- Every command's output is redacted + capped INDIVIDUALLY, then persisted as
  evidence ({profile/asset, command id, exit, timestamp}).
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from app.infrastructure.encoding import decode_console
from app.application.tool_providers.ssh_provider import _ssh_args, run_registered_process

logger = logging.getLogger(__name__)

# Fixed read-only diagnostic set. (command_id, argv) — argv is passed verbatim as
# the remote command; there is NO shell and NO model input in it.
_HEALTH_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("hostname", ["hostname"]),
    ("uname", ["uname", "-a"]),
    ("uptime", ["uptime"]),
)
_PER_CMD_CAP = 4000          # redacted stdout/stderr chars kept per command (evidence + reply)
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
_NETWORK_OPEN_ENDPOINT_LIMIT = 200

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


def _ssh_argv(alias: str, remote: list[str]) -> list[str]:
    return [*_ssh_args(alias, connect_timeout_seconds=10), *remote]


class ItopsTargetError(ValueError):
    """Malformed or missing SSH target metadata."""


def _resolve_ssh_target(*, target: str = "", profile_id: str = "") -> tuple[str, str, str]:
    """Resolve an explicit SSH target or an optional saved profile shortcut.

    Assets and profiles are metadata, never authorization. An explicit target wins
    and is passed as one argv item. Only option/NUL injection is rejected; OpenSSH
    owns hostname, alias, ``user@host`` and address interpretation.
    """
    direct = str(target or "").strip()
    pid = str(profile_id or "").strip()
    if direct:
        if direct.startswith("-") or "\x00" in direct:
            raise ItopsTargetError("invalid_ssh_target")
        return direct, pid, f"ssh/{direct}"
    if not pid:
        raise ItopsTargetError("ssh_target_required")

    from app.infrastructure.it_ops import store

    store.init_db()
    prof = store.get_connection_profile(pid)
    if not prof or prof.get("transport") != "ssh":
        raise ItopsTargetError("unknown_profile")
    alias = str(prof.get("ssh_alias") or "").strip()
    if not alias or alias.startswith("-") or "\x00" in alias:
        raise ItopsTargetError("invalid_ssh_target")
    return alias, pid, f"profile/{pid}"


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


def tool_itops_ssh_healthcheck(
    target: str = "", profile_id: str = "", **_ignored: Any,
) -> dict[str, Any]:
    """Run the fixed read-only health check on any SSH target.

    profile_id optionally selects saved connection metadata. It is not an
    authorization scope; Workflow permission owns the execution decision. run_id
    comes from runtime context and is used only to correlate evidence.
    Returns {ok, text, results:[...]} and writes one evidence row per command.
    """
    from app.application.code_agent.tools import get_current_run_id
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()   # authoritative — from runtime context, not args
    try:
        alias, pid, target_identity = _resolve_ssh_target(target=target, profile_id=profile_id)
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or "ssh_target_unavailable"
        return {"ok": False, "text": f"ERROR: {error}", "error": error}

    results: list[dict[str, Any]] = []
    lines: list[str] = [f"Read-only health check — {alias}:"]
    evidence_ok = True
    for cmd_id, remote in _HEALTH_COMMANDS:
        try:
            proc = run_registered_process(_ssh_argv(alias, remote))
            code = proc.returncode
            out = _clean(proc.stdout, _PER_CMD_CAP)
            err = _clean(proc.stderr, _PER_CMD_CAP)
        except OSError as exc:
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


def tool_itops_linux_inventory(
    target: str = "", profile_id: str = "", **_ignored: Any,
) -> dict[str, Any]:
    """Linux read-only inventory on any SSH target (adapter #1).

    Runs a fixed set of read-only commands (≤10s each). The 12K TOTAL cap is a shared
    budget across all commands, applied to BOTH the reply AND the persisted evidence.
    Any command failure (non-zero that is NOT the exact systemd-absent signal)
    makes the whole inventory ok=false; a failed evidence write does too.
    """
    from app.application.code_agent.tools import get_current_run_id
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    try:
        alias, pid, target_identity = _resolve_ssh_target(target=target, profile_id=profile_id)
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or "ssh_target_unavailable"
        return {"ok": False, "text": f"ERROR: {error}", "error": error}

    budget = _INVENTORY_TOTAL_CAP     # shared across commands: bounds reply AND evidence
    results: list[dict[str, Any]] = []
    lines: list[str] = [f"Linux inventory — {alias}:"]
    cmds_ok = True
    evidence_ok = True
    for cmd_id, remote, required in _INVENTORY_COMMANDS:
        try:
            proc = run_registered_process(_ssh_argv(alias, remote))
            code, raw_out, raw_err = proc.returncode, proc.stdout, proc.stderr
        except OSError as exc:
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


def tool_itops_windows_inventory(
    target: str = "", profile_id: str = "", **_ignored: Any,
) -> dict[str, Any]:
    """Windows read-only inventory on any SSH target (adapter #2).

    Runs a fixed set of STATIC PowerShell scripts via -EncodedCommand (≤15s each).
    The 12K TOTAL cap is a shared context budget across all commands, applied to
    both the reply and persisted evidence. Any command failure or a failed evidence
    write makes the whole inventory ok=false.
    """
    from app.application.code_agent.tools import get_current_run_id
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    try:
        alias, pid, target_identity = _resolve_ssh_target(target=target, profile_id=profile_id)
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or "ssh_target_unavailable"
        return {"ok": False, "text": f"ERROR: {error}", "error": error}

    budget = _INVENTORY_TOTAL_CAP     # shared across commands: bounds reply AND evidence
    results: list[dict[str, Any]] = []
    lines: list[str] = [f"Windows inventory — {alias}:"]
    cmds_ok = True
    evidence_ok = True
    for cmd_id, script in _WINDOWS_COMMANDS:
        try:
            proc = run_registered_process(_ps_argv(alias, script))
            code, raw_out, raw_err = proc.returncode, proc.stdout, proc.stderr
        except OSError as exc:
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


def tool_itops_network_inventory(
    cidr: str = "",
    ports: list[int] | None = None,
    connect_timeout: float = 1.0,
    concurrency: int = 64,
    **_ignored: Any,
) -> dict[str, Any]:
    """Read-only network inventory (Phase 3) for an explicit IPv4 CIDR.

    Scans until completion or Workflow Stop, with caller-selected ports,
    per-connect transport timeout and concurrency,
    writes one evidence row per CONFIRMED OPEN host:port, and ALWAYS writes a
    server-owned summary in a finally (planned/attempted/completed/state counts,
    stop_reason, status). complete → ok=true; stopped/partial → ok=false
    scan_incomplete; a failed summary write OR a failed per-open write → ok=false
    evidence_persist_failed (no open may be reported without its own proof row). A
    killed process cannot write the summary — absence of a terminal summary is
    'unknown', never 'nothing found'.
    """
    from app.application.code_agent.tools import get_current_run_id, run_was_stopped
    from app.application.it_ops import net_inventory as ni
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    cidr = str(cidr or "").strip()
    try:
        network = ni.parse_cidr_v1(cidr)
        profile = ni.build_profile(
            ports,
            per_connect_timeout=connect_timeout,
            in_flight=concurrency,
        )
    except ni.CidrError as exc:
        return {"ok": False, "text": f"ERROR: {exc.reason}", "error": exc.reason}

    target_identity = f"net/{cidr}"
    result = None
    summary_ok = False
    opens_evidence_ok = True     # every open MUST leave its own durable proof row
    try:
        store.init_db()
        result = ni.run_scan(
            cidr,
            (str(host) for host in network.hosts()),
            profile,
            host_count=ni.usable_host_count(network),
            should_stop=lambda: run_was_stopped(run_id),
        )
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
                            "runtime": {
                                "per_connect_timeout": profile.per_connect_timeout,
                                "in_flight": profile.in_flight,
                            }},
                    exit_status="0" if result.status == "complete" else "1")
                summary_ok = True
            except Exception:  # noqa: BLE001
                logger.warning("itops network_inventory: SUMMARY evidence write failed for %s", cidr)

    if result is None:
        return {"ok": False, "text": "ERROR: scan did not run", "error": "scan_failed"}
    if result.stop_reason == "stopped":
        ok, err = False, "cancelled_by_user"
    elif result.status != "complete":
        ok, err = False, "scan_incomplete"
    elif not summary_ok or not opens_evidence_ok:
        # Mirror the SSH/inventory adapters: a run can never claim success while any
        # confirmed-open lacks its own persisted proof row (or the summary is missing).
        ok, err = False, "evidence_persist_failed"
    else:
        ok, err = True, None
    visible_opens = [
        f"{item['host']}:{item['port']}"
        for item in result.opens[:_NETWORK_OPEN_ENDPOINT_LIMIT]
    ]
    open_endpoints = ",".join(visible_opens) or "none"
    if len(result.opens) > len(visible_opens):
        open_endpoints += f",...(+{len(result.opens) - len(visible_opens)} more)"
    text = (
        f"Network inventory {cidr} ({result.vantage}): status={result.status}; "
        f"{len(result.opens)} open / {result.attempted} of {result.planned} attempted; "
        f"states={result.counts}; open_endpoints={open_endpoints}"
    )
    out: dict[str, Any] = {"ok": ok, "text": text, "cidr": cidr, "status": result.status,
                           "opens": result.opens, "summary_persisted": summary_ok}
    if err:
        out["error"] = err
    return out


def tool_itops_systemd_service_inspect(
    target: str = "",
    profile_id: str = "",
    unit: str = "",
    **_ignored: Any,
) -> dict[str, Any]:
    """Read-only systemd SERVICE inspect for any SSH target and unit.

    Runs a SINGLE fixed
    `systemctl show` for a fixed property set, projects the output to TYPED fields (never
    raw stdout), and writes exactly ONE evidence row; a failed evidence write → ok=false.
    NO systemctl status / journalctl / unit-file content, and NO start/stop/restart.
    """
    from app.application.code_agent.tools import get_current_run_id
    from app.application.it_ops import systemd_inspect as si
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    unit = str(unit or "").strip()
    if not si.unit_name_ok(unit):
        return {"ok": False, "text": "ERROR: invalid unit name", "error": "bad_unit"}
    try:
        alias, pid, target_identity = _resolve_ssh_target(target=target, profile_id=profile_id)
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or "ssh_target_unavailable"
        return {"ok": False, "text": f"ERROR: {error}", "error": error}

    # ONE fixed, read-only `systemctl show` (no shell; the unit is strictly validated).
    try:
        proc = run_registered_process(_ssh_argv(alias, si.show_remote_command(unit)))
        code, raw_out, raw_err = proc.returncode, proc.stdout, proc.stderr
    except OSError as exc:
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


def tool_itops_config_inspect(
    target: str = "",
    profile_id: str = "",
    config_id: str = "",
    **_ignored: Any,
) -> dict[str, Any]:
    """Read one named config target on any SSH target and persist its projection."""
    from app.application.code_agent.tools import get_current_run_id
    from app.application.it_ops import config_inspect as ci
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    try:
        spec = ci.resolve_config(str(config_id or "").strip())
    except ci.ConfigInspectError as exc:
        return {"ok": False, "text": f"ERROR: {exc.reason}", "error": exc.reason}

    try:
        alias, pid, target_identity = _resolve_ssh_target(target=target, profile_id=profile_id)
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or "ssh_target_unavailable"
        return {"ok": False, "text": f"ERROR: {error}", "error": error}

    code: int | None
    raw_out: bytes
    raw_err: bytes
    try:
        proc = run_registered_process(_ssh_argv(alias, ci.read_remote_command(spec)))
        code, raw_out, raw_err = proc.returncode, proc.stdout, proc.stderr
    except OSError as exc:
        code, raw_out, raw_err = None, b"", f"ssh could not run: {exc}".encode()

    projection: dict[str, Any] = {
        "config_id": spec.config_id,
        "path": spec.path,
        "format": spec.format,
        "status": "failed",
    }
    if code == 0:
        try:
            projection = {**ci.inspect_bytes(spec, raw_out), "status": "ok"}
            ok, err = True, None
        except ci.ConfigInspectError as exc:
            projection["error"] = exc.reason
            ok, err = False, exc.reason
    else:
        ok, err = False, "inspect_failed"
        projection["error"] = err

    evidence_ok = True
    try:
        store.record_evidence(
            run_id=run_id, target_identity=target_identity, scanner_vantage=_SCANNER_VANTAGE,
            operation="config_inspect:netdata-main", result=projection,
            exit_status="0" if code == 0 else (str(code) if code is not None else ""))
    except Exception:  # noqa: BLE001
        evidence_ok = False
        logger.warning("itops config_inspect: evidence write failed for %s/%s", pid, spec.config_id)
    if not evidence_ok:
        ok, err = False, "evidence_persist_failed"

    if projection.get("status") == "ok":
        text = (f"Config inspect {spec.config_id} ({alias}): sha256={projection['sha256']} "
                f"bytes={projection['bytes']} comment_only={projection['comment_only']} "
                f"safe_settings={projection['safe_settings']}")
    elif code == 0:
        text = f"Config inspect {spec.config_id} ({alias}): FAILED ({err})"
    else:
        text = f"Config inspect {spec.config_id} ({alias}): FAILED ({_clean(raw_err, 300) or ('exit ' + str(code))})"
    out: dict[str, Any] = {"ok": ok, "text": text, "config": projection,
                           "evidence_persisted": evidence_ok}
    if err:
        out["error"] = err
    return out


def tool_itops_database_inspect(
    database_id: str = "",
    **_ignored: Any,
) -> dict[str, Any]:
    """Inspect one registered SQLite database selected by id.

    The inspector owns the path, URI,
    schemas, limits and fixed aggregate query. Evidence is a flat safe projection:
    no path/DSN/SQL, schema details or row contents are persisted.
    """
    from app.application.code_agent.tools import get_current_run_id
    from app.application.it_ops import database_inspect as di
    from app.infrastructure.it_ops import store

    run_id = get_current_run_id()
    try:
        spec = di.resolve_database(str(database_id or "").strip())
    except di.DatabaseInspectError as exc:
        return {"ok": False, "text": f"ERROR: {exc.reason}", "error": exc.reason}

    try:
        store.init_db()
    except Exception:  # noqa: BLE001
        logger.warning("itops database_inspect: evidence store unavailable for %s", spec.database_id)
        return {"ok": False, "text": "ERROR: evidence store unavailable",
                "error": "store_unavailable"}

    inspection: dict[str, Any] | None = None
    error: str | None = None
    try:
        inspection = di.inspect_database(spec)
    except di.DatabaseInspectError as exc:
        error = exc.reason

    if inspection is not None:
        safe_query = inspection["safe_query"]
        backup = inspection["backup"]
        evidence_result: dict[str, Any] = {
            "database_id": spec.database_id,
            "engine": spec.engine,
            "status": "ok",
            "quick_check": inspection["quick_check"],
            "user_version": inspection["user_version"],
            "schema_version": inspection["schema_version"],
            "migration_state": inspection["migration_state"],
            "table_count": inspection["table_count"],
            "schema_summary": inspection["schema_summary"],
            "file_bytes": inspection["file_bytes"],
            "file_modified_at": inspection["file_modified_at"],
            "safe_query_profile": safe_query["profile"],
            "chat_count": safe_query["chat_count"],
            "message_count": safe_query["message_count"],
            "backup_status": backup["status"],
            "backup_count": backup["count"],
            "backup_stale_after_seconds": backup["stale_after_seconds"],
        }
        if "latest_age_seconds" in backup:
            evidence_result["backup_age_seconds"] = backup["latest_age_seconds"]
    else:
        evidence_result = {
            "database_id": spec.database_id,
            "engine": spec.engine,
            "status": "failed",
        }

    evidence_ok = True
    try:
        store.record_evidence(
            run_id=run_id,
            target_identity=f"database:{spec.database_id}",
            scanner_vantage="elira-local:sqlite-ro",
            operation=f"database_inspect:{spec.database_id}",
            result=evidence_result,
            exit_status="0" if inspection is not None else "1",
        )
    except Exception:  # noqa: BLE001
        evidence_ok = False
        logger.warning("itops database_inspect: evidence write failed for %s", spec.database_id)

    if not evidence_ok:
        return {"ok": False, "text": "Database inspect failed: evidence was not persisted",
                "error": "evidence_persist_failed", "evidence_persisted": False}
    if inspection is None:
        return {"ok": False, "text": f"Database inspect {spec.database_id}: FAILED ({error})",
                "error": error or "inspect_failed", "evidence_persisted": True}

    safe_query = inspection["safe_query"]
    backup = inspection["backup"]
    text = (
        f"Database inspect {spec.database_id}: quick_check={inspection['quick_check']} "
        f"user_version={inspection['user_version']} tables={inspection['table_count']} "
        f"chats={safe_query['chat_count']} messages={safe_query['message_count']} "
        f"backup={backup['status']}\nSchema: {inspection['schema_summary']}"
    )
    return {"ok": True, "text": text, "database": inspection,
            "evidence_persisted": True}


def _tool_itops_mikrotik_inventory(**kwargs: Any) -> dict[str, Any]:
    """Thin dispatch shim: the full adapter (format check, fixed MCP
    call plan, projection, evidence, bounded text) lives in
    app.application.it_ops.mikrotik_runtime. Imported lazily so the provider
    module never grows a transport dependency."""
    from app.application.it_ops.mikrotik_runtime import tool_itops_mikrotik_inventory
    return tool_itops_mikrotik_inventory(**kwargs)


_DISPATCH = {
    "itops_ssh_healthcheck": tool_itops_ssh_healthcheck,
    "itops_linux_inventory": tool_itops_linux_inventory,
    "itops_windows_inventory": tool_itops_windows_inventory,
    "itops_network_inventory": tool_itops_network_inventory,
    "itops_systemd_service_inspect": tool_itops_systemd_service_inspect,
    "itops_config_inspect": tool_itops_config_inspect,
    "itops_database_inspect": tool_itops_database_inspect,
    "itops_mikrotik_inventory": _tool_itops_mikrotik_inventory,
}


class ItopsToolProvider:
    """ToolProvider for Workflow-controlled IT Ops runtimes."""

    name = "itops"

    def is_enabled(self) -> bool:
        return True

    def get_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "itops_ssh_healthcheck",
                    "description": (
                        "Read-only SSH diagnostic on any target: runs a "
                        "fixed set of harmless commands (hostname, uname -a, uptime) and returns "
                        "their output. Pass target directly; profile_id is an optional saved shortcut."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "profile_id": {
                                "type": "string",
                                "description": "Optional saved connection shortcut.",
                            },
                            "target": {
                                "type": "string",
                                "description": "SSH alias, hostname, IP address, or user@host.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_linux_inventory",
                    "description": (
                        "Read-only Linux inventory on any SSH target: runs a fixed "
                        "set of harmless commands (hostname, uname, /etc/os-release, lscpu, free, df, "
                        "ip addr, uptime, lsblk, failed systemd units) and returns their output. "
                        "Pass target directly; profile_id is an optional saved shortcut."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "profile_id": {
                                "type": "string",
                                "description": "Optional saved connection shortcut.",
                            },
                            "target": {
                                "type": "string",
                                "description": "SSH alias, hostname, IP address, or user@host.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_windows_inventory",
                    "description": (
                        "Read-only Windows inventory on any SSH target: runs a "
                        "fixed set of harmless PowerShell queries (OS/version, hostname, uptime, "
                        "logical disks, running services, IP config) and returns their output. "
                        "Pass target directly; profile_id is an optional saved shortcut."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "profile_id": {
                                "type": "string",
                                "description": "Optional saved connection shortcut.",
                            },
                            "target": {
                                "type": "string",
                                "description": "SSH alias, hostname, IP address, or user@host.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_network_inventory",
                    "description": (
                        "Read-only TCP-connect inventory of any canonical IPv4 CIDR. "
                        "Ports and concurrency are caller-controlled; there is no product host cap "
                        "or whole-scan timeout. The scan runs until complete or Workflow Stop."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cidr": {"type": "string", "description": "Canonical IPv4 CIDR."},
                            "ports": {
                                "type": "array",
                                "items": {"type": "integer", "minimum": 1, "maximum": 65535},
                                "description": "TCP ports; defaults to common services.",
                            },
                            "connect_timeout": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": "Per-connection transport timeout in seconds.",
                            },
                            "concurrency": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "Concurrent connection attempts.",
                            },
                        },
                        "required": ["cidr"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_systemd_service_inspect",
                    "description": (
                        "Read-only systemd service inspect: runs one fixed `systemctl show` for a "
                        "unit on any SSH target, and returns its "
                        "state (active/sub state, main pid, last exit status, restart count, unit-file "
                        "state and path). No status text, journal, unit-file content, or changes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string", "description": "SSH alias, hostname, IP, or user@host."},
                            "profile_id": {"type": "string"},
                            "unit": {"type": "string"},
                        },
                        "required": ["unit"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_config_inspect",
                    "description": (
                        "Read-only typed configuration inspect for a named config on any SSH target. "
                        "The runtime resolves path, format and projected keys. Returns "
                        "hash/size and typed settings; "
                        "never raw config text or unknown values. One call per run."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string", "description": "SSH alias, hostname, IP, or user@host."},
                            "profile_id": {"type": "string"},
                            "config_id": {"type": "string"},
                        },
                        "required": ["config_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_database_inspect",
                    "description": (
                        "Read-only inspection of a registered local database target. The runtime "
                        "resolves path, engine and query profile by database_id. Returns bounded "
                        "schema metadata, migration and "
                        "backup state, and aggregate counts; never row contents, SQL or a "
                        "connection string. One call per run."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"database_id": {"type": "string"}},
                        "required": ["database_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "itops_mikrotik_inventory",
                    "description": (
                        "Read-only MikroTik inventory via the mikrotik MCP runtime: system "
                        "(resource/identity/license/routerboard/clock), interfaces, routes, DNS and "
                        "DHCP servers, projected and bounded. Takes an explicit configured router_id. "
                        "IP addresses are not covered when the upstream runtime lacks that tool."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"router_id": {"type": "string"}},
                        "required": ["router_id"],
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
