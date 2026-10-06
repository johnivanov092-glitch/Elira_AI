"""Local Git history and atomic activation of agent-authored skill packages.

Execution uses the existing shell runtime; this module owns package files and
receipts only. It never imports generated Python into the agent process.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator
import uuid

from app.core.config import DATA_DIR


ROOT = DATA_DIR / "skill_development"
logger = logging.getLogger(__name__)
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ID = re.compile(r"[0-9a-f]{32}")
# Runtime dependencies belong to each candidate, never a shared active venv.
# They stay at the same absolute path when the candidate becomes active.
_TRANSIENT = {".git", "__pycache__", ".pytest_cache"}
_DEPENDENCIES = {".venv", "venv", "node_modules"}
_CANCELLED: ContextVar[threading.Event | None] = ContextVar("skill_operation_cancelled", default=None)
# validated_package runs for every loaded skill on every turn; re-reading a
# 100 MB skill .venv cost ~3 s per message. A verified digest is reused only
# while the tree's stat signature is unchanged, never for files modified within
# the racy window, never for trees with links/reparse points, and the content is
# re-hashed in full at least once per TTL.
_DIGEST_CACHE: dict[str, tuple[str, str, float]] = {}
_DIGEST_CACHE_LOCK = threading.Lock()
_DIGEST_CACHE_TTL = 3600.0
_RACY_WINDOW_NS = 2_000_000_000
_REPARSE_POINT = 0x400  # stat.FILE_ATTRIBUTE_REPARSE_POINT (Windows symlinks and junctions)


class _Cancelled(Exception):
    pass


class PackageChanged(ValueError):
    """A well-formed publication receipt whose current files no longer match."""

    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details
        changes = details["source_diff"]
        messages = ["Skill package files or dependencies changed since verification"]
        if changes["status"] == "unavailable":
            messages.append("Source comparison unavailable: " + changes["error"])
        elif any(changes[kind] for kind in ("added", "changed", "deleted")):
            remaining = 8
            for kind in ("added", "changed", "deleted"):
                paths = changes[kind]
                shown = paths[:remaining]
                remaining -= len(shown)
                if paths:
                    messages.append(f"{kind} ({len(paths)}): " + json.dumps(
                        [path[:240] for path in shown], ensure_ascii=False)
                        + ("; more paths in skill_status" if len(paths) > len(shown) else ""))
        else:
            messages.append("Git source is unchanged; installed dependencies or file metadata differ")
        messages.append(f"History: {changes['repository']}; revision: {details['revision']}")
        messages.append(f"Use skill_status(name='{details['name']}') for the full diff, or "
                        f"skill_create(name='{details['name']}') to copy current source into a new UNVERIFIED "
                        "candidate; recreate its dependencies, skill_check, skill_publish, then skill_load")
        super().__init__(". ".join(messages))


def _check_cancelled() -> None:
    from app.application.code_agent.tools._shell import run_was_stopped

    event = _CANCELLED.get()
    if (event is not None and event.is_set()) or run_was_stopped():
        raise _Cancelled("Skill operation cancelled")


@contextmanager
def _cancel_scope() -> Iterator[None]:
    from app.application.code_agent.tools._shell import (
        register_run_cancel_callback, unregister_run_cancel_callback,
    )

    event = threading.Event()
    token = _CANCELLED.set(event)
    callback = register_run_cancel_callback(event.set)
    try:
        _check_cancelled()
        yield
    finally:
        unregister_run_cancel_callback(callback)
        _CANCELLED.reset(token)


def _managed(path: Path) -> Path:
    """Reject redirects before touching managed state, history or packages."""
    absolute = path.absolute()
    root = ROOT.absolute()
    if root.resolve() != root or absolute.resolve() != absolute or not absolute.is_relative_to(root):
        raise ValueError("Skill storage path escaped its managed directory")
    return absolute


def _name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or not _NAME.fullmatch(value):
        raise ValueError("Invalid skill name; use lowercase letters, digits and hyphens")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    path = _managed(path)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid package state: {path.name}")
    return value


def _write_json(path: Path, value: dict[str, Any], *, activation: bool = False) -> None:
    path = _managed(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _managed(path)
        if activation:
            _check_cancelled()
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _locked() -> Iterator[None]:
    _managed(ROOT).mkdir(parents=True, exist_ok=True)
    with _managed(ROOT / "state.lock").open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _directory(name: str, candidate_id: str) -> Path:
    if not isinstance(candidate_id, str) or not _ID.fullmatch(candidate_id):
        raise ValueError("Invalid candidate ID")
    return _managed(ROOT / "packages" / _name(name) / candidate_id)


def _command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _python_environment(
    directory: Path, *, required: bool = False, validate: bool = True,
) -> dict[str, Any]:
    """Discover package-owned paths without importing or executing package code.

    The complete package digest covers this environment too. Python's base
    standard library remains an OS installation; third-party search paths must
    stay inside the durable package, never an application or scratch venv.
    """
    environment = _managed(directory / ".venv")
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    configuration = environment / "pyvenv.cfg"
    ready = configuration.is_file() and python.is_file()
    if required and not ready:
        raise ValueError("Package Python environment is missing; create its .venv and run skill_check again")
    if ready and validate:
        settings = dict(line.split("=", 1) for line in configuration.read_text(encoding="utf-8").splitlines()
                        if "=" in line)
        settings = {key.strip().lower(): value.strip().lower() for key, value in settings.items()}
        if settings.get("include-system-site-packages") != "false":
            raise ValueError("Package .venv must set include-system-site-packages = false")
        for path in _files(directory):
            if not path.is_relative_to(environment) or path.suffix not in {".pth", ".egg-link"}:
                continue
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                entry = line.strip()
                # Normal package hooks (e.g. setuptools) are Python code, not
                # declarative paths. This contract is not a code sandbox.
                if not entry or entry.startswith(("#", "import ", "import\t")):
                    continue
                if not (path.parent / entry).resolve().is_relative_to(directory):
                    raise ValueError(f"Package environment has an external dependency path: {path.name}")
    python_argv = [str(python), "-E", "-s"]
    pip_argv = [*python_argv, "-m", "pip", "--isolated", "--require-virtualenv"]
    pip_command = ((f'set "PIP_CONFIG_FILE={os.devnull}" && ' if os.name == "nt"
                    else f"PIP_CONFIG_FILE={shlex.quote(os.devnull)} ") + _command(pip_argv))
    return {
        "kind": "python", "status": "ready" if ready else "not_created",
        "isolation": ("validated" if validate else "legacy_unverified") if ready else "not_created",
        "directory": str(environment), "python": str(python),
        "python_argv": python_argv, "pip_argv": pip_argv,
        "pip_env": {"PIP_CONFIG_FILE": os.devnull},
        "python_command": _command(python_argv), "pip_command": pip_command,
        "command_shell": "cmd" if os.name == "nt" else "sh",
        "create_command": _command([str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
                                    "-I", "-m", "venv", "--copies", str(environment)]),
        "guidance": "Use these absolute python/pip commands, never bare python or pip. -E -s ignores "
                    "PYTHONHOME/PYTHONPATH and user-site packages. Install only in this candidate .venv; "
                    "do not change application dependencies. MCP command uses python and args starts with -E, -s. "
                    "Use pip_command (or pip_argv with pip_env) to disable inherited pip configuration. "
                    "Do not move/copy a venv or depend on .scratch; published environments are immutable."
                    + (" This legacy environment predates isolation checks; create a new candidate with "
                       "config.environment='python' to migrate it." if ready and not validate else ""),
    }


def _python_check_environment(environment: dict[str, Any]) -> dict[str, str | None]:
    """Scope existing shell execution to this version without changing the app."""
    scripts = str(Path(environment["python"]).parent)
    return {
        "PATH": scripts + os.pathsep + os.environ.get("PATH", ""),
        "VIRTUAL_ENV": environment["directory"], "PYTHONNOUSERSITE": "1",
        "PYTHONHOME": None, "PYTHONPATH": None,
        "PIP_TARGET": None, "PIP_PREFIX": None, "PIP_USER": None,
        "PIP_CONFIG_FILE": os.devnull,
    }


def _files(directory: Path, *, include_dependencies: bool = True) -> list[Path]:
    def walk_error(error: OSError) -> None:
        raise error

    files: list[Path] = []
    for parent, directories, names in os.walk(directory, followlinks=False, onerror=walk_error):
        _check_cancelled()
        current = Path(parent)
        retained = []
        for name in sorted([*directories, *names]):
            if name in _TRANSIENT or (not include_dependencies and name in _DEPENDENCIES):
                continue
            path = current / name
            resolved = path.resolve()
            redirected = path.is_symlink() or resolved != path.absolute()
            if redirected:
                dependency = any(part in _DEPENDENCIES for part in path.relative_to(directory).parts)
                if not dependency or not (
                    resolved.is_file() or (resolved.is_dir() and resolved.is_relative_to(directory.resolve()))
                ):
                    raise ValueError("Package contains an untracked source or dependency redirect")
                # Unix venv Python executables may refer outside the package;
                # hash that exact interpreter too. Internal directory aliases
                # (e.g. lib64 -> lib) refer to an independently traversed tree.
                files.append(path)
            elif name in directories:
                retained.append(name)
            else:
                if not path.is_file():
                    raise ValueError("Skill package contains a non-regular file")
                files.append(path)
        directories[:] = retained
    return sorted(files)


def _digest(directory: Path, *, include_dependencies: bool = True) -> str:
    digest = hashlib.sha256()
    for path in _files(directory, include_dependencies=include_dependencies):
        digest.update(path.relative_to(directory).as_posix().encode("utf-8") + b"\0")
        if path.is_symlink() or path.resolve() != path.absolute():
            digest.update(b"link\0" + os.readlink(path).encode("utf-8") + b"\0")
            digest.update(str(path.resolve()).encode("utf-8") + b"\0")
            if path.is_dir():
                digest.update(b"directory-link\0")
                continue
        # Fixed-length content digests keep file boundaries unambiguous even
        # for binary dependencies containing NUL bytes and embedded filenames.
        content = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                _check_cancelled()
                content.update(chunk)
        digest.update(b"file\0" + content.digest())
        digest.update(str(path.stat().st_mode & 0o111).encode("ascii") + b"\0")
    return digest.hexdigest()


def _stat_signature(directory: Path) -> str | None:
    """Cheap fingerprint of every entry's name, type, size, mtime and file id.

    None means "do not trust a cached digest": a link or reparse point (its
    target can change without changing the link) or a file modified so recently
    that a same-timestamp rewrite could go unnoticed.
    """
    digest = hashlib.sha256()
    newest = 0
    for parent, directories, names in os.walk(directory, followlinks=False):
        _check_cancelled()
        directories[:] = sorted(name for name in directories if name not in _TRANSIENT)
        for name in sorted([*directories, *names]):
            if name in _TRANSIENT:
                continue
            path = os.path.join(parent, name)
            entry = os.lstat(path)
            if os.path.islink(path) or getattr(entry, "st_file_attributes", 0) & _REPARSE_POINT:
                return None
            digest.update(os.path.relpath(path, directory).encode("utf-8", "surrogatepass") + b"\0")
            digest.update(f"{entry.st_mode}:{entry.st_size}:{entry.st_mtime_ns}:{entry.st_ino}:{entry.st_dev}\0"
                          .encode("ascii"))
            newest = max(newest, entry.st_mtime_ns)
    if time.time_ns() - newest < _RACY_WINDOW_NS:
        return None
    return digest.hexdigest()


def _verified_digest(directory: Path, verified: str) -> str:
    """`_digest(directory)`, skipping the content re-read while nothing changed."""
    key = str(directory)
    signature = _stat_signature(directory)
    now = time.monotonic()
    with _DIGEST_CACHE_LOCK:
        cached = _DIGEST_CACHE.get(key)
    if signature is not None and cached is not None and cached[:2] == (signature, verified) \
            and now - cached[2] < _DIGEST_CACHE_TTL:
        return verified
    observed = _digest(directory)
    with _DIGEST_CACHE_LOCK:
        # The pre-hash signature is stored: any change after it alters the next
        # signature and forces a full re-hash.
        if signature is not None and observed == verified:
            _DIGEST_CACHE[key] = (signature, observed, now)
        else:
            _DIGEST_CACHE.pop(key, None)
    return observed


def _git(directory: Path, *arguments: str) -> str:
    from app.application.code_agent.tools._shell import (
        _kill_proc_tree, _new_process_group_kwargs, register_run_process, unregister_run_process,
    )

    _check_cancelled()
    directory = _managed(directory)
    git_directory = _managed(directory / ".git")
    if git_directory.exists() and not git_directory.is_dir():
        raise ValueError("Skill history must use its own Git directory")
    for name in ("config", "HEAD", "index", "objects", "refs"):
        _managed(git_directory / name)
    if (git_directory / "commondir").exists():
        raise ValueError("Skill history must not share an external Git directory")
    # This dedicated repository has no inherited global filters, aliases,
    # signing hooks or excludes. No network command is ever issued.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1")
    process_options = _new_process_group_kwargs()
    if os.name == "nt":
        process_options["creationflags"] = process_options.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        ["git", "-c", "core.hooksPath=", "-c", "commit.gpgsign=false",
         "-c", f"core.excludesFile={os.devnull}", "-c", f"core.worktree={directory}",
         "-c", "core.bare=false", "-C", str(directory), *arguments],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace", env=env, **process_options,
    )
    run_id = register_run_process(process)
    try:
        try:
            stdout, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            _kill_proc_tree(process)
            process.communicate(timeout=5)
            raise RuntimeError("Local skill Git operation timed out") from None
    finally:
        unregister_run_process(run_id, process)
    _check_cancelled()
    if process.returncode:
        raise RuntimeError(f"Local skill Git operation failed: {stderr.strip()[-1000:]}")
    return stdout.strip()


def active_directories() -> dict[str, Path]:
    """Enumerate references; each loaded package is verified independently."""
    state = _read_json(ROOT / "active.json")
    result: dict[str, Path] = {}
    for name, entry in state.items():
        if not isinstance(entry, dict):
            raise ValueError("Invalid active skill entry")
        result[_name(name)] = _directory(name, entry.get("candidate_id", ""))
    return result


def active_package(name: str) -> dict[str, Any] | None:
    entry = _read_json(ROOT / "active.json").get(_name(name))
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError("Invalid active skill entry")
    return validated_package(name, entry.get("candidate_id", ""), expected=entry)


def _source_diff(directory: Path, repository: Path, revision: str) -> dict[str, Any]:
    """Compare literal source bytes to the saved commit, never mutable HEAD."""
    result: dict[str, Any] = {"repository": str(repository), "revision": revision}
    try:
        records = _git(repository, "ls-tree", "-r", "-z", revision, "--", "package")
        expected = {}
        for record in records.split("\0"):
            if not record:
                continue
            attributes, path = record.split("\t", 1)
            mode, kind, object_id = attributes.split(" ")
            if not path.startswith("package/") or kind != "blob" or mode not in {"100644", "100755"}:
                raise ValueError("Published source tree contains an unsupported entry")
            expected[path[len("package/"):]] = (mode, object_id)
        if not expected:
            raise ValueError("Saved revision has no published package source")
        sources = _files(directory, include_dependencies=False)
        actual = {}
        # Batch below Windows command-line limits. hash-object without -w only
        # reads files; --no-filters keeps binary data and .gitattributes literal.
        while sources:
            batch, size = [], 0
            while sources and (not batch or size + len(str(sources[0])) + 3 < 12000):
                path = sources.pop(0)
                batch.append(path)
                size += len(str(path)) + 3
            hashes = _git(repository, "hash-object", "--no-filters", "--", *map(str, batch)).splitlines()
            if len(hashes) != len(batch) or any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in hashes):
                raise ValueError("Git did not return one source hash per file")
            for path, object_id in zip(batch, hashes):
                actual[path.relative_to(directory).as_posix()] = (path.stat().st_mode & 0o111, object_id)
        changed = [path for path in actual.keys() & expected.keys() if actual[path][1] != expected[path][1]
                   or (os.name != "nt" and bool(actual[path][0]) != (expected[path][0] == "100755"))]
        return {**result, "status": "available", "added": sorted(actual.keys() - expected.keys()),
                "changed": sorted(changed), "deleted": sorted(expected.keys() - actual.keys()),
                "dependencies": "not versioned in Git; only the complete publication digest covers them"}
    except (OSError, ValueError, RuntimeError) as exc:
        # Missing/corrupt history is not evidence that the source is unchanged.
        return {**result, "status": "unavailable", "error": f"{type(exc).__name__}: {str(exc)[:400]}"}


def validated_package(
    name: str, candidate_id: str, *, expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind an active/saved reference to its exact published package receipt."""
    directory, _, metadata = _candidate(name, candidate_id)
    digest = metadata.get("sha256")
    revision = metadata.get("revision")
    if metadata.get("status") != "verified" or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Skill package has no successful verification receipt")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Skill package has no local Git publication receipt")
    if expected is not None:
        expected_digest = expected.get("package_sha256", expected.get("sha256"))
        if expected_digest != digest or expected.get("revision") != revision:
            raise ValueError("Saved skill package integrity check failed")
        if "directory" in expected and expected["directory"] != str(directory):
            raise ValueError("Saved skill package directory does not match its receipt")
    observed = _verified_digest(directory, digest)
    if observed != digest:
        raise PackageChanged({"name": name, "candidate_id": candidate_id, "directory": str(directory),
            "revision": revision, "verified_package_sha256": digest, "observed_package_sha256": observed,
            "source_diff": _source_diff(directory, ROOT / "history" / name, revision)})
    return {
        "candidate_id": candidate_id, "package_sha256": digest,
        "revision": revision, "directory": str(directory),
        "environment": _python_environment(directory, required=metadata.get("environment") == "python",
                                           validate=metadata.get("environment") == "python"),
    }


def _candidate(name: str, candidate_id: str) -> tuple[Path, Path, dict[str, Any]]:
    directory = _directory(name, candidate_id)
    metadata_path = _managed(ROOT / "receipts" / f"{candidate_id}.json")
    metadata = _read_json(metadata_path)
    if metadata.get("name") != name or metadata.get("candidate_id") != candidate_id or not directory.is_dir():
        raise ValueError("Candidate does not exist for this skill")
    return directory, metadata_path, metadata


def _ensure_inactive(name: str, candidate_id: str) -> None:
    entry = _read_json(ROOT / "active.json").get(name, {})
    if candidate_id in {entry.get("candidate_id"), (entry.get("previous") or {}).get("candidate_id")}:
        raise ValueError("Create a new candidate to change an active or rollback version")


def develop(operation: str, name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Use existing runtime_control and Workflow permissions for all mutations."""
    try:
        with _cancel_scope():
            return _develop(operation, name, config)
    except _Cancelled:
        return {"ok": False, "status": "cancelled", "name": name, "error": "cancelled"}


def _develop(operation: str, name: str, config: dict[str, Any]) -> dict[str, Any]:
    name = _name(name)
    candidate_id = str(config.get("candidate_id") or "")
    with _locked():
        if operation == "skill_status":
            integrity: dict[str, Any] = {"status": "not_installed"}
            try:
                active = active_package(name)
                if active:
                    integrity = {"status": "verified", "ok": True, **active}
            except PackageChanged as exc:
                integrity = {"status": "modified", "ok": False, **exc.details}
            return {"ok": True, "name": name,
                    "active": _read_json(ROOT / "active.json").get(name),
                    "repository": str(_managed(ROOT / "history" / name)), "integrity": integrity}
        if operation == "skill_create":
            environment_kind = config.get("environment")
            if environment_kind not in (None, "python"):
                raise ValueError("config.environment must be 'python' or omitted")
            candidate_id = uuid.uuid4().hex
            directory = _directory(name, candidate_id)
            recovery = None
            try:
                active = active_package(name)
                current = Path(active["directory"]) if active else None
            except PackageChanged as exc:
                # Creation is not activation. Recover only a well-formed owned
                # publication; invalid receipts or source redirects still fail.
                recovery = exc.details
                current = _directory(name, recovery["candidate_id"])
            if current is None:
                from app.application.code_agent.task_skills import SKILLS_ROOT
                installed = SKILLS_ROOT / name
                current = installed if installed.is_dir() else None
            before = _digest(current, include_dependencies=False) if current is not None else None
            source = ({"candidate_id": recovery["candidate_id"], "revision": recovery["revision"],
                       "verified_package_sha256": recovery["verified_package_sha256"],
                       "observed_package_sha256": recovery["observed_package_sha256"],
                       "observed_source_sha256": before, "integrity": "modified"} if recovery else None)
            directory.mkdir(parents=True)
            completed = False
            try:
                if current is not None:
                    for item in _files(current, include_dependencies=False):
                        # Environments are path-bound; recreate in the candidate.
                        relative = item.relative_to(current)
                        _check_cancelled()
                        destination = directory / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, destination)
                    if (_digest(current, include_dependencies=False) != before
                            or _digest(directory, include_dependencies=False) != before):
                        raise ValueError("Source changed while copying the candidate; retry skill_create")
                if environment_kind == "python":
                    from app.application.code_agent.tools._run import tool_run_bash

                    created = tool_run_bash(directory, command=_python_environment(directory)["create_command"])
                    _check_cancelled()
                    if created.get("ok") is not True or created.get("exit_code") != 0:
                        raise ValueError("Package environment creation failed: " + str(created.get("text", ""))[-2000:])
                environment = _python_environment(directory, required=environment_kind == "python")
                _write_json(ROOT / "receipts" / f"{candidate_id}.json",
                            {"name": name, "candidate_id": candidate_id, "status": "candidate",
                              **({"environment": "python"} if environment_kind else {}),
                             **({"source": source} if source else {})})
                completed = True
            finally:
                if not completed:
                    # Only this newly created, unpublished directory is removed.
                    shutil.rmtree(_managed(directory))
            logger.info("skill_candidate_created name=%s candidate=%s", name, candidate_id)
            return {"ok": True, "name": name, "candidate_id": candidate_id,
                    "directory": str(directory), "status": "candidate", "environment": environment,
                    **({"source": source, "source_diff": recovery["source_diff"],
                        "warning": "Copied modified source into an UNVERIFIED candidate. The previous package "
                                   "was not repaired or activated; recreate dependencies and verify this candidate."}
                       if recovery else {}),
                    "skill_format": {
                        "encoding": "UTF-8 without BOM, LF",
                        "frontmatter": {"name": name, "description": "1-320 characters; when to use this skill",
                                        "metadata": {"title": "optional, 1-80 characters"}},
                        "layout": "Start SKILL.md with --- followed by YAML, then --- and Markdown body. Quote YAML strings containing colon-space, or use a YAML block scalar.",
                        "body": "Applicability, execution instructions, result checks, known limitations. Use the candidate directory returned here for reusable scripts.",
                    },
                    "next": "Write SKILL.md/scripts here. For Python use environment.create_command if not_created, "
                            "then its absolute pip_command/python_command; keep dependencies in this .venv. "
                            "skill_check with config.command, then skill_publish and skill_load."}
        if operation == "skill_rollback":
            state = _read_json(ROOT / "active.json")
            current = state.get(name) or {}
            previous = current.get("previous")
            if not isinstance(previous, dict):
                raise ValueError("No previously activated version exists")
            package = validated_package(name, previous.get("candidate_id", ""), expected=previous)
            state[name] = {**previous, "previous": {key: value for key, value in current.items() if key != "previous"}}
            _write_json(ROOT / "active.json", state, activation=True)
            logger.info("skill_rolled_back name=%s candidate=%s", name, previous["candidate_id"])
            return {"ok": True, "name": name, "status": "rolled_back", **state[name],
                    "environment": package["environment"]}
        directory, receipt_path, metadata = _candidate(name, candidate_id)
        _ensure_inactive(name, candidate_id)
        if operation == "skill_discard":
            # The caller explicitly chose this managed candidate. Active and
            # rollback versions were excluded above; external paths never enter.
            if directory.resolve() != _directory(name, candidate_id):
                raise ValueError("Invalid managed candidate path")
            _check_cancelled()
            shutil.rmtree(directory)
            receipt_path.unlink()
            logger.info("skill_candidate_discarded name=%s candidate=%s", name, candidate_id)
            return {"ok": True, "name": name, "candidate_id": candidate_id, "discarded": True}
        if operation == "skill_check":
            from app.application.code_agent import task_skills
            from app.application.code_agent.tools._run import tool_run_bash
            command = config.get("command")
            if metadata.get("revision"):
                raise ValueError("Create a new candidate to change a previously published version")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("config.command must run a meaningful package verification")
            task_skills.read_package(name, directory)
            environment = _python_environment(directory, required=metadata.get("environment") == "python")
            before = _digest(directory)
            # Invalidate an older receipt BEFORE a check which may fail/Stop.
            metadata.update(status="checking", sha256=None,
                            **({"environment": "python"} if environment["status"] == "ready" else {}))
            _write_json(receipt_path, metadata)
            options = ({"env_overrides": _python_check_environment(environment)}
                       if environment["status"] == "ready" else {})
            check = tool_run_bash(directory, command=command, **options)
            after = _digest(directory)
            passed = check.get("ok") is True and check.get("exit_code") == 0 and before == after
            metadata.update(status="verified" if passed else "failed", sha256=after if passed else None,
                            check={"command": command, "exit_code": check.get("exit_code"),
                                   "source_unchanged": before == after})
            _write_json(receipt_path, metadata)
            logger.info("skill_candidate_checked name=%s candidate=%s passed=%s", name, candidate_id, passed)
            return {"ok": passed, "name": name, "candidate_id": candidate_id,
                    "environment": environment,
                    "status": "completed" if passed else ("cancelled" if check.get("error") == "cancelled" else "failed"),
                    "verification": metadata["check"],
                    "text": check.get("text", ""),
                    **({} if passed else {"error": check.get("error") or (
                        "package_changed_during_check" if before != after else "verification_failed"
                    )})}
        if operation == "skill_publish":
            if metadata.get("status") != "verified" or _digest(directory) != metadata.get("sha256"):
                raise ValueError("Verify the current candidate before activation; its files or dependencies changed")
            environment = _python_environment(directory, required=metadata.get("environment") == "python")
            repository = _managed(ROOT / "history" / name)
            repository.mkdir(parents=True, exist_ok=True)
            if not (repository / ".git").exists():
                _git(repository, "init", "--initial-branch=main")
                _git(repository, "config", "user.name", "Elira")
                _git(repository, "config", "user.email", "elira@localhost")
                _git(repository, "config", "core.autocrlf", "false")
            # Git tracks reusable source/locks; the activation receipt additionally
            # hashes installed dependencies. Model weights should live outside it.
            source_root = _managed(repository / "package")
            if source_root.exists():
                _check_cancelled()
                shutil.rmtree(source_root)
            source_root.mkdir()
            for source in _files(directory, include_dependencies=False):
                relative = source.relative_to(directory)
                _check_cancelled()
                target = source_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            # Highest-precedence attributes preserve literal bytes, including
            # a package's own .gitattributes/.gitignore as versioned source.
            attributes = _managed(repository / ".git" / "info" / "attributes")
            attributes.parent.mkdir(exist_ok=True)
            attributes.write_text("* -text -filter -ident -working-tree-encoding\n", encoding="utf-8", newline="\n")
            _git(repository, "add", "--force", "--all", "--", "package")
            _git(repository, "commit", "--allow-empty", "-m", f"Verified skill {name} {candidate_id}")
            revision = _git(repository, "rev-parse", "HEAD")
            if _digest(directory) != metadata["sha256"]:
                raise ValueError("Candidate changed during publication; run skill_check again")
            if _digest(directory, include_dependencies=False) != _digest(source_root, include_dependencies=False):
                raise ValueError("Published Git source differs from the verified candidate")
            metadata["revision"] = revision
            _write_json(receipt_path, metadata)
            state = _read_json(ROOT / "active.json")
            current = state.get(name)
            previous = {}
            for prior in (current, current.get("previous") if isinstance(current, dict) else None):
                if not isinstance(prior, dict):
                    continue
                reference = {key: value for key, value in prior.items() if key != "previous"}
                try:
                    validated_package(name, reference.get("candidate_id", ""), expected=reference)
                except (OSError, ValueError, TypeError, RuntimeError) as exc:
                    logger.warning("skill_rollback_reference_skipped name=%s candidate=%s reason=%s",
                                   name, reference.get("candidate_id"), type(exc).__name__)
                    continue
                previous = reference
                break
            state[name] = {"candidate_id": candidate_id, "revision": revision, "sha256": metadata["sha256"],
                           **({"previous": previous} if previous else {})}
            # This single rename is the publication point. Old in-flight skill
            # snapshots and script paths continue to refer to their old version.
            _write_json(ROOT / "active.json", state, activation=True)
            logger.info("skill_published name=%s candidate=%s revision=%s", name, candidate_id, revision)
            return {"ok": True, "name": name, "status": "active", "directory": str(directory),
                    "environment": environment,
                    **state[name], "next": "skill_load to use this version now; MCP still uses mcp_upsert/start."}
        raise ValueError(f"Unsupported skill development operation: {operation}")
