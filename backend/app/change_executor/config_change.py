"""Typed executor-side protocol for the fixed Netdata configuration target.

The remote helper emits JSON, but JSON alone is not evidence. This module validates and
projects that protocol before the change engine may plan, finalize, persist, or resolve a
run. Raw helper output and unknown keys never cross this boundary.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable

from . import transport
from ._frozen import decode_console
from .registry import Target

CONFIG_PATH = "/etc/netdata/netdata.conf"
PROTOCOL_VERSION = 1
MAX_PAYLOAD = 32 * 1024

Runner = Callable[[list[str], int], "transport.SshResult"]
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUS_EXIT = {
    "applied": 0,
    "aborted_before_apply": 10,
    "rolled_back": 20,
    "rollback_failed": 21,
    "command_failed": 22,
}


def _service(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("id", "load_state", "active_state", "sub_state"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        out[key] = value
    pid = raw.get("main_pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    out["main_pid"] = pid
    return out


def healthy(service: dict[str, Any], unit: str) -> bool:
    return (service.get("id") == unit and service.get("load_state") == "loaded"
            and service.get("active_state") == "active" and service.get("sub_state") == "running"
            and isinstance(service.get("main_pid"), int)
            and not isinstance(service.get("main_pid"), bool) and int(service["main_pid"]) > 0)


def _json(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > MAX_PAYLOAD:
        return None
    try:
        value = json.loads(decode_console(raw))
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _sha(value: Any) -> str | None:
    text = value if isinstance(value, str) else ""
    return text if _SHA_RE.fullmatch(text) else None


def _inspect_projection(target: Target, payload: dict[str, Any]) -> dict[str, Any] | None:
    before = _sha(payload.get("before_sha256"))
    planned = _sha(payload.get("planned_sha256"))
    service = _service(payload.get("service"))
    safe = payload.get("safe_settings")
    desired = payload.get("desired_settings")
    size = payload.get("size_bytes")
    if (payload.get("protocol") != PROTOCOL_VERSION or payload.get("status") != "ok"
            or payload.get("helper_sha256") != target.helper_sha256
            or payload.get("config_id") != target.config_id or payload.get("path") != CONFIG_PATH
            or before is None or planned is None or service is None
            or not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= 256 * 1024
            or not isinstance(payload.get("comment_only"), bool)
            or not isinstance(safe, dict) or desired != {"global.update_every": 1}):
        return None
    if set(safe) - {"global.update_every"}:
        return None
    if "global.update_every" in safe:
        current = safe["global.update_every"]
        if not isinstance(current, int) or isinstance(current, bool) or not 1 <= current <= 60:
            return None
    if not healthy(service, target.unit):
        return None
    return {
        "kind": "netdata_config",
        "config_id": target.config_id,
        "before_sha256": before,
        "planned_sha256": planned,
        "size_bytes": size,
        "comment_only": payload["comment_only"],
        "safe_settings": dict(safe),
        "desired_settings": {"global.update_every": 1},
        "service": service,
        "helper_sha256": target.helper_sha256,
    }


def inspect(target: Target, runner: Runner) -> tuple[bool, str, dict[str, Any]]:
    try:
        result = runner(transport.config_inspect_argv(target), transport.INSPECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "timeout", {}
    except OSError:
        return False, "error", {}
    if result.exit_code != 0:
        return False, str(result.exit_code), {}
    payload = _json(result.stdout)
    projected = _inspect_projection(target, payload or {})
    return (projected is not None, "0", projected or {})


def apply(target: Target, *, change_run_id: str, before_sha256: str,
          after_sha256: str, before_main_pid: int,
          runner: Runner) -> tuple[str, str, dict[str, Any]]:
    """Return (status, exit_status, projected evidence). Ambiguous transport/protocol
    outcomes are always `apply_unknown`; definite helper states are projected strictly."""
    argv = transport.config_apply_argv(
        target, change_run_id=change_run_id,
        before_sha256=before_sha256, after_sha256=after_sha256)
    try:
        result = runner(argv, transport.CONFIG_APPLY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "apply_unknown", "timeout", {"outcome": "timeout"}
    except OSError:
        return "command_failed", "error", {"outcome": "ssh_launch_error"}
    if result.exit_code == 255:
        return "apply_unknown", "255", {"outcome": "ssh_255"}
    payload = _json(result.stdout)
    if payload is None:
        return "apply_unknown", str(result.exit_code), {"outcome": "invalid_helper_response"}
    status = payload.get("status")
    if status not in _STATUS_EXIT:
        return "apply_unknown", str(result.exit_code), {"outcome": "invalid_helper_status"}
    if result.exit_code != _STATUS_EXIT[status]:
        return "apply_unknown", str(result.exit_code), {
            "outcome": "helper_exit_status_mismatch",
        }
    before = _sha(payload.get("before_sha256"))
    after = _sha(payload.get("after_sha256"))
    if status not in ("aborted_before_apply", "command_failed"):
        if (before != before_sha256 or after != after_sha256
                or payload.get("helper_sha256") != target.helper_sha256):
            return "apply_unknown", str(result.exit_code), {"outcome": "helper_binding_mismatch"}
    projected: dict[str, Any] = {
        "outcome": status,
        "config_id": target.config_id,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "rollback_attempted": payload.get("rollback_attempted") is True,
    }
    reason = payload.get("reason")
    if isinstance(reason, str):
        projected["reason"] = reason[:200]
    service = _service(payload.get("service"))
    if service is not None:
        projected["fields"] = service
    if status in ("applied", "rolled_back"):
        if (not healthy(service or {}, target.unit)
                or int((service or {}).get("main_pid") or 0) == int(before_main_pid)):
            return "apply_unknown", str(result.exit_code), {
                "outcome": "invalid_success_evidence",
            }
    return str(status), str(result.exit_code), projected
