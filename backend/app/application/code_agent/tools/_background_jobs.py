"""Durable metadata and reconciliation for ``run_server(kind='job')``.

This module does not execute agent tools and owns no process registry. The
canonical ``_run`` runtime still launches, lists, logs and stops processes. The
journal only preserves enough machine-local state to reconstruct a handle after
the backend process restarts.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.data_files import data_subdir
from app.core.redaction import redact_text


_SCHEMA_VERSION = 1
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_MAX_TERMINAL_RECORDS = 128
_TERMINAL_RETENTION_SECONDS = 7 * 24 * 60 * 60
_JOURNAL_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def _state_dir() -> Path:
    return data_subdir("background_jobs")


def _journal_path() -> Path:
    return _state_dir() / "jobs.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_journal() -> dict[str, Any]:
    path = _journal_path()
    if not path.exists():
        return {"schema_version": _SCHEMA_VERSION, "jobs": {}}
    payload = _read_json(path)
    invalid_reason: str | None = None
    if payload is None:
        invalid_reason = "unreadable or malformed JSON"
    elif payload.get("schema_version") != _SCHEMA_VERSION:
        invalid_reason = f"unsupported schema_version={payload.get('schema_version')!r}"
    elif not isinstance(payload.get("jobs"), dict):
        invalid_reason = "jobs must be an object"
    if invalid_reason is not None:
        quarantine = path.with_name(
            f"{path.stem}.corrupt-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}{path.suffix}"
        )
        try:
            os.replace(path, quarantine)
        except OSError as exc:
            raise RuntimeError(
                f"background job journal is invalid ({invalid_reason}) and could not be "
                f"quarantined: {exc}"
            ) from exc
        logger.error(
            "quarantined invalid background job journal %s as %s: %s",
            path,
            quarantine,
            invalid_reason,
        )
        return {"schema_version": _SCHEMA_VERSION, "jobs": {}}
    jobs = payload.get("jobs")
    return {"schema_version": _SCHEMA_VERSION, "jobs": jobs}


def _prune_terminal_records(journal: dict[str, Any]) -> None:
    jobs = journal["jobs"]
    now = time.time()
    terminal = sorted(
        (
            record
            for record in jobs.values()
            if isinstance(record, dict) and record.get("status") in _TERMINAL_STATUSES
        ),
        key=lambda record: float(record.get("finished_at") or record.get("updated_at") or 0),
        reverse=True,
    )
    expired_ids = {
        str(record.get("job_id") or "")
        for record in terminal
        if now - float(record.get("finished_at") or record.get("updated_at") or now)
        > _TERMINAL_RETENTION_SECONDS
    }
    expired_ids.update(
        str(record.get("job_id") or "")
        for record in terminal[_MAX_TERMINAL_RECORDS:]
    )
    for job_id in expired_ids:
        if job_id:
            record = jobs.pop(job_id, None)
            if isinstance(record, dict):
                root = _state_dir().resolve()
                for key in ("spec_path", "launch_path", "result_path"):
                    try:
                        candidate = Path(str(record.get(key) or "")).resolve()
                        if candidate.parent == root and candidate.name.startswith(job_id):
                            candidate.unlink(missing_ok=True)
                    except OSError:
                        pass


def process_identity(pid: int) -> str | None:
    """Return an OS process-creation token used to reject a reused PID."""
    if pid <= 0:
        return None
    if os.name == "nt":
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            return None
        try:
            created = FileTime()
            exited = FileTime()
            kernel = FileTime()
            user = FileTime()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            return f"win:{(int(created.high) << 32) | int(created.low)}"
        finally:
            kernel32.CloseHandle(handle)
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8")
        fields_after_name = raw[raw.rfind(")") + 2 :].split()
        return f"proc:{fields_after_name[19]}"
    except (OSError, IndexError, ValueError):
        return None


def process_matches(pid: int, expected_identity: str | None) -> bool:
    current = process_identity(pid)
    if current is not None:
        return expected_identity is None or current == expected_identity
    if expected_identity is not None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def prepare_job(
    command: str,
    cwd: Path,
    *,
    log_path: Path,
    run_id: str | None,
    started_at: float,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    state_dir = _state_dir()
    spec_path = state_dir / f"{job_id}.spec.json"
    launch_path = state_dir / f"{job_id}.launch.json"
    result_path = state_dir / f"{job_id}.result.json"
    _atomic_json(
        spec_path,
        {
            "schema_version": _SCHEMA_VERSION,
            "job_id": job_id,
            "command": command,
            "cwd": str(cwd.resolve()),
            "launch_path": str(launch_path.resolve()),
            "result_path": str(result_path.resolve()),
        },
    )
    prepared = {
        "job_id": job_id,
        "spec_path": spec_path,
        "launch_path": launch_path,
        "result_path": result_path,
        "argv": [
            sys.executable,
            str(Path(__file__).with_name("_job_worker.py").resolve()),
            str(spec_path.resolve()),
        ],
    }
    record = {
        "job_id": job_id,
        "pid": 0,
        "process_identity": None,
        "command": redact_text(command),
        "cwd": str(cwd.resolve()),
        "log_path": str(log_path.resolve()),
        "spec_path": str(spec_path.resolve()),
        "launch_path": str(launch_path.resolve()),
        "result_path": str(result_path.resolve()),
        "run_id": run_id,
        "kind": "job",
        "status": "starting",
        "exit_code": None,
        "started_at": float(started_at),
        "updated_at": time.time(),
        "finished_at": None,
        "error": None,
    }
    try:
        with _JOURNAL_LOCK:
            journal = _load_journal()
            journal["jobs"][job_id] = record
            _prune_terminal_records(journal)
            _atomic_json(_journal_path(), journal)
    except Exception:
        for key in ("spec_path", "launch_path", "result_path"):
            try:
                Path(prepared[key]).unlink(missing_ok=True)
            except (KeyError, OSError, TypeError):
                pass
        raise
    prepared["record"] = record
    return prepared


def discard_prepared_job(prepared: dict[str, Any]) -> None:
    job_id = str(prepared.get("job_id") or "")
    for key in ("spec_path", "launch_path", "result_path"):
        try:
            Path(prepared[key]).unlink(missing_ok=True)
        except (KeyError, OSError, TypeError):
            pass
    if job_id:
        with _JOURNAL_LOCK:
            journal = _load_journal()
            if journal["jobs"].pop(job_id, None) is not None:
                _atomic_json(_journal_path(), journal)


def activate_job(
    prepared: dict[str, Any],
    *,
    pid: int,
) -> dict[str, Any]:
    job_id = str(prepared["job_id"])
    identity = process_identity(int(pid))
    if identity is None:
        raise RuntimeError(f"could not read process identity for pid={pid}")
    with _JOURNAL_LOCK:
        journal = _load_journal()
        record = journal["jobs"].get(job_id)
        if not isinstance(record, dict):
            raise RuntimeError(f"prepared background job {job_id} is missing")
        record.update(
            {
                "pid": int(pid),
                "process_identity": identity,
                "status": "running",
                "updated_at": time.time(),
            }
        )
        _prune_terminal_records(journal)
        _atomic_json(_journal_path(), journal)
    return dict(record)


def _activate_from_launch(record: dict[str, Any]) -> bool:
    launch_path = Path(str(record.get("launch_path") or ""))
    payload = _read_json(launch_path) if launch_path.name else None
    if not payload or payload.get("job_id") != record.get("job_id"):
        return False
    pid = int(payload.get("pid") or 0)
    identity = str(payload.get("process_identity") or "") or None
    if pid <= 0 or identity is None:
        return False
    record.update(
        {
            "pid": pid,
            "process_identity": identity,
            "status": "running",
            "updated_at": time.time(),
        }
    )
    return True


def _result_for(record: dict[str, Any]) -> dict[str, Any] | None:
    result_path = Path(str(record.get("result_path") or ""))
    payload = _read_json(result_path) if result_path.name else None
    if not payload or payload.get("job_id") != record.get("job_id"):
        return None
    if payload.get("status") not in _TERMINAL_STATUSES:
        return None
    return payload


def _apply_result(record: dict[str, Any], result: dict[str, Any]) -> bool:
    changed = False
    for key in ("status", "exit_code", "finished_at", "error"):
        value = result.get(key)
        if record.get(key) != value:
            record[key] = value
            changed = True
    if changed:
        record["updated_at"] = time.time()
    return changed


def reconcile_job_records(
    *,
    wait_for_starting_s: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Reconcile durable records with result sidecars and live OS identities."""
    deadline = time.monotonic() + max(0.0, wait_for_starting_s)
    while True:
        with _JOURNAL_LOCK:
            journal = _load_journal()
            pending = [
                record
                for record in journal["jobs"].values()
                if isinstance(record, dict)
                and record.get("status") == "starting"
                and not _activate_from_launch(record)
            ]
            if not pending or time.monotonic() >= deadline:
                break
        time.sleep(0.02)
    with _JOURNAL_LOCK:
        journal = _load_journal()
        changed = False
        for record in journal["jobs"].values():
            if not isinstance(record, dict):
                continue
            if record.get("status") == "starting":
                if _activate_from_launch(record):
                    changed = True
                else:
                    record.update(
                        {
                            "status": "failed",
                            "exit_code": None,
                            "finished_at": time.time(),
                            "updated_at": time.time(),
                            "error": "worker did not publish its launch identity",
                        }
                    )
                    changed = True
            result = _result_for(record)
            if result is not None:
                changed = _apply_result(record, result) or changed
            elif record.get("status") == "running" and not process_matches(
                int(record.get("pid") or 0),
                str(record.get("process_identity") or "") or None,
            ):
                record.update(
                    {
                        "status": "failed",
                        "exit_code": None,
                        "finished_at": time.time(),
                        "updated_at": time.time(),
                        "error": "process disappeared before writing an exit status",
                    }
                )
                changed = True
        before = len(journal["jobs"])
        _prune_terminal_records(journal)
        changed = changed or len(journal["jobs"]) != before
        if changed:
            _atomic_json(_journal_path(), journal)
        records = [dict(record) for record in journal["jobs"].values() if isinstance(record, dict)]
        summary = {"running": 0, "completed": 0, "failed": 0, "cancelled": 0}
        for record in records:
            status = str(record.get("status") or "failed")
            summary[status if status in summary else "failed"] += 1
    return records, summary


def write_terminal_result(
    record: dict[str, Any],
    *,
    status: str,
    exit_code: int | None,
    error: str | None = None,
) -> dict[str, Any]:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"invalid terminal job status: {status}")
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "job_id": str(record["job_id"]),
        "status": status,
        "exit_code": exit_code,
        "finished_at": time.time(),
        "error": error,
    }
    _atomic_json(Path(str(record["result_path"])), payload)
    with _JOURNAL_LOCK:
        journal = _load_journal()
        stored = journal["jobs"].get(str(record["job_id"]))
        if isinstance(stored, dict):
            _apply_result(stored, payload)
            _atomic_json(_journal_path(), journal)
    return payload


class RecoveredJobProcess:
    """Popen-compatible liveness adapter for a process created by an old backend."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.pid = int(record.get("pid") or 0)
        self._job_id = str(record.get("job_id") or "")
        self._result_path = Path(str(record.get("result_path") or ""))
        self._identity = str(record.get("process_identity") or "") or None
        exit_code = record.get("exit_code")
        self.returncode = int(exit_code) if isinstance(exit_code, int) else None
        if record.get("status") in _TERMINAL_STATUSES and self.returncode is None:
            self.returncode = -1

    def poll(self) -> int | None:
        payload = _read_json(self._result_path) if self._result_path.name else None
        if payload and payload.get("job_id") == self._job_id:
            exit_code = payload.get("exit_code")
            self.returncode = int(exit_code) if isinstance(exit_code, int) else -1
            return self.returncode
        if process_matches(self.pid, self._identity):
            return None
        self.returncode = -1
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            result = self.poll()
            if result is not None:
                return result
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.05)

    def kill(self) -> None:
        if self.poll() is None:
            os.kill(self.pid, signal.SIGTERM)
