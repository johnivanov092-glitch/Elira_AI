"""resource_process — the deferred read-only resource tool (R1 + R2).

One general tool over durable resources. It takes ONLY a resource_id, an
operation, and an execution_target enum — never a path, URL, hostname, argv,
command, model path, or env. The resource_id is resolved against the CURRENT
run's binding — a resource that is unknown, owned by another session, or not
attached to this run fails closed before any bytes are read. The runtime resolves
the id, checks the operation, reads the capability catalog, selects a registered
adapter, hands the adapter the internal path, and returns a bounded result. The
file extension is NOT a routing input; it only lets an adapter validate the
container after the user has stated the task.
"""
from __future__ import annotations

from typing import Any

_ERROR_TEXT = {
    "no_run_context": "resource_process requires a run context",
    "unknown_operation": "operation must be inspect, extract_text or transcribe",
    "invalid_execution_target": "execution_target must be auto, local_gpu, local_cpu or server_gpu",
    "unsupported_arguments": "resource_process accepts only resource_id, operation and execution_target",
    "resource_not_bound": "resource is not attached to this run",
    "resource_not_found": "resource not found",
}


def _refusal(code: str, *, requested_target: str | None) -> dict[str, Any]:
    """Return one stable execution-shaped refusal without echoing invalid input."""
    out: dict[str, Any] = {
        "ok": False,
        "error": code,
        "requested_target": requested_target,
        "selected_target": None,
        "backend": None,
        "text": f"ERROR: {_ERROR_TEXT[code]}",
    }
    if requested_target == "auto":
        out["fallback_chain"] = []
    return out


def tool_resource_process(resource_id: str = "", operation: str = "",
                          execution_target: str = "auto", **extra: Any) -> dict[str, Any]:
    """Process a durable resource attached to this run. Args: resource_id (opaque
    id, NOT a path), operation (inspect | extract_text | transcribe), and
    execution_target (auto | local_gpu | local_cpu | server_gpu). Read-only; one
    bounded result; stable ``error`` code with ok=False on any refusal."""
    from app.application.code_agent.tools import get_current_run_id
    from app.application.media import execution, processing, resource_store, run_binding

    run_id = get_current_run_id()
    resource_id = str(resource_id or "").strip()
    operation = str(operation or "").strip().lower()
    target = str(execution_target or "auto").strip().lower()

    if operation not in processing.SUPPORTED_OPERATIONS:
        safe_target = target if execution.valid_execution_target(target) else None
        return _refusal("unknown_operation", requested_target=safe_target)
    if not execution.valid_execution_target(target):
        return _refusal("invalid_execution_target", requested_target=None)
    # Do not silently accept path/url/argv/backend-like model arguments. The
    # runtime owns every execution detail; extra keys fail closed without echoing
    # their names or values.
    if extra:
        return _refusal("unsupported_arguments", requested_target=target)
    if not run_id:
        return _refusal("no_run_context", requested_target=target)
    # Ownership/run gate FIRST — a bare id (or a path passed as an id) that is not
    # bound to this run is refused before any store lookup or byte read.
    if not run_binding.is_bound(run_id, resource_id):
        return _refusal("resource_not_bound", requested_target=target)
    record = resource_store.get_record(resource_id)
    if record is None:
        return _refusal("resource_not_found", requested_target=target)
    return processing.process_resource(record, operation, target)


# ── resource_materialize (R4A) — bridge a run-bound ResourceRef into the run's
#    project workspace so the existing file/run_bash tools can process it. ──────

import os as _os                                              # noqa: E402
import re as _re                                              # noqa: E402
from pathlib import Path as _Path                             # noqa: E402

_MAX_DEST_DEPTH = 8
_MAX_NAME_CHARS = 200
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
    | {f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "¹²³"}
)

_MATERIALIZE_ERROR_TEXT = {
    "no_run_context": "resource_materialize requires a run context",
    "unsupported_arguments": "resource_materialize accepts only resource_id and destination_name",
    "resource_not_bound": "resource is not attached to this run",
    "resource_not_found": "resource not found",
    "invalid_destination": "destination must be a relative name inside the project workspace",
    "destination_exists": "a file already exists at that destination; choose another name",
    "integrity_mismatch": "the materialized copy did not match the resource; nothing was written",
    "materialize_failed": "could not materialize the resource",
}


def _materialize_refusal(code: str) -> dict[str, Any]:
    """A stable refusal that never echoes the model input or any absolute path."""
    return {"ok": False, "error": code, "text": f"ERROR: {_MATERIALIZE_ERROR_TEXT[code]}"}


def _safe_basename(name: str) -> str:
    """A safe single-component filename from a stored resource name (metadata)."""
    raw = str(name or "").replace("\\", "/").split("/")[-1]
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ch not in '\r\n\t"<>:|?*')
    cleaned = cleaned.strip().strip(".")
    cleaned = cleaned[:_MAX_NAME_CHARS]
    return cleaned if _safe_component(cleaned) else "file"


def _safe_component(value: str) -> bool:
    """Reject names Windows can reinterpret as devices, ADS, or aliases."""
    if not value or value in {".", ".."} or value[-1] in {" ", "."}:
        return False
    if any(ord(ch) < 32 or ch in _WINDOWS_FORBIDDEN for ch in value):
        return False
    device_stem = value.split(".", 1)[0].rstrip(" .").upper()
    return device_stem not in _WINDOWS_RESERVED


def _safe_dest(project_root: _Path, name: str) -> _Path | None:
    """Resolve *name* STRICTLY inside *project_root*. Returns the canonical target
    path or None on ANY escape. Rejects absolute paths, drive prefixes, UNC,
    ``..``, NUL/control; and rejects a symlink/junction/reparse escape by resolving
    the target (which follows existing reparse points) and requiring it to stay
    under the real project root. There is NO ELIRA_FS_UNRESTRICTED bypass — a
    materialized file always stays in the workspace."""
    import ntpath
    raw = str(name or "")
    if not raw or raw != raw.strip():
        return None
    if _os.path.isabs(raw) or ntpath.isabs(raw) or ntpath.splitdrive(raw)[0]:
        return None
    if raw[:2] in ("\\\\", "//"):                            # UNC
        return None
    parts = [p for p in _re.split(r"[\\/]+", raw) if p not in ("", ".")]
    if not parts or any(not _safe_component(p) for p in parts):
        return None
    if len(parts) > _MAX_DEST_DEPTH or any(len(p) > _MAX_NAME_CHARS for p in parts):
        return None
    try:
        real_root = project_root.resolve()
        target = real_root.joinpath(*parts)
        resolved = target.resolve()                          # follows existing junctions/symlinks
        resolved.relative_to(real_root)                      # escape → ValueError
    except Exception:  # noqa: BLE001
        return None
    if resolved == real_root:
        return None
    return resolved


def tool_resource_materialize(project_root: Any, resource_id: str = "",
                              destination_name: str = "", **extra: Any) -> dict[str, Any]:
    """Materialize a run-bound resource into the project workspace so the existing
    file/run_bash tools can process it. Args: resource_id (opaque id, NOT a path)
    and optional destination_name (a relative name inside the workspace; default =
    the resource's safe basename). Writes a NEW file (never overwrites); returns a
    project-relative path only — never an absolute/storage path."""
    from app.application.code_agent.tools import get_current_run_id
    from app.application.media import resource_store, run_binding

    run_id = get_current_run_id()
    resource_id = str(resource_id or "").strip()
    if extra:
        return _materialize_refusal("unsupported_arguments")
    if not run_id:
        return _materialize_refusal("no_run_context")
    # Ownership/run gate FIRST — an unbound id (or a path passed as an id) is
    # refused before any store lookup or byte read.
    if not run_binding.is_bound(run_id, resource_id):
        return _materialize_refusal("resource_not_bound")
    record = resource_store.get_record(resource_id)
    if record is None:
        return _materialize_refusal("resource_not_found")

    root = _Path(str(project_root)).resolve()
    raw_destination = str(destination_name or "")
    requested = raw_destination if raw_destination.strip() else _safe_basename(record.original_name)
    dest = _safe_dest(root, requested)
    if dest is None:
        return _materialize_refusal("invalid_destination")
    if dest.exists():
        return _materialize_refusal("destination_exists")
    rel = dest.relative_to(root).as_posix()
    try:
        resource_store.materialize(
            record,
            dest.parent,
            dest.name,
            workspace_root=root,
        )
    except resource_store.ResourceError as exc:
        code = exc.reason if exc.reason in _MATERIALIZE_ERROR_TEXT else "materialize_failed"
        return _materialize_refusal(code)
    except Exception:  # noqa: BLE001 — never surface a raw path/exception
        return _materialize_refusal("materialize_failed")

    return {
        "ok": True,
        "resource_id": resource_id,
        "project_path": rel,
        "size": record.size,
        "sha256": record.sha256,
        # Surface it through the existing touched-files/artifact flow.
        "touched_path": rel,
        "diff_action": "create",
        "text": f"Materialized attached resource into {rel} ({record.size} bytes)",
    }
