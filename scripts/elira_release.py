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
from urllib.error import URLError
from urllib.request import Request, urlopen


LOG = logging.getLogger("elira.release")
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".vite"}
_DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


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
        raw = Path(f"/proc/{pid}/stat").read_text()
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


def _snapshot_databases(data: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    names = []
    for source in sorted(_tree_files(data)) if data.exists() else []:
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
    for name in names:
        _contained(data / name, data)
        _contained(snapshot / name, snapshot)
    known = set(names)
    current_files = list(_tree_files(data))
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


class ReleaseManager:
    def __init__(self, platform: Path, *, port: int = 8000, startup_timeout: float = 90,
                 publish_processes: bool = True):
        self.platform = platform.resolve()
        self.store = self.platform / ".runtime" / "releases"
        _contained(self.store, self.platform)
        self.data = Path(os.getenv("ELIRA_DATA_DIR") or self.platform / "data").resolve()
        self.journals = Path(os.getenv("ELIRA_AGENT_RUNS_DIR") or self.platform / ".agent" / "runs").resolve()
        self.port = port
        self.startup_timeout = startup_timeout
        self.publish_processes = publish_processes
        self.backend: subprocess.Popen | None = None
        self.ui: subprocess.Popen | None = None
        self.token = secrets.token_urlsafe(32)
        self.instance = secrets.token_hex(16)
        self.store.mkdir(parents=True, exist_ok=True)

    def path(self, release_id: str) -> Path:
        if not _ID.fullmatch(release_id) or release_id in {".", ".."}:
            raise ValueError("Release id must contain only lowercase letters, digits, dots, dashes or underscores")
        return self.owned("candidates/" + release_id)

    def owned(self, relative: str) -> Path:
        return _contained(self.store / relative, self.platform)

    def record(self, release_id: str) -> Path:
        self.path(release_id)
        return self.owned(f"records/{release_id}.json")

    def state(self) -> dict:
        return _read_json(self.owned("state.json"), {
            "active": None, "previous": None, "pending": None, "transition": None,
        })

    def _save(self, state: dict) -> None:
        _write_json(self.owned("state.json"), state)

    def _python(self, root: Path) -> Path:
        return root / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _npm(self) -> str:
        result = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not result:
            raise RuntimeError("npm is required to verify a complete application release")
        return result

    def _command(self, arguments: list[str], *, cwd: Path, env: dict | None = None,
                 log=None) -> subprocess.CompletedProcess:
        return subprocess.run(arguments, cwd=cwd, env=env, check=True,
                              stdout=log, stderr=subprocess.STDOUT if log else None)

    def prepare(self, release_id: str) -> dict:
        root = self.path(release_id)
        if root.exists():
            raise ValueError("Candidate already exists; continue editing that candidate or choose a new id")
        root.parent.mkdir(parents=True, exist_ok=True)
        active = self.state().get("active")
        source = self.path(active) if active else self.platform
        if active:
            self.checked(active)
        # Start from the active release, so successive improvements accumulate.
        # The initial developer checkout contributes committed source only.
        self._command(["git", "clone", "--local", "--no-hardlinks", str(source), str(root)], cwd=self.platform)
        self._command(["git", "remote", "remove", "origin"], cwd=root)
        if active:
            files = dict(self._source_files(source))
            for name, path in self._source_files(root):
                if name not in files:
                    path.unlink()  # New candidate only; removed source stays removed.
            for name, path in files.items():
                target = _contained(root / name, root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        for relative in ("backend/.venv", "node_modules", "frontend/node_modules"):
            dependency = source / relative
            if not dependency.is_dir():
                raise ValueError(f"Install the platform dependency environment first: {relative}")
            # Validate redirections before copying; do not flatten a junction
            # into an apparently independent candidate environment.
            for _ in _tree_files(dependency):
                pass
            shutil.copytree(dependency, root / relative, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
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
        value = {"release_id": release_id, "status": "candidate", "root": str(root)}
        _write_json(self.record(release_id), value)
        return value

    def _source_files(self, root: Path) -> list[tuple[str, Path]]:
        # Runtime code may be ignored by Git. Seal the actual source tree rather
        # than trusting Git's inventory or an agent-supplied file manifest.
        excluded = {".git", ".runtime", ".agent", ".scratch", "data", "backend/data",
                    "backend/.venv", "node_modules", "frontend/node_modules", "frontend/dist",
                    "src-tauri/target", "src-tauri/gen", "target"}
        return [(path.relative_to(root).as_posix(), path) for path in _tree_files(root, excluded=excluded)]

    def fingerprint(self, root: Path, *, executable: str = "") -> str:
        paths = self._source_files(root)
        for relative in ("backend/.venv", "node_modules", "frontend/node_modules"):
            paths.extend((file.relative_to(root).as_posix(), file) for file in _tree_files(root / relative))
        if executable:
            paths.extend((file.relative_to(root).as_posix(), file) for file in _tree_files(root / "frontend/dist"))
            paths.append((executable, _contained(root / executable, root)))
        return _digest(paths)

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

    def _environment(self, release_id: str, *, data: Path | None = None) -> dict:
        env = os.environ.copy()
        candidate = self.path(release_id)
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env.update({"ELIRA_RELEASE_ID": release_id, "ELIRA_RELEASE_STAGING": "1",
                    "ELIRA_RELEASE_TOKEN": self.token, "ELIRA_RELEASE_INSTANCE": self.instance,
                    "ELIRA_CONFIG_ROOT": str(self.platform / "backend"),
                    "ELIRA_PLATFORM_ROOT": str(self.platform),
                    "ELIRA_DATA_DIR": str(data or self.data),
                    "ELIRA_AGENT_RUNS_DIR": str(self.journals if data is None else data / "agent-runs"),
                    "ELIRA_EXTERNAL_BACKEND": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        env["VIRTUAL_ENV"] = str(candidate / "backend/.venv")
        env["PATH"] = os.pathsep.join((str(self._python(candidate).parent),
                                      str(candidate / "node_modules/.bin"),
                                      str(candidate / "frontend/node_modules/.bin"), env.get("PATH", "")))
        env.setdefault("ELIRA_FS_UNRESTRICTED", "1")
        if data is not None:
            env.update({"ELIRA_DRIFT_CHECK": "0", "LLAMA_SERVER_ENABLED": "false",
                        "LOCAL_EMBED_ENABLED": "false"})
        return env

    def verify(self, release_id: str) -> dict:
        root = self.path(release_id)
        before = self.fingerprint(root)
        value = {"release_id": release_id, "status": "verifying", "root": str(root)}
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
                    for command in self._verification_commands(root):
                        LOG.info("Verifying %s: %s", release_id, command[1:])
                        self._command(command, cwd=root, env=env, log=log)
                if self.fingerprint(root) != before:
                    raise ValueError("Candidate code or dependencies changed during verification; verify the final version")
                executable = self._find_executable(root)
                value.update({"status": "verified", "executable": executable,
                              "sha256": self.fingerprint(root, executable=executable),
                              "verified_at": time.time()})
                # Exercise actual candidate imports/startup against copied data,
                # with schedulers and user requests held until admission.
                probe = type(self)(self.platform, port=_free_port(), startup_timeout=self.startup_timeout,
                                   publish_processes=False)
                try:
                    probe._start_backend(release_id, data=check_data)
                finally:
                    probe._stop_backend()
            _write_json(self.record(release_id), value)
            return value
        except Exception as exc:
            value.update({"status": "failed", "error": str(exc)})
            _write_json(self.record(release_id), value)
            raise

    def checked(self, release_id: str) -> dict:
        value = _read_json(self.record(release_id))
        if value.get("status") != "verified":
            raise ValueError("Candidate has not passed the release checks")
        if self.fingerprint(self.path(release_id), executable=value["executable"]) != value["sha256"]:
            raise ValueError("Candidate changed after verification; run verify again")
        return value

    def request(self, release_id: str) -> dict:
        self.checked(release_id)
        with _lock(self.owned("state.lock")):
            state = self.state()
            if state.get("transition"):
                raise ValueError("Another release transition is still in progress")
            if state.get("active") == release_id:
                return state
            state["pending"] = release_id
            self._save(state)
            self.owned("enabled").touch()
            return state

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
        return [str(self._python(self.path(release_id))), str(self.platform / "scripts/elira_release.py"),
                "--platform", str(self.platform), "--port", str(self.port), "serve", release_id]

    def _save_processes(self) -> None:
        if self.publish_processes:
            owned = {"port": self.port, "token": self.token, "instance": self.instance}
            for name, process in (("backend", self.backend), ("ui", self.ui)):
                if process is not None and process.poll() is None:
                    identity = _process_identity(process.pid)
                    if identity is None:
                        raise RuntimeError(f"Cannot identify owned {name} process")
                    owned[name] = {"pid": process.pid, "identity": identity}
            _write_json(self.owned("live.json"), owned)

    def _reap_owned_processes(self) -> None:
        saved = _read_json(self.owned("live.json"), {})
        owned = {name: item for name in ("backend", "ui")
                 if isinstance(item := saved.get(name), dict)
                 and _process_identity(int(item["pid"])) == item.get("identity")}
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
                if name == "ui" and _process_identity(pid) == identity:
                    os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and _process_identity(pid) == identity:
                    time.sleep(0.1)
                if _process_identity(pid) == identity:
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
            self.backend = subprocess.Popen(self._backend_command(release_id), cwd=self.path(release_id) / "backend",
                                            env=self._environment(release_id, data=data), stdout=log, stderr=log,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._save_processes()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.backend.poll() is not None:
                raise RuntimeError(f"Candidate backend exited with code {self.backend.returncode}")
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
        self.ui = subprocess.Popen([str(self.path(release_id) / executable)], cwd=self.path(release_id),
                                   env=self._environment(release_id))
        self._save_processes()
        time.sleep(0.5)
        if self.ui.poll() is not None:
            raise RuntimeError("Candidate desktop exited during startup")

    def _stop_ui(self) -> None:
        if self.ui is not None and self.ui.poll() is None:
            self.ui.terminate()
            try:
                self.ui.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.ui.kill()
                self.ui.wait(timeout=10)
        self.ui = None
        self._save_processes()

    def _stop_backend(self) -> None:
        if self.backend is not None and self.backend.poll() is None:
            try:
                self._http("shutdown")
                self.backend.wait(timeout=15)
            except (OSError, URLError, ValueError, subprocess.TimeoutExpired):
                self.backend.terminate()
                self.backend.wait(timeout=15)
        self.backend = None
        self._save_processes()

    def apply_pending(self) -> bool:
        state = self.state()
        release_id = state.get("pending")
        if not release_id:
            return False
        try:
            candidate = self.checked(release_id)
        except (OSError, ValueError) as exc:
            with _lock(self.owned("state.lock")):
                state = self.state()
                if state.get("pending") == release_id:
                    state.update({"pending": None, "error": str(exc)})
                    self._save(state)
            LOG.error("Pending release rejected without interrupting the active application: %s", exc)
            return False
        if self.backend is not None:
            if not self._http("drain").get("idle"):
                self._http("resume")
                return False
        with _lock(self.owned("state.lock")):
            state = self.state()
            if state.get("pending") != release_id:
                if self.backend is not None:
                    self._http("resume")
                return False
            previous = state.get("active")
            backup = self.owned("backups/" + secrets.token_hex(12))
            state["transition"] = {"from": previous, "to": release_id, "phase": "stopping", "backup": str(backup)}
            self._save(state)
            self._stop_ui()
            self._stop_backend()
            try:
                self.checked(release_id)
                _snapshot_databases(self.data, backup)
                state["transition"]["phase"] = "switching"
                self._save(state)
                self._start_backend(release_id)
                self._start_ui(release_id, candidate["executable"])
                self.checked(release_id)
                state.update({"active": release_id, "previous": previous, "pending": None})
                state["transition"]["phase"] = "admitting"
                self._save(state)  # From here, never silently restore older user data.
                self._http("activate")
                state["transition"] = None
                state.pop("error", None)
                self._save(state)
                return True
            except Exception as exc:
                self._stop_ui()
                self._stop_backend()
                if state["transition"]["phase"] == "admitting":
                    state["error"] = f"Activation acknowledgement failed: {exc}; data retained"
                    self._save(state)
                    raise
                if (backup / "snapshot.json").exists():
                    _restore_databases(self.data, backup)
                state.update({"active": previous, "pending": None, "transition": None, "error": str(exc)})
                self._save(state)
                if previous:
                    self._launch_active(previous)
                LOG.error("Candidate %s failed startup; previous release restored: %s", release_id, exc)
                return False

    def _launch_active(self, release_id: str) -> None:
        candidate = self.checked(release_id)
        self._start_backend(release_id)
        self._start_ui(release_id, candidate["executable"])
        self._http("activate")

    def recover(self) -> dict:
        state = self.state()
        transition = state.get("transition")
        if transition:
            if transition["phase"] in {"stopping", "switching"}:
                backup = _contained(Path(transition["backup"]), self.platform)
                if backup.parent != self.owned("backups"):
                    raise ValueError("Invalid transition backup path")
                if (backup / "snapshot.json").exists():
                    _restore_databases(self.data, backup)
                state.update({"active": transition.get("from"), "pending": None})
            # admitting may have served real work. Keep its code and data.
            state["transition"] = None
            state["recovered_at"] = time.time()
            self._save(state)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path,
                        default=Path(os.getenv("ELIRA_PLATFORM_ROOT") or Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--startup-timeout", type=float, default=90)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "verify", "request", "serve"):
        commands.add_parser(name).add_argument("release_id")
    for name in ("status", "run", "rollback"):
        commands.add_parser(name)
    args = parser.parse_args()
    manager = ReleaseManager(args.platform, port=args.port, startup_timeout=args.startup_timeout)
    if args.command == "serve":
        root = manager.path(args.release_id)
        sys.path.insert(0, str(root / "backend"))
        import uvicorn
        from app.main import app
        from app.core import release_runtime
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                              timeout_graceful_shutdown=5))
        release_runtime.set_callbacks(shutdown=lambda: setattr(server, "should_exit", True))
        server.run()
        return 0
    if args.command == "run":
        manager.run()
        return 0
    if args.command == "status":
        result = manager.state()
    elif args.command == "rollback":
        previous = manager.state().get("previous")
        if not previous:
            raise ValueError("No previous verified release")
        result = manager.request(previous)
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
