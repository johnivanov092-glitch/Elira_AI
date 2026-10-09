"""Offline release supervisor. Run from the stable platform checkout.

Candidates contain the replaceable application and private dependencies. The
supervisor never imports candidate code except in its dedicated `serve` child.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOG = logging.getLogger("elira.release")
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_CONFIRMATION_ID = re.compile(r"[a-f0-9]{32}")
_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".vite"}
_DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_NATIVE_PROFILE_DIRS = {"webview2"}  # Tauri window profile, never application databases.
_DEPENDENCY_DIRECTORIES = ("backend/.venv", "node_modules", "frontend/node_modules")
_OPTIONAL_NATIVE_DIRECTORIES = (".runtime/poppler",)
_PROGRESS_PHASES = {"idle", "preparing", "prepared", "checking", "verified", "awaiting_confirmation", "waiting", "switching",
                    "completed", "rolling_back", "failed", "interrupted"}
_PROGRESS_BUSY = {"preparing", "checking", "switching", "rolling_back"}


def saved_release_action(state: dict) -> str:
    """Publication order survives pair swaps; identifiers are not version numbers."""
    history = state.get("installed_releases")
    if (isinstance(history, list) and all(isinstance(item, str) for item in history)
            and len(history) == len(set(history))
            and state.get("active") in history and state.get("previous") in history
            and history.index(state["previous"]) > history.index(state["active"])):
        return "update"
    return "rollback"


def _process_identity(pid: int) -> str | None:
    """OS creation identity, so crash recovery never acts on a reused PID."""
    if os.name == "nt":
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        api.OpenProcess.restype = ctypes.c_void_p
        api.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FileTime)] * 4
        api.GetProcessTimes.restype = ctypes.c_int
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            values = [FileTime() for _ in range(4)]
            if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
                return None
            if values[1].low or values[1].high:
                return None
            return f"win:{(values[0].high << 32) | values[0].low}"
        finally:
            api.CloseHandle(handle)
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw[raw.rfind(")") + 2:].split()
        return None if fields[0] == "Z" else "proc:" + fields[19]
    except (OSError, IndexError):
        return None


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists() and default is not None:
        return dict(default)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid release record: {path.name}")
    return value


def _empty_progress() -> dict:
    return {"version": 1, "operation_id": None, "release_id": None, "operation": None,
            "phase": "idle", "updated_at": None, "started_at": None, "step": None,
            "error": None, "owner_pid": None, "owner_identity": None}


def _confirmation_matches(state: dict, release_id: str, sha256: str | None = None) -> bool:
    accepted = state.get("last_confirmation")
    return (isinstance(accepted, dict) and isinstance(release_id, str)
            and bool(_ID.fullmatch(release_id)) and accepted.get("release_id") == release_id
            and isinstance(accepted.get("request_id"), str)
            and bool(_CONFIRMATION_ID.fullmatch(accepted["request_id"]))
            and isinstance(accepted.get("sha256"), str)
            and bool(re.fullmatch(r"[a-f0-9]{64}", accepted["sha256"]))
            and (sha256 is None or accepted["sha256"] == sha256))


def _progress_metadata(store: Path) -> dict:
    """Best-effort bookkeeping; observer corruption cannot break the owner loop."""
    result = read_release_progress(store)
    if result["phase"] == "unavailable":
        LOG.warning("Release progress metadata is unreadable; existing lifecycle state remains authoritative")
        return {}
    return result


def read_release_progress(store: Path) -> dict:
    """Read durable owner events without constructing a manager or writing files.

    A missing journal is idle. Broken journals are unavailable; dead/reused owner
    PIDs project busy work to interrupted. Confirmation and pending waits are
    durable handoffs, not live process claims. Foundation reads inside its owner.
    """
    result = _empty_progress()
    try:
        value = _read_json(Path(store) / "progress.json", result)
        if value.get("version") != 1 or value.get("phase") not in _PROGRESS_PHASES:
            raise ValueError("Invalid release progress record")
        if value["phase"] != "idle" and (
                not isinstance(value.get("operation_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["operation_id"])
                or value.get("operation") not in {"prepare", "verify", "request", "confirm", "apply", "rollback", "recover"}
                or value.get("started_at") is None or value.get("updated_at") is None):
            raise ValueError("Incomplete release progress identity")
        for key in ("operation_id", "release_id", "operation", "error", "owner_identity"):
            if value.get(key) is not None and not isinstance(value[key], str):
                raise ValueError("Invalid release progress field")
        for key in ("started_at", "updated_at"):
            if value.get(key) is not None and (type(value[key]) not in (int, float)
                                              or not math.isfinite(value[key]) or value[key] < 0):
                raise ValueError("Invalid release progress timestamp")
        pid = value.get("owner_pid")
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError("Invalid release progress owner")
        step = value.get("step")
        if step is not None and (not isinstance(step, dict) or type(step.get("index")) is not int
                or type(step.get("total")) is not int or not 1 <= step["index"] <= step["total"]
                or not isinstance(step.get("label"), str)):
            raise ValueError("Invalid release progress step")
        result.update({key: value.get(key) for key in result})
        if step is not None:
            result["step"] = {"index": step["index"], "total": step["total"], "label": step["label"][:160]}
        if result["phase"] in _PROGRESS_BUSY and (
                pid is None or not result["owner_identity"] or _process_identity(pid) != result["owner_identity"]):
            result.update(phase="interrupted", error="Владелец обновления завершился; результат этапа не подтверждён.")
        if result["phase"] == "waiting":
            state = _read_json(Path(store) / "state.json", {})
            if (not result["release_id"] or state.get("pending") != result["release_id"]
                    or not _confirmation_matches(state, result["release_id"])):
                result.update(phase="interrupted", error="Ожидавший запрос обновления больше не выбран или не подтверждён.")
        if result["phase"] == "awaiting_confirmation":
            confirmation = _read_json(Path(store) / "state.json", {}).get("confirmation")
            if (not isinstance(confirmation, dict) or not result["release_id"]
                    or confirmation.get("release_id") != result["release_id"]
                    or confirmation.get("request_id") != result["operation_id"]):
                result.update(phase="interrupted", error="Предложение установки заменено или больше не выбрано.")
    except (OSError, ValueError, TypeError) as exc:
        result.update(phase="unavailable", error=f"Состояние обновления недоступно: {type(exc).__name__}")
    return result


@contextlib.contextmanager
def _progress_lock(store: Path):
    deadline = time.monotonic() + 2
    while True:
        lock = _lock(store / "progress.lock")
        try:
            lock.__enter__()
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _begin_progress(store: Path, operation: str, release_id: str | None, phase: str) -> str:
    operation_id = secrets.token_hex(16)
    now = time.time()
    if not isinstance(release_id, str) or not _ID.fullmatch(release_id) or release_id in {".", ".."}:
        release_id = None
    record = {**_empty_progress(), "operation_id": operation_id, "release_id": release_id,
              "operation": operation, "phase": phase, "started_at": now, "updated_at": now,
              "owner_pid": os.getpid() if phase in _PROGRESS_BUSY else None,
              "owner_identity": _process_identity(os.getpid()) if phase in _PROGRESS_BUSY else None}
    with _progress_lock(store):
        _write_json(store / "progress.json", record)
    return operation_id


def _advance_progress(store: Path, operation_id: str, phase: str, *, step: dict | None = None,
                      error: str | None = None) -> bool:
    with _progress_lock(store):
        record = _progress_metadata(store)
        if record.get("operation_id") != operation_id:
            return False  # A newer operation owns the visible event stream.
        record.update(phase=phase, step=step, error=str(error)[:2000] if error is not None else None,
                      updated_at=time.time(), owner_pid=os.getpid() if phase in _PROGRESS_BUSY else None,
                      owner_identity=_process_identity(os.getpid()) if phase in _PROGRESS_BUSY else None)
        _write_json(store / "progress.json", record)
        return True


@contextlib.contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _contained(path: Path, boundary: Path) -> Path:
    boundary = boundary.resolve()
    if not path.resolve().is_relative_to(boundary):
        raise ValueError(f"Path leaves its owned directory: {path.name}")
    current = path
    while current != boundary and current.is_relative_to(boundary):
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if current.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"Linked/reparse paths cannot belong to a release snapshot: {current.name}")
        current = current.parent
    return path


def _tree_files(root: Path, *, excluded: set[str] | None = None):
    if not root.is_dir():
        raise ValueError(f"Missing release directory: {root}")
    _contained(root, root.parent)
    excluded = excluded or set()
    def walk_error(error: OSError) -> None:
        raise error
    for folder, directories, names in os.walk(root, onerror=walk_error):
        kept = []
        for name in sorted(directories):
            path = Path(folder) / name
            if name in _CACHE_DIRS or path.relative_to(root).as_posix() in excluded:
                continue
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError(f"Linked/reparse directories cannot belong to a release snapshot: {path}")
            kept.append(name)
        directories[:] = kept
        for name in sorted(names):
            if not name.endswith((".pyc", ".pyo")):
                path = Path(folder) / name
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    raise ValueError(f"Release files must be regular files: {path}")
                yield path


def _digest(paths: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for label, path in sorted(paths):
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f"Release receipt must seal a regular file: {label}")
        encoded_label = label.encode("utf-8")
        content_digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content_digest.update(chunk)
        digest.update(len(encoded_label).to_bytes(8, "big"))
        digest.update(encoded_label)
        digest.update(content_digest.digest())
    return digest.hexdigest()


def _detach_build_artifact(root: Path, executable: str) -> None:
    """Give Cargo's linked desktop output its own inode before sealing it."""
    artifact = _contained(root / executable, root)
    info = artifact.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Desktop build output must be a regular file")
    if info.st_nlink == 1:
        return
    before = _digest([(executable, artifact)])
    descriptor, name = tempfile.mkstemp(prefix=".elira-desktop-", suffix=".tmp", dir=artifact.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output, artifact.open("rb") as source:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(artifact, temporary)
        if (_digest([(executable, temporary)]) != before
                or _digest([(executable, artifact)]) != before):
            raise ValueError("Desktop build output changed while detaching its hard links")
        _contained(artifact, root)
        os.replace(temporary, artifact)
        if artifact.stat().st_nlink != 1:
            raise ValueError("Desktop build output still has hard-link aliases")
        LOG.info("Detached hard-linked desktop build output: %s", executable)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_databases(data: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    names = []
    for source in sorted(_tree_files(data, excluded=_NATIVE_PROFILE_DIRS)) if data.exists() else []:
        if not source.is_file() or source.suffix not in _DB_SUFFIXES:
            continue
        relative = source.relative_to(data)
        target = _contained(destination / relative, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with contextlib.closing(sqlite3.connect(target)) as backup:
                original.backup(backup)
        names.append(relative.as_posix())
    _write_json(destination / "snapshot.json", {"databases": names})


def _restore_databases(data: Path, snapshot: Path) -> None:
    names = _read_json(snapshot / "snapshot.json")["databases"]
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("Invalid database snapshot inventory")
    # Older snapshots included Chromium SQLite files. Keep the current profile
    # even when restoring one of those snapshots; an open WebView owns its locks.
    names = [name for name in names if not Path(name).parts or Path(name).parts[0].casefold() not in _NATIVE_PROFILE_DIRS]
    for name in names:
        _contained(data / name, data)
        _contained(snapshot / name, snapshot)
    known = set(names)
    current_files = list(_tree_files(data, excluded=_NATIVE_PROFILE_DIRS))
    for source in current_files:
        if source.is_file() and source.suffix in _DB_SUFFIXES and source.relative_to(data).as_posix() not in known:
            source.unlink()  # Created during failed, unadmitted startup only.
            for suffix in ("-wal", "-shm", "-journal"):
                _contained(Path(str(source) + suffix), data).unlink(missing_ok=True)
    for name in names:
        target = _contained(data / name, data)
        target.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            _contained(Path(str(target) + suffix), data).unlink(missing_ok=True)
        temporary = _contained(target.with_name(target.name + ".release-restore"), data)
        shutil.copy2(snapshot / name, temporary)
        os.replace(temporary, target)


class LocalProcessHost:
    """The legacy, same-user host. Foundation injects its checked user-token host."""

    def user_environment(self) -> dict:
        return os.environ.copy()

    def process_identity(self, pid: int) -> str | None:
        return _process_identity(pid)

    def run(self, arguments, *, cwd, env=None, log=None, timeout=None):
        return subprocess.run(arguments, cwd=cwd, env=env, check=True, timeout=timeout,
                              stdout=log, stderr=subprocess.STDOUT if log else None,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def popen(self, arguments, *, cwd, env=None, log=None, desktop=False):
        return subprocess.Popen(arguments, cwd=cwd, env=env, stdout=log,
                                stderr=subprocess.STDOUT if log else None,
                                creationflags=0 if desktop else getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def terminate_owned(self, pid: int, identity: str) -> None:
        if self.process_identity(pid) == identity:
            os.kill(pid, signal.SIGTERM)


class ReleaseLayout:
    """Explicit authorities: private records, editable sources, sealed runtime, user data."""

    def __init__(self, *, store: Path, candidates: Path, data: Path, journals: Path,
                 config_root: Path, published: Path | None = None, worker_root: Path | None = None,
                 worker_python: Path | None = None, worker_script: Path | None = None,
                 service_name: str = "EliraFoundation"):
        for name, value in locals().copy().items():
            if name not in {"self", "service_name"}:
                setattr(self, name, Path(value).resolve() if value is not None else None)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", service_name):
            raise ValueError("Invalid Foundation service name")
        self.service_name = service_name
        self.published = self.published or self.candidates

    @property
    def protected(self) -> bool:
        return self.published != self.candidates


class ReleaseManager:
    def __init__(self, platform: Path, *, port: int = 8000, startup_timeout: float = 90,
                 publish_processes: bool = True, layout: ReleaseLayout | None = None,
                 host=None, storage=None):
        self.platform = platform.resolve()
        self.layout = layout or ReleaseLayout(
            store=self.platform / ".runtime/releases", candidates=self.platform / ".runtime/releases/candidates",
            data=Path(os.getenv("ELIRA_DATA_DIR") or self.platform / "data"),
            journals=Path(os.getenv("ELIRA_AGENT_RUNS_DIR") or self.platform / ".agent/runs"),
            config_root=self.platform / "backend")
        self.host = host if host is not None else LocalProcessHost()
        self.storage = storage
        if self.layout.protected:
            if host is None or type(host) is LocalProcessHost or storage is None:
                raise ValueError("Protected releases require an explicit user process host and secure storage")
            if not all((self.layout.worker_root, self.layout.worker_python, self.layout.worker_script)):
                raise ValueError("Protected releases require explicit user worker root and protected Python/script")
        self.store = self.layout.store
        self.data = self.layout.data
        self.journals = self.layout.journals
        self.port = port
        self.startup_timeout = startup_timeout
        self.publish_processes = publish_processes
        self.backend: subprocess.Popen | None = None
        self.ui: subprocess.Popen | None = None
        self.token = secrets.token_urlsafe(32)
        self.instance = secrets.token_hex(16)
        self.store.mkdir(parents=True, exist_ok=True)

    def path(self, release_id: str) -> Path:
        self._validate_id(release_id)
        return _contained(self.layout.published / release_id, self.layout.published)

    @staticmethod
    def _validate_id(release_id: str) -> None:
        if not _ID.fullmatch(release_id) or release_id in {".", ".."}:
            raise ValueError("Release id must contain only lowercase letters, digits, dots, dashes or underscores")

    def candidate_path(self, release_id: str) -> Path:
        self._validate_id(release_id)
        if self.layout.protected:
            with self.storage.user_access():
                return _contained(self.layout.candidates / release_id, self.layout.candidates)
        return self.path(release_id)

    def owned(self, relative: str) -> Path:
        return _contained(self.store / relative, self.store)

    def record(self, release_id: str) -> Path:
        self.path(release_id)
        return self.owned(f"records/{release_id}.json")

    def state(self) -> dict:
        return _read_json(self.owned("state.json"), {
            "active": None, "previous": None, "pending": None, "transition": None,
        })

    def _save(self, state: dict) -> None:
        _write_json(self.owned("state.json"), state)

    @staticmethod
    def _python(root: Path) -> Path:
        return root / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _npm(self) -> str:
        result = shutil.which("npm.cmd" if os.name == "nt" else "npm", path=self.host.user_environment().get("PATH", ""))
        if not result:
            raise RuntimeError("npm is required to verify a complete application release")
        return result

    def _candidate_provenance(self, release_id: str) -> dict:
        record = _read_json(self.record(release_id), {})
        return {key: record[key] for key in ("base_release_id", "base_sha256", "base_runtime", "source_root")
                if key in record}

    def _command(self, arguments: list[str], *, cwd: Path, env: dict | None = None,
                 log=None, timeout=None) -> subprocess.CompletedProcess:
        result = self.host.run(arguments, cwd=cwd, env=env, log=log, timeout=timeout)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, arguments)
        return result

    def prepare(self, release_id: str) -> dict:
        operation_id = _begin_progress(self.store, "prepare", release_id, "preparing")
        try:
            result = self._prepare(release_id)
        except Exception as exc:
            _advance_progress(self.store, operation_id, "failed", error=str(exc))
            raise
        _advance_progress(self.store, operation_id, "prepared")
        return result

    def _prepare(self, release_id: str) -> dict:
        root = self.candidate_path(release_id)
        active = self.state().get("active")
        source = self.path(active) if active else self.platform
        base_release_id, base_sha256, base_runtime = active, None, "foundation" if self.layout.protected else "legacy"
        if active:
            base_sha256 = self.checked(active)["sha256"]
        elif self.layout.protected:
            # Bootstrap from the selected, sealed legacy application rather
            # than an unrelated platform HEAD. Read untrusted legacy metadata
            # as the user; never execute its code in the LocalService process.
            with self.storage.user_access():
                legacy_store = self.platform / ".runtime/releases"
                legacy = _read_json(legacy_store / "state.json", {})
                if legacy.get("pending") or legacy.get("transition"):
                    raise ValueError("Finish the legacy release transition before Foundation bootstrap")
                legacy_id = legacy.get("active")
                if legacy_id:
                    self._validate_id(legacy_id)
                    source = _contained(legacy_store / "candidates" / legacy_id, legacy_store / "candidates")
                    receipt = _read_json(_contained(legacy_store / "records" / (legacy_id + ".json"), legacy_store))
                    if (receipt.get("status") != "verified" or receipt.get("release_id") != legacy_id
                            or receipt.get("root") != str(source)):
                        raise ValueError("Legacy source does not have a matching verified receipt")
                    executable = receipt.get("executable")
                    if not isinstance(executable, str) or not executable or Path(executable).is_absolute():
                        raise ValueError("Invalid legacy desktop executable")
                    _contained(source / executable, source)
                    base_sha256 = self.fingerprint(source, executable=executable)
                    if base_sha256 != receipt.get("sha256"):
                        raise ValueError("Legacy source changed after verification; bootstrap was not prepared")
                    base_release_id, base_runtime = legacy_id, "legacy"
        if self.layout.protected:
            work = self._work_path("prepare")
            try:
                self._worker("prepare", root=root, source=source, work=work, overlay=bool(base_release_id))
            finally:
                self._worker("cleanup", work=work)
        else:
            self._prepare_files(root, source, overlay=bool(active))
        value = {"release_id": release_id, "status": "candidate", "root": str(root),
                 "candidate_root": str(root), "python": str(self._python(root)),
                 "base_release_id": base_release_id, "base_sha256": base_sha256,
                 "base_runtime": base_runtime, "source_root": str(source), "platform_root": str(self.platform)}
        _write_json(self.record(release_id), value)
        return value

    def _prepare_files(self, root: Path, source: Path, *, overlay: bool) -> None:
        if root.exists():
            raise ValueError("Candidate already exists; continue editing that candidate or choose a new id")
        root.parent.mkdir(parents=True, exist_ok=True)
        # Start from the active release, so successive improvements accumulate.
        # Published snapshots have no Git administration files. The platform Git
        # supplies history; the exact published source is overlaid below.
        git_source = source if (source / ".git").exists() else self.platform
        self._command(["git", "clone", "--local", "--no-hardlinks", str(git_source), str(root)], cwd=self.platform)
        self._command(["git", "remote", "remove", "origin"], cwd=root)
        if overlay:
            files = dict(self._source_files(source))
            for name, path in self._source_files(root):
                if name not in files:
                    path.unlink()  # New candidate only; removed source stays removed.
            for name, path in files.items():
                target = _contained(root / name, root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        for relative in _DEPENDENCY_DIRECTORIES + _OPTIONAL_NATIVE_DIRECTORIES:
            dependency = _contained(source / relative, source)
            if relative in _OPTIONAL_NATIVE_DIRECTORIES and not dependency.exists():
                continue
            if not dependency.is_dir():
                raise ValueError(f"Install the platform dependency environment first: {relative}")
            # Validate redirections before copying; do not flatten a junction
            # into an apparently independent candidate environment.
            for _ in _tree_files(dependency):
                pass
            shutil.copytree(dependency, _contained(root / relative, root),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        scripts = self._python(root).parent
        source_env = str(source / "backend" / ".venv")
        old_paths = {source_env.encode().lower(), source_env.replace("\\", "/").encode().lower()}
        # Remove path-bound wrappers, including stale pip3/versioned and
        # unregistered launchers. Known entry points are regenerated below.
        # Native auxiliary executables without an old environment path remain.
        for file in scripts.iterdir():
            if file.is_file() and file.name.lower() not in {"python.exe", "pythonw.exe", "python", "python3"}:
                content = file.read_bytes().lower()
                if any(old in content for old in old_paths):
                    file.unlink()
        # Recreate activation scripts/pyvenv.cfg without touching copied packages.
        # Console launchers embed an absolute Python path, so reinstall their
        # entry points offline using the already installed pip/distlib.
        self._command([str(self._python(source)), "-m", "venv", "--without-pip",
                       str(root / "backend/.venv")], cwd=root)
        relaunchers = (
            "import importlib.metadata,sys,sysconfig; "
            "from pip._vendor.distlib.scripts import ScriptMaker; "
            "m=ScriptMaker(None,sysconfig.get_path('scripts')); "
            "m.executable=sys.executable; m.clobber=True; m.variants={''}; "
            "[m.make(e.name+'='+e.value,options={'gui':e.group=='gui_scripts'}) "
            "for d in importlib.metadata.distributions() for e in d.entry_points "
            "if e.group in {'console_scripts','gui_scripts'}]"
        )
        self._command([str(self._python(root)), "-c", relaunchers], cwd=root)

    def _work_path(self, purpose: str) -> Path:
        return self.layout.worker_root / (purpose + "-" + secrets.token_hex(16))

    def _worker(self, operation: str, *, log=None, **values) -> None:
        arguments = [str(self.layout.worker_python), "-I", "-S", "-B", str(self.layout.worker_script),
                     "--platform", str(self.platform), "worker", operation,
                     "--worker-root", str(self.layout.worker_root)]
        for key, value in values.items():
            arguments.extend(["--" + key.replace("_", "-"), "1" if value is True else "0" if value is False else str(value)])
        if log is not None:
            self._command(arguments, cwd=self.layout.worker_script.parent,
                          env=self.host.user_environment(), log=log)
        else:
            worker_log = self.owned("logs/worker-" + secrets.token_hex(16) + ".log")
            worker_log.parent.mkdir(parents=True, exist_ok=True)
            with worker_log.open("xb") as output:
                try:
                    self._command(arguments, cwd=self.layout.worker_script.parent,
                                  env=self.host.user_environment(), log=output)
                except Exception as exc:
                    raise RuntimeError(f"Release worker {operation} failed; log: {worker_log}") from exc

    def _snapshot(self, destination: Path) -> None:
        if not self.layout.protected:
            _snapshot_databases(self.data, destination)
            return
        work = self._work_path("backup")
        try:
            self._worker("snapshot", data=self.data, destination=work / "snapshot", work=work)
            self.storage.publish(work / "snapshot", destination)
        finally:
            self._worker("cleanup", work=work)

    def _restore(self, snapshot: Path) -> None:
        if not self.layout.protected:
            _restore_databases(self.data, snapshot)
            return
        work = self._work_path("restore")
        try:
            self.storage.export(snapshot, work / "snapshot")
            self._worker("restore", data=self.data, source=work / "snapshot", work=work)
        finally:
            self._worker("cleanup", work=work)

    @staticmethod
    def _source_files(root: Path) -> list[tuple[str, Path]]:
        # Runtime code may be ignored by Git. Seal the actual source tree rather
        # than trusting Git's inventory or an agent-supplied file manifest.
        excluded = {".git", ".runtime", ".agent", ".scratch", "data", "backend/data",
                    "backend/.venv", "node_modules", "frontend/node_modules", "frontend/dist",
                    "src-tauri/target", "src-tauri/gen", "target"}
        return [(path.relative_to(root).as_posix(), path) for path in _tree_files(root, excluded=excluded)]

    def _fingerprint_files(self, root: Path, *, executable: str = "") -> list[tuple[str, Path]]:
        paths = self._source_files(root)
        for relative in _DEPENDENCY_DIRECTORIES + _OPTIONAL_NATIVE_DIRECTORIES:
            dependency = _contained(root / relative, root)
            if relative in _OPTIONAL_NATIVE_DIRECTORIES and not dependency.exists():
                continue
            paths.extend((file.relative_to(root).as_posix(), file) for file in _tree_files(dependency))
        if executable:
            paths.extend((file.relative_to(root).as_posix(), file) for file in _tree_files(root / "frontend/dist"))
            paths.append((executable, _contained(root / executable, root)))
        return paths

    def fingerprint(self, root: Path, *, executable: str = "") -> str:
        return _digest(self._fingerprint_files(root, executable=executable))

    def _verification_commands(self, root: Path) -> list[list[str]]:
        return [
            [self._npm(), "--prefix", "frontend", "run", "typecheck"],
            [self._npm(), "--prefix", "frontend", "run", "build"],
            [str(self._python(root)), "-m", "pytest", "-q", "backend/tests"],
            [self._npm(), "run", "tauri", "--", "build", "--no-bundle"],
        ]

    def _find_executable(self, root: Path) -> str:
        candidates = [file for file in (root / "src-tauri/target/release").glob("*.exe")
                      if file.name.casefold() in {"elira-desktop.exe", "elira ai.exe"}]
        if len(candidates) != 1:
            raise ValueError("A complete release must have exactly one freshly built Elira desktop executable")
        return candidates[0].relative_to(root).as_posix()

    def _environment(self, release_id: str, *, data: Path | None = None, root: Path | None = None) -> dict:
        env = dict(self.host.user_environment())
        candidate = root or self.path(release_id)
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env.update({"ELIRA_RELEASE_ID": release_id, "ELIRA_RELEASE_STAGING": "1",
                    "ELIRA_RELEASE_TOKEN": self.token, "ELIRA_RELEASE_INSTANCE": self.instance,
                    "ELIRA_CONFIG_ROOT": str(self.layout.config_root),
                    "ELIRA_PLATFORM_ROOT": str(self.platform),
                    "ELIRA_DATA_DIR": str(data or self.data),
                    "ELIRA_AGENT_RUNS_DIR": str(self.journals if data is None else data / "agent-runs"),
                    "ELIRA_EXTERNAL_BACKEND": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        env["VIRTUAL_ENV"] = str(candidate / "backend/.venv")
        env["PATH"] = os.pathsep.join((str(self._python(candidate).parent),
                                      str(candidate / "node_modules/.bin"),
                                      str(candidate / "frontend/node_modules/.bin"), env.get("PATH", "")))
        if self.layout.protected:
            env.update({"ELIRA_FOUNDATION_MANAGED": "1", "ELIRA_FOUNDATION_SERVICE": self.layout.service_name,
                        "ELIRA_FOUNDATION_PYTHON": str(self.layout.worker_python),
                        "ELIRA_FOUNDATION_CLIENT": str(self.layout.worker_script.parent / "foundation_client.py")})
        else:
            for name in ("ELIRA_FOUNDATION_MANAGED", "ELIRA_FOUNDATION_SERVICE", "ELIRA_FOUNDATION_PYTHON", "ELIRA_FOUNDATION_CLIENT"):
                env.pop(name, None)
            env.setdefault("ELIRA_FS_UNRESTRICTED", "1")
        if data is not None:
            env.update({"ELIRA_DRIFT_CHECK": "0", "LLAMA_SERVER_ENABLED": "false",
                        "LOCAL_EMBED_ENABLED": "false"})
        return env

    def verify(self, release_id: str) -> dict:
        operation_id = _begin_progress(self.store, "verify", release_id, "checking")
        try:
            result = self._verify(release_id, operation_id=operation_id)
        except Exception as exc:
            _advance_progress(self.store, operation_id, "failed", error=str(exc))
            raise
        _advance_progress(self.store, operation_id, "verified")
        return result

    def _verify(self, release_id: str, *, operation_id: str) -> dict:
        state = self.state()
        if release_id in {state.get("active"), state.get("previous"), state.get("pending")}:
            raise ValueError("Selected release IDs are immutable; prepare a new candidate ID before verification")
        if self.layout.protected:
            return self._verify_protected(release_id, operation_id=operation_id)
        root = self.path(release_id)
        before = self.fingerprint(root)
        value = {**self._candidate_provenance(release_id), "release_id": release_id,
                 "status": "verifying", "root": str(root)}
        _write_json(self.record(release_id), value)
        self.owned("logs").mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="elira-release-check-") as name:
                check_data = Path(name) / "data"
                _snapshot_databases(self.data, check_data)
                env = self._environment(release_id, data=check_data)
                # Tests must exercise normal task admission. Their data is
                # isolated; the subsequent real startup probe remains staged.
                env["ELIRA_RELEASE_STAGING"] = "0"
                with self.owned(f"logs/{release_id}-verify.log").open("w", encoding="utf-8") as log:
                    commands = self._verification_commands(root)
                    for index, command in enumerate(commands, 1):
                        self._verification_progress(operation_id, index, len(commands) + 2, command)
                        LOG.info("Verifying %s: %s", release_id, command[1:])
                        self._command(command, cwd=root, env=env, log=log)
                self._verification_progress(operation_id, len(commands) + 1, len(commands) + 2, label="Проверка целостности")
                if self.fingerprint(root) != before:
                    raise ValueError("Candidate code or dependencies changed during verification; verify the final version")
                executable = self._find_executable(root)
                _detach_build_artifact(root, executable)
                value.update({"status": "verified", "executable": executable,
                              "sha256": self.fingerprint(root, executable=executable),
                              "verified_at": time.time()})
                # Exercise actual candidate imports/startup against copied data,
                # with schedulers and user requests held until admission.
                probe = type(self)(self.platform, port=_free_port(), startup_timeout=self.startup_timeout,
                                   publish_processes=False, layout=self.layout, host=self.host, storage=self.storage)
                self._verification_progress(operation_id, len(commands) + 2, len(commands) + 2, label="Проверка запуска")
                try:
                    probe._start_backend(release_id, data=check_data)
                finally:
                    probe._stop_backend()
                if self.fingerprint(root, executable=executable) != value["sha256"]:
                    raise ValueError("Candidate runtime changed during staged startup")
            _write_json(self.record(release_id), value)
            return value
        except Exception as exc:
            value.update({"status": "failed", "error": str(exc)})
            _write_json(self.record(release_id), value)
            raise

    def _verification_progress(self, operation_id: str, index: int, total: int,
                               command: list[str] | None = None, *, label: str | None = None) -> None:
        if label is None:
            parts = command or []
            label = ("Проверка типов" if "typecheck" in parts else "Тесты backend" if "pytest" in parts
                     else "Сборка приложения" if "tauri" in parts else "Сборка интерфейса" if "build" in parts
                     else "Проверка кандидата")
        _advance_progress(self.store, operation_id, "checking", step={"index": index, "total": total, "label": label})

    def _verify_protected(self, release_id: str, *, operation_id: str) -> dict:
        root, published = self.candidate_path(release_id), self.path(release_id)
        if published.exists():
            raise ValueError("Published release IDs are immutable; prepare a new candidate ID")
        with self.storage.user_access():
            before = self.fingerprint(root)
        work = self._work_path("verify")
        value = {**self._candidate_provenance(release_id), "release_id": release_id, "status": "verifying", "root": str(published),
                 "candidate_root": str(root), "verification_work": str(work)}
        _write_json(self.record(release_id), value)
        self.owned("logs").mkdir(exist_ok=True)
        check_data = work / "data"
        restore_pending = False
        try:
            self._worker("snapshot", data=self.data, destination=check_data, work=work)
            env = self._environment(release_id, data=check_data, root=root)
            env["ELIRA_RELEASE_STAGING"] = "0"
            with self.owned(f"logs/{release_id}-verify.log").open("w", encoding="utf-8") as log:
                with self.storage.user_access():
                    commands = self._verification_commands(root)
                for index, command in enumerate(commands, 1):
                    self._verification_progress(operation_id, index, len(commands) + 2, command)
                    LOG.info("Verifying %s: %s", release_id, command[1:])
                    self._command(command, cwd=root, env=env, log=log)
            self._verification_progress(operation_id, len(commands) + 1, len(commands) + 2, label="Проверка и публикация целостного релиза")
            with self.storage.user_access():
                if self.fingerprint(root) != before:
                    raise ValueError("Candidate code or dependencies changed during verification; verify the final version")
                executable = self._find_executable(root)
                _detach_build_artifact(root, executable)
            try:
                restore_pending = True
                self._worker("relocate", root=root, destination=published, backup=work / "environment", work=work)
                with self.storage.user_access():
                    files = sorted({name for name, _ in self._fingerprint_files(root, executable=executable)})
                    copied_hash = self.fingerprint(root, executable=executable)
                self.storage.publish(root, published, files=files)
                if self.fingerprint(published, executable=executable) != copied_hash:
                    raise ValueError("Published bytes do not match the observed candidate snapshot")
            finally:
                self._worker("restore-environment", root=root, backup=work / "environment", work=work)
                restore_pending = False
            with self.storage.user_access():
                if self.fingerprint(root) != before:
                    raise ValueError("Candidate environment restoration did not reproduce its original bytes")
            probe = type(self)(self.platform, port=_free_port(), startup_timeout=self.startup_timeout,
                               publish_processes=False, layout=self.layout, host=self.host, storage=self.storage)
            self._verification_progress(operation_id, len(commands) + 2, len(commands) + 2, label="Проверка запуска")
            try:
                probe._start_backend(release_id, data=check_data)
            finally:
                probe._stop_backend()
            if self.fingerprint(published, executable=executable) != copied_hash:
                raise ValueError("Published runtime changed during staged startup")
            value.update(status="verified", executable=executable, sha256=copied_hash, verified_at=time.time())
            _write_json(self.record(release_id), value)
            return value
        except Exception as exc:
            value.update(status="failed", error=str(exc))
            _write_json(self.record(release_id), value)
            raise
        finally:
            if not restore_pending:
                self._worker("cleanup", work=work)

    def checked(self, release_id: str) -> dict:
        value = _read_json(self.record(release_id))
        if value.get("status") != "verified":
            raise ValueError("Candidate has not passed the release checks")
        if self.fingerprint(self.path(release_id), executable=value["executable"]) != value["sha256"]:
            raise ValueError("Candidate changed after verification; run verify again")
        return value

    def request(self, release_id: str, *, operation: str = "request",
                expected_active: str | None = None, expected_previous: str | None = None) -> dict:
        with _lock(self.owned("state.lock")):
            state = self.state()
            if expected_active is not None or expected_previous is not None:
                if (operation != "rollback" or not expected_active or not expected_previous
                        or release_id != expected_previous or expected_active == expected_previous
                        or state.get("active") != expected_active or state.get("previous") != expected_previous):
                    raise ValueError("Rollback selection changed; refresh the application release status")
            if state.get("active") == release_id and not state.get("transition"):
                self.checked(release_id)
                return state
            if state.get("pending"):
                raise ValueError("An explicitly confirmed installation is still pending")
            operation_id = _begin_progress(self.store, operation, release_id, "checking")
            try:
                candidate = self.checked(release_id)
                if state.get("transition"):
                    raise ValueError("Another release transition is still in progress")
                state.update(pending=None, confirmation={"request_id": operation_id,
                    "release_id": release_id, "sha256": candidate["sha256"], "requested_at": time.time()})
                state.pop("error", None)
                self._save(state)
                _advance_progress(self.store, operation_id, "awaiting_confirmation")
            except Exception as exc:
                _advance_progress(self.store, operation_id, "failed", error=str(exc))
                raise
            return state

    def confirm(self, request_id: str) -> dict:
        """Approve only the displayed proposal and its verified immutable bytes."""
        if not isinstance(request_id, str) or not _CONFIRMATION_ID.fullmatch(request_id):
            raise ValueError("Invalid confirmation request_id")
        with _lock(self.owned("state.lock")):
            state = self.state()
            proposal = state.get("confirmation")
            if not isinstance(proposal, dict) or proposal.get("request_id") != request_id:
                accepted = state.get("last_confirmation")
                if (proposal is None and isinstance(accepted, dict) and accepted.get("request_id") == request_id
                        and accepted.get("release_id") in {state.get("pending"), state.get("active")}):
                    candidate = self.checked(accepted["release_id"])
                    self._check_approval(state, accepted["release_id"], candidate["sha256"])
                    return state  # Replaying an accepted click never approves another proposal.
                raise ValueError("Confirmation is stale or no longer selected")
            if state.get("transition"):
                raise ValueError("Another release transition is still in progress")
            release_id = proposal.get("release_id")
            previous_progress = _progress_metadata(self.store)
            operation = "rollback" if (previous_progress.get("operation_id") == request_id
                                        and previous_progress.get("operation") == "rollback") else "confirm"
            operation_id = _begin_progress(self.store, operation, release_id, "checking")
            try:
                candidate = self.checked(release_id)
                if candidate["sha256"] != proposal.get("sha256"):
                    raise ValueError("Candidate changed since the installation proposal; request it again")
                state.update(pending=release_id, confirmation=None, last_confirmation={
                    "request_id": request_id, "release_id": release_id, "sha256": candidate["sha256"]})
                state.pop("error", None)
                self._save(state)
                self.owned("enabled").touch()
                _advance_progress(self.store, operation_id, "waiting")
            except Exception as exc:
                _advance_progress(self.store, operation_id, "failed", error=str(exc))
                raise
            return state

    @staticmethod
    def _check_approval(state: dict, release_id: str, sha256: str) -> None:
        if not _confirmation_matches(state, release_id, sha256):
            raise ValueError("Pending release does not match an explicitly confirmed installation proposal")

    def _http(self, action: str | None = None) -> dict:
        path = "/api/release/control" if action else "/health"
        request = Request(f"http://127.0.0.1:{self.port}{path}",
                          data=json.dumps({"action": action}).encode() if action else None,
                          headers={"Content-Type": "application/json", "x-elira-release-token": self.token})
        with urlopen(request, timeout=5) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Invalid backend lifecycle response")
        return result

    def _backend_command(self, release_id: str) -> list[str]:
        script = self.layout.worker_script or self.platform / "scripts/elira_release.py"
        return [str(self._python(self.path(release_id))), str(script),
                "--platform", str(self.platform), "--port", str(self.port),
                "--runtime-root", str(self.path(release_id)), "serve", release_id]

    def _save_processes(self) -> None:
        if self.publish_processes:
            owned = {"port": self.port, "token": self.token, "instance": self.instance}
            for name, process in (("backend", self.backend), ("ui", self.ui)):
                if process is not None and process.poll() is None:
                    identity = getattr(process, "creation_identity", None) or self.host.process_identity(process.pid)
                    if identity is None:
                        raise RuntimeError(f"Cannot identify owned {name} process")
                    owned[name] = {"pid": process.pid, "identity": identity}
            _write_json(self.owned("live.json"), owned)

    def _reap_owned_processes(self) -> None:
        saved = _read_json(self.owned("live.json"), {})
        owned = {name: item for name in ("backend", "ui")
                 if isinstance(item := saved.get(name), dict)
                 and self.host.process_identity(int(item["pid"])) == item.get("identity")}
        if not owned:
            return
        prior_token, prior_instance = self.token, self.instance
        self.token, self.instance = saved["token"], saved["instance"]
        try:
            if "backend" in owned:
                health = self._http()
                if health.get("instance_id") != self.instance:
                    raise RuntimeError("Owned backend identity does not match the recovery record")
                if not self._http("drain").get("idle"):
                    self._http("resume")
                    raise RuntimeError("An owned backend still has active work; recovery will wait for task completion")
                self._http("shutdown")
            for name, item in owned.items():
                pid, identity = int(item["pid"]), item["identity"]
                if name == "ui" and self.host.process_identity(pid) == identity:
                    self.host.terminate_owned(pid, identity)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and self.host.process_identity(pid) == identity:
                    time.sleep(0.1)
                if self.host.process_identity(pid) == identity:
                    raise RuntimeError(f"Owned {name} did not exit; databases have not been restored")
        finally:
            self.token, self.instance = prior_token, prior_instance

    def _start_backend(self, release_id: str, *, data: Path | None = None) -> None:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                raise RuntimeError(f"Port {self.port} is already owned; no process was stopped")
        logs = self.owned("logs")
        logs.mkdir(exist_ok=True)
        with self.owned(f"logs/{release_id}-backend.log").open("ab") as log:
            self.backend = self.host.popen(self._backend_command(release_id), cwd=self.path(release_id) / "backend",
                                           env=self._environment(release_id, data=data), log=log)
        self._save_processes()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            exit_code = self.backend.poll()
            if exit_code is not None:
                raise RuntimeError(f"Candidate backend exited with code {exit_code}")
            try:
                health = self._http()
                if (health.get("service") == "elira-ai-api" and health.get("release_id") == release_id
                        and health.get("instance_id") == self.instance and health.get("draining") is True):
                    return
            except (OSError, URLError, ValueError):
                pass
            time.sleep(0.2)
        raise RuntimeError("Candidate startup health deadline exceeded; see the release backend log")

    def _start_ui(self, release_id: str, executable: str) -> None:
        env = self._environment(release_id)
        env.pop("ELIRA_RELEASE_TOKEN", None)
        self.ui = self.host.popen([str(self.path(release_id) / executable)], cwd=self.path(release_id),
                                  env=env, desktop=True)
        self._save_processes()
        time.sleep(0.5)
        if self.ui.poll() is not None:
            raise RuntimeError("Candidate desktop exited during startup")

    def _stop_ui(self) -> None:
        try:
            if self.ui is not None and self.ui.poll() is None:
                self.ui.terminate()
                try:
                    self.ui.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.ui.kill()
                    self.ui.wait(timeout=10)
        finally:
            self._close_exited_process("ui")

    def _stop_backend(self) -> None:
        try:
            if self.backend is not None and self.backend.poll() is None:
                try:
                    self._http("shutdown")
                    self.backend.wait(timeout=15)
                except (OSError, URLError, ValueError, subprocess.TimeoutExpired):
                    self.backend.terminate()
                    self.backend.wait(timeout=15)
        finally:
            self._close_exited_process("backend")

    def _close_exited_process(self, name: str) -> None:
        process = getattr(self, name)
        try:
            if process is not None and process.poll() is not None:
                setattr(self, name, None)
                close = getattr(process, "close", None)
                if callable(close):
                    close()
        finally:
            self._save_processes()

    def apply_pending(self) -> bool:
        state = self.state()
        release_id = state.get("pending")
        if not release_id:
            return False
        progress = _progress_metadata(self.store)
        if progress.get("release_id") == release_id and progress.get("phase") == "waiting":
            operation_id = progress["operation_id"]
        else:
            operation_id = _begin_progress(self.store, "apply", release_id, "waiting")
        try:
            return self._apply_pending(release_id, operation_id=operation_id)
        except Exception as exc:
            _advance_progress(self.store, operation_id, "failed", error=str(exc))
            raise

    def _apply_pending(self, release_id: str, *, operation_id: str) -> bool:
        progress = _progress_metadata(self.store)
        switching = "rolling_back" if progress.get("operation") == "rollback" else "switching"
        try:
            candidate = self.checked(release_id)
            self._check_approval(self.state(), release_id, candidate["sha256"])
        except (OSError, ValueError) as exc:
            with _lock(self.owned("state.lock")):
                state = self.state()
                if state.get("pending") == release_id:
                    state.update({"pending": None, "error": str(exc)})
                    self._save(state)
            LOG.error("Pending release rejected without interrupting the active application: %s", exc)
            _advance_progress(self.store, operation_id, "failed", error=str(exc))
            return False
        if self.backend is not None:
            if not self._http("drain").get("idle"):
                self._http("resume")
                _advance_progress(self.store, operation_id, "waiting")
                return False
        with _lock(self.owned("state.lock")):
            state = self.state()
            if state.get("pending") != release_id:
                if self.backend is not None:
                    self._http("resume")
                _advance_progress(self.store, operation_id, "interrupted", error="Запрос обновления заменён до переключения.")
                return False
            self._check_approval(state, release_id, candidate["sha256"])
            previous = state.get("active")
            backup = self.owned("backups/" + secrets.token_hex(12))
            state["transition"] = {"from": previous, "to": release_id, "phase": "stopping", "backup": str(backup)}
            self._save(state)
            _advance_progress(self.store, operation_id, switching,
                              step={"index": 1, "total": 4, "label": "Завершение текущей работы"})
            self._stop_ui()
            self._stop_backend()
            try:
                self.checked(release_id)
                _advance_progress(self.store, operation_id, switching,
                                  step={"index": 2, "total": 4, "label": "Сохранение пользовательских данных"})
                self._snapshot(backup)
                state["transition"]["phase"] = "switching"
                self._save(state)
                _advance_progress(self.store, operation_id, switching,
                                  step={"index": 3, "total": 4, "label": "Запуск выбранной версии"})
                self._start_backend(release_id)
                self._start_ui(release_id, candidate["executable"])
                self.checked(release_id)
                state.update({"active": release_id, "previous": previous, "pending": None})
                state["transition"]["phase"] = "admitting"
                self._save(state)  # From here, never silently restore older user data.
                _advance_progress(self.store, operation_id, switching,
                                  step={"index": 4, "total": 4, "label": "Подтверждение готовности"})
                self._activate(release_id)
                state["transition"] = None
                state.pop("error", None)
                self._save(state)
                _advance_progress(self.store, operation_id, "completed")
                self._retain_installed_releases(state)
                return True
            except Exception as exc:
                self._stop_ui()
                self._stop_backend()
                if state["transition"]["phase"] == "admitting":
                    state["error"] = f"Activation acknowledgement failed: {exc}; data retained"
                    self._save(state)
                    raise
                _advance_progress(self.store, operation_id, "rolling_back", error=str(exc))
                if (backup / "snapshot.json").exists():
                    self._restore(backup)
                state.update({"active": previous, "pending": None, "transition": None, "error": str(exc)})
                self._save(state)
                if previous:
                    self._launch_active(previous, retain=False)
                LOG.error("Candidate %s failed startup; previous release restored: %s", release_id, exc)
                _advance_progress(self.store, operation_id, "failed", error=f"Новая версия не запущена; предыдущая восстановлена. {exc}")
                return False

    def _launch_active(self, release_id: str, *, retain: bool = True) -> None:
        progress = _progress_metadata(self.store)
        recovery_id = progress.get("operation_id") if (
            progress.get("operation") == "recover" and progress.get("phase") == "switching"
            and progress.get("release_id") == release_id) else None
        candidate = self.checked(release_id)
        self._start_backend(release_id)
        self._start_ui(release_id, candidate["executable"])
        self._activate(release_id)
        if recovery_id:
            with _lock(self.owned("state.lock")):
                state = self.state()
                if state.get("active") == release_id and not state.get("transition"):
                    state.pop("error", None)
                    self._save(state)
            _advance_progress(self.store, recovery_id, "completed")
        if retain:
            with _lock(self.owned("state.lock")):
                self._retain_installed_releases(self.state())

    def _activate(self, release_id: str) -> None:
        for attempt in range(3):
            try:
                acknowledged = self._http("activate")
                break
            except URLError as exc:
                # Older releases may admit work before starting a scheduler.
                # Retrying this idempotent control action can finish a transient
                # failure, but an observed health alone is never an ACK.
                if attempt == 2 or isinstance(exc, HTTPError) and exc.code < 500:
                    raise
                health = self._http()
                if (health.get("service") != "elira-ai-api" or health.get("release_id") != release_id
                        or health.get("instance_id") != self.instance or health.get("admitted") is not True
                        or health.get("draining") is not False):
                    raise
                LOG.warning("Retrying activation acknowledgement for admitted release %s", release_id)
                time.sleep(0.1)
        if acknowledged.get("ok") is not True:
            raise RuntimeError("Backend refused activation; admission is not confirmed")
        health = self._http()
        if (health.get("service") != "elira-ai-api" or health.get("release_id") != release_id
                or health.get("instance_id") != self.instance or health.get("admitted") is not True
                or health.get("draining") is not False):
            raise RuntimeError("Backend health did not confirm release identity and admission")

    def _retain_installed_releases(self, state: dict) -> None:
        """Called under state.lock only AFTER successful admission. Candidates stay editable."""
        if not self.layout.protected or state.get("pending") or state.get("transition") or state.get("confirmation"):
            return
        try:
            history = state.get("installed_releases", [])
            if (not isinstance(history, list) or any(not isinstance(item, str) for item in history)
                    or len(history) != len(set(history))):
                raise ValueError("Invalid installed release inventory")
            history = list(history)
            for item in [*history, state.get("previous"), state.get("active")]:
                if item:
                    self._validate_id(item)
                    if item not in history:
                        history.append(item)
            state["installed_releases"] = history
            self._save(state)
            selected = [item for item in (state.get("active"), state.get("previous")) if item]
            keep = set(selected)
            for item in reversed(history):
                if len(keep) >= 3:
                    break
                keep.add(item)
            self._retire_unused_verified(history, keep, state.get("active"))
            if len(history) <= 3:
                return
            # The hidden reserve must also be usable before an older copy goes.
            for item in keep:
                self.checked(item)
            for item in list(history):
                if item in keep:
                    continue
                root = self.path(item)
                receipt = _read_json(self.record(item))
                self._check_retention_target(root)
                if not receipt.get("retention_retiring"):
                    self.checked(item)
                    receipt["retention_retiring"] = True
                    _write_json(self.record(item), receipt)
                if root.exists():
                    self._remove_published_tree(root)
                receipt.update(status="retired", retired_at=time.time())
                _write_json(self.record(item), receipt)
                history.remove(item)
                state["installed_releases"] = list(history)
                self._save(state)
                LOG.info("Retired installed release %s; active/return/hidden reserve retained: %s", item, sorted(keep))
        except Exception:
            # Cleanup cannot turn a successfully admitted release into a rollback.
            # The durable retirement intent allows retry on the next admission.
            LOG.exception("Installed release retention incomplete; application and candidates retained")

    def _retire_unused_verified(self, installed: list[str], keep: set[str], active: str | None) -> None:
        """Verification seals a copy into published before anyone installs it, and
        installed-release retention never sees a copy that was not installed, so such
        copies stayed forever. Retire those verified BEFORE the admitted release; a copy
        verified after it may still be proposed for installation and stays."""
        if not active:
            return
        try:
            admitted_at = _read_json(self.record(active), {}).get("verified_at")
        except (OSError, ValueError):
            LOG.exception("Unused verified releases retained: the admitted release record is unreadable")
            return
        if not isinstance(admitted_at, (int, float)) or not self.layout.published.is_dir():
            return
        for folder in sorted(self.layout.published.iterdir()):
            item = folder.name
            # Dot-folders are publications in progress (or interrupted ones): never touched here.
            if item.startswith(".") or not _ID.fullmatch(item) or item in keep or item in installed:
                continue
            try:
                root = self.path(item)
                receipt = _read_json(self.record(item), {})
                verified_at = receipt.get("verified_at")
                if (receipt.get("status") != "verified" or not root.is_dir()
                        or not isinstance(verified_at, (int, float)) or verified_at >= admitted_at):
                    continue
                self._check_retention_target(root)
                self._remove_published_tree(root)
                receipt.update(status="retired", retired_at=time.time(), retired_reason="never_installed")
                _write_json(self.record(item), receipt)
                LOG.info("Retired verified release %s that was never installed (older than %s)", item, active)
            except Exception:
                LOG.exception("Unused verified release %s retained; retry on the next admission", item)

    def _check_retention_target(self, root: Path) -> None:
        if root.parent != self.layout.published or any(
            boundary == root or root in boundary.parents
            for boundary in (self.platform, self.store, self.data, self.journals,
                             self.layout.candidates, self.layout.config_root)
        ):
            raise ValueError("Retention target overlaps persistent or editable paths")

    def _remove_published_tree(self, root: Path) -> None:
        # Include caches and bytecode: rmtree must never encounter a
        # junction or link hidden in fingerprint-excluded paths.
        _contained(root, self.layout.published)
        def walk_error(error: OSError) -> None:
            raise error

        for folder, directories, names in os.walk(root, onerror=walk_error):
            for name in [*directories, *names]:
                path = Path(folder) / name
                info = path.lstat()
                if (path.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                        or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                    raise ValueError("Retention refuses linked or special release files")
        shutil.rmtree(root)

    def recover(self) -> dict:
        state = self.state()
        transition = state.get("transition")
        if transition:
            operation_id = _begin_progress(self.store, "recover", transition.get("to"),
                                           "rolling_back" if transition["phase"] in {"stopping", "switching"} else "switching")
            if transition["phase"] in {"stopping", "switching"}:
                backup = _contained(Path(transition["backup"]), self.store)
                if backup.parent != self.owned("backups"):
                    raise ValueError("Invalid transition backup path")
                if (backup / "snapshot.json").exists():
                    self._restore(backup)
                state.update({"active": transition.get("from"), "pending": None})
            # admitting may have served real work. Keep its code and data.
            state["transition"] = None
            state["recovered_at"] = time.time()
            self._save(state)
            if transition["phase"] in {"stopping", "switching"}:
                _advance_progress(self.store, operation_id, "interrupted",
                                  error="Обновление прервано до допуска работы; восстановлены предыдущая версия и данные.")
        return state

    def run(self) -> None:
        with _lock(self.owned("supervisor.lock")):
            self._reap_owned_processes()
            # A crashed supervisor may leave its child alive. Never restore live
            # databases or kill an unrelated process discovered only by port.
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    raise RuntimeError("A backend is still listening. Close the old managed application before recovery")
            state = self.recover()
            try:
                if state.get("active"):
                    self._launch_active(state["active"])
                elif not state.get("pending"):
                    raise ValueError("No active or pending verified release")
                while True:
                    self.apply_pending()
                    if self.backend is None or self.ui is None or self.ui.poll() is not None:
                        break
                    if self.backend.poll() is not None:
                        raise RuntimeError("Active backend exited; data retained for recovery")
                    time.sleep(2)
            finally:
                self._stop_ui()
                if self.backend is not None:
                    try:
                        self._http("drain")
                    except (OSError, URLError, ValueError):
                        pass
                self._stop_backend()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _relocate_environment(root: Path, destination: Path, backup: Path) -> None:
    """Temporarily rebase copied venv launchers; executed only by the user worker."""
    scripts = ReleaseManager._python(root).parent
    for _ in _tree_files(scripts):
        pass
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copytree(scripts, backup / "scripts")
    config = root / "backend/.venv/pyvenv.cfg"
    if config.exists():
        shutil.copy2(config, backup / "pyvenv.cfg")
    _write_json(backup / "ready.json", {"config": config.exists()})
    replacements = [(str(root / "backend/.venv").encode(), str(destination / "backend/.venv").encode()),
                    ((root / "backend/.venv").as_posix().encode(), (destination / "backend/.venv").as_posix().encode())]
    for file in list(_tree_files(scripts)) + ([config] if config.exists() else []):
        if file.suffix.lower() not in {".exe", ".dll", ".pyd"}:
            content = original = file.read_bytes()
            for old, new in replacements:
                content = content.replace(old, new)
            if content != original:
                file.write_bytes(content)
    code = (
        "import importlib.metadata,sys; from pip._vendor.distlib.scripts import ScriptMaker; "
        "m=ScriptMaker(None,sys.argv[1]); m.executable=sys.argv[2]; m.clobber=True; m.variants={''}; "
        "[m.make(e.name+'='+e.value,options={'gui':e.group=='gui_scripts'}) "
        "for d in importlib.metadata.distributions() for e in d.entry_points "
        "if e.group in {'console_scripts','gui_scripts'}]"
    )
    LocalProcessHost().run([str(ReleaseManager._python(root)), "-I", "-c", code,
                            str(scripts), str(ReleaseManager._python(destination))], cwd=root)
    for file in _tree_files(scripts):
        if any(old.lower() in file.read_bytes().lower() for old, _ in replacements):
            raise ValueError(f"Venv launcher still refers to the editable candidate: {file.name}")


def _restore_environment(root: Path, backup: Path) -> None:
    if not (backup / "ready.json").exists():
        return  # Relocation failed before any original bytes were changed.
    scripts = ReleaseManager._python(root).parent
    saved = {file.relative_to(backup / "scripts"): file for file in _tree_files(backup / "scripts")}
    for file in list(_tree_files(scripts)):
        if file.relative_to(scripts) not in saved:
            file.unlink()
    for relative, source in saved.items():
        target = _contained(scripts / relative, scripts)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Relocation leaves the interpreter binaries untouched. Do not rewrite
        # an unchanged executable that Windows may still hold as an image.
        if not target.is_file() or source.read_bytes() != target.read_bytes():
            shutil.copy2(source, target)
    if _read_json(backup / "ready.json")["config"]:
        shutil.copy2(backup / "pyvenv.cfg", root / "backend/.venv/pyvenv.cfg")


def _run_worker(args) -> None:
    """Fixed filesystem recipes. This entry point must run under the user host."""
    if args.work is None or args.worker_root is None:
        raise ValueError("Worker operation requires its assigned work directory")
    required = {
        "prepare": ("root", "source"), "snapshot": ("data", "destination"),
        "restore": ("data", "source"), "relocate": ("root", "destination", "backup"),
        "restore-environment": ("root", "backup"), "cleanup": (),
    }
    if any(getattr(args, name) is None for name in required[args.operation]):
        raise ValueError(f"Missing paths for worker operation: {args.operation}")
    work = _contained(args.work, args.worker_root)
    if work == args.worker_root or work.parent != args.worker_root:
        raise ValueError("Worker directory must be a direct child of its assigned root")
    if args.operation == "cleanup":
        if work.exists():
            shutil.rmtree(work)
        return
    if args.operation == "prepare":
        layout = ReleaseLayout(store=work / "state", candidates=args.root.parent,
                               data=work / "data", journals=work / "journals", config_root=args.platform / "backend")
        manager = ReleaseManager(args.platform, layout=layout, publish_processes=False)
        manager._prepare_files(args.root, args.source, overlay=args.overlay == "1")
    elif args.operation == "snapshot":
        _snapshot_databases(args.data, _contained(args.destination, work))
    elif args.operation == "restore":
        _restore_databases(args.data, _contained(args.source, work))
    elif args.operation == "relocate":
        _relocate_environment(args.root, args.destination, _contained(args.backup, work))
    elif args.operation == "restore-environment":
        _restore_environment(args.root, _contained(args.backup, work))


def _foundation_client_command(*, platform: Path, port: int) -> list[str] | None:
    if os.name != "nt":
        return None
    import winreg

    managed = os.environ.get("ELIRA_FOUNDATION_MANAGED") == "1"
    service = os.environ.get("ELIRA_FOUNDATION_SERVICE", "EliraFoundation") if managed else "EliraFoundation"
    if service not in {"EliraFoundation", "EliraFoundationProof"}:
        raise ValueError("Unknown Foundation service")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Elira\{service}",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            try:
                location, kind = winreg.QueryValueEx(key, "InstallRoot")
                registered_platform, platform_kind = winreg.QueryValueEx(key, "Platform")
                registered_port, port_kind = winreg.QueryValueEx(key, "Port")
            except FileNotFoundError as exc:
                raise RuntimeError("Foundation registration lacks its platform/port binding; repair the installation") from exc
    except FileNotFoundError:
        if managed:
            raise RuntimeError("Managed Foundation registration is missing; local supervisor fallback is disabled")
        return None
    root = Path(location)
    if kind != winreg.REG_SZ or not root.is_absolute():
        raise ValueError("Invalid protected Foundation registration")
    if (platform_kind != winreg.REG_SZ or not Path(registered_platform).is_absolute()
            or port_kind != winreg.REG_DWORD or not 1024 <= registered_port <= 65535):
        raise ValueError("Invalid protected Foundation platform/port binding")
    if platform.resolve() != Path(registered_platform).resolve():
        if port == registered_port:
            raise ValueError("An isolated platform must use a port different from the installed Foundation")
        return None
    if port != registered_port:
        raise ValueError("This platform belongs to Foundation at its registered port; local fallback is disabled")
    python, client = root / "python/python.exe", root / "host/foundation_client.py"
    if not python.is_file() or not client.is_file():
        raise RuntimeError("Foundation installation is incomplete; local supervisor fallback is disabled")
    return [str(python), "-X", "utf8", "-I", "-S", "-B", str(client), "--service", service]


def _load_application_environment(config_root: Path) -> None:
    # Only the unprivileged serve child imports application dependencies/secrets.
    from dotenv import dotenv_values

    values = {**dotenv_values(config_root / ".env"), **dotenv_values(config_root / ".env.local")}
    for key, value in values.items():
        if value is not None:
            os.environ.setdefault(key, value)
    os.environ.setdefault("ELIRA_FS_UNRESTRICTED", "1")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path,
                        default=Path(os.getenv("ELIRA_PLATFORM_ROOT") or Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--startup-timeout", type=float, default=90)
    parser.add_argument("--runtime-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "verify", "request", "serve"):
        commands.add_parser(name).add_argument("release_id")
    commands.add_parser("confirm").add_argument("confirmation_id")
    for name in ("status", "run"):
        commands.add_parser(name)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--expected-active")
    rollback.add_argument("--expected-previous")
    rollback.add_argument("--confirm", action="store_true")
    worker = commands.add_parser("worker", help="Private user-process filesystem worker; not a service IPC operation")
    worker.add_argument("operation", choices=("prepare", "snapshot", "restore", "relocate", "restore-environment", "cleanup"))
    for name in ("worker-root", "work", "root", "source", "destination", "data", "backup"):
        worker.add_argument("--" + name, type=Path)
    worker.add_argument("--overlay", choices=("0", "1"), default="0")
    args = parser.parse_args()
    if args.command == "rollback" and (args.expected_active or args.expected_previous or args.confirm):
        if not args.expected_active or not args.expected_previous or not args.confirm:
            parser.error("Confirmed rollback requires both expected releases and --confirm")
    if args.command == "worker":
        _run_worker(args)
        return 0
    if args.command == "serve":
        ReleaseManager._validate_id(args.release_id)
        root = args.runtime_root or args.platform / ".runtime/releases/candidates" / args.release_id
        if os.environ.get("ELIRA_FOUNDATION_MANAGED") == "1":
            _load_application_environment(Path(os.environ.get("ELIRA_CONFIG_ROOT") or args.platform / "backend"))
        sys.path.insert(0, str(root / "backend"))
        import uvicorn
        from app.main import app
        from app.core import release_runtime
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                              timeout_graceful_shutdown=5))
        release_runtime.set_callbacks(shutdown=lambda: setattr(server, "should_exit", True))
        server.run()
        return 0
    client = _foundation_client_command(platform=args.platform, port=args.port)
    if client is not None:
        command = "open" if args.command == "run" else args.command
        arguments = client + [command]
        if getattr(args, "release_id", None):
            arguments.append(args.release_id)
        if getattr(args, "confirmation_id", None):
            arguments.append(args.confirmation_id)
        if args.command == "rollback" and args.confirm:
            arguments.extend(["--expected-active", args.expected_active,
                              "--expected-previous", args.expected_previous, "--confirm"])
        arguments.append("--wait")
        response = subprocess.run(arguments, check=False, capture_output=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # Explicit pipes also work when the parent has no Windows console handles.
        sys.stdout.write((response.stdout or b"").decode("utf-8", errors="strict").replace("\r\n", "\n"))
        sys.stderr.write((response.stderr or b"").decode("utf-8", errors="strict").replace("\r\n", "\n"))
        return response.returncode
    manager = ReleaseManager(args.platform, port=args.port, startup_timeout=args.startup_timeout)
    if args.command == "run":
        manager.run()
        return 0
    if args.command == "status":
        result = manager.state()
    elif args.command == "rollback":
        previous = manager.state().get("previous")
        if not previous:
            raise ValueError("No previous verified release")
        result = manager.request(args.expected_previous or previous, operation="rollback",
                                 expected_active=args.expected_active, expected_previous=args.expected_previous)
        if args.confirm:
            result = manager.confirm(result["confirmation"]["request_id"])
    elif args.command == "confirm":
        result = manager.confirm(args.confirmation_id)
    else:
        result = getattr(manager, args.command)(args.release_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        LOG.error("Release operation failed: %s", error)
        raise SystemExit(1)
