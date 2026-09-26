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
import shutil
import subprocess
import tempfile
import threading
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


class _Cancelled(Exception):
    pass


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


def active_package(name: str) -> dict[str, str] | None:
    entry = _read_json(ROOT / "active.json").get(_name(name))
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError("Invalid active skill entry")
    return validated_package(name, entry.get("candidate_id", ""), expected=entry)


def validated_package(
    name: str, candidate_id: str, *, expected: dict[str, Any] | None = None,
) -> dict[str, str]:
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
    if _digest(directory) != digest:
        raise ValueError("Skill package files or dependencies changed since verification")
    return {
        "candidate_id": candidate_id, "package_sha256": digest,
        "revision": revision, "directory": str(directory),
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
            return {"ok": True, "name": name,
                    "active": _read_json(ROOT / "active.json").get(name),
                    "repository": str(_managed(ROOT / "history" / name))}
        if operation == "skill_create":
            candidate_id = uuid.uuid4().hex
            directory = _directory(name, candidate_id)
            active = active_package(name)
            current = Path(active["directory"]) if active else None
            if current is None:
                from app.application.code_agent.task_skills import SKILLS_ROOT
                installed = SKILLS_ROOT / name
                current = installed if installed.is_dir() else None
            directory.mkdir(parents=True)
            if current is not None:
                for source in _files(current, include_dependencies=False):
                    # Python environments are path-bound: recreate in the new
                    # candidate, instead of copying a previous candidate's venv.
                    relative = source.relative_to(current)
                    _check_cancelled()
                    destination = directory / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            _write_json(ROOT / "receipts" / f"{candidate_id}.json",
                        {"name": name, "candidate_id": candidate_id, "status": "candidate"})
            logger.info("skill_candidate_created name=%s candidate=%s", name, candidate_id)
            return {"ok": True, "name": name, "candidate_id": candidate_id,
                    "directory": str(directory), "status": "candidate",
                    "next": "Write SKILL.md and scripts with file tools; create candidate-local dependencies; skill_check with config.command."}
        if operation == "skill_rollback":
            state = _read_json(ROOT / "active.json")
            current = state.get(name) or {}
            previous = current.get("previous")
            if not isinstance(previous, dict):
                raise ValueError("No previously activated version exists")
            validated_package(name, previous.get("candidate_id", ""), expected=previous)
            state[name] = {**previous, "previous": {key: value for key, value in current.items() if key != "previous"}}
            _write_json(ROOT / "active.json", state, activation=True)
            logger.info("skill_rolled_back name=%s candidate=%s", name, previous["candidate_id"])
            return {"ok": True, "name": name, "status": "rolled_back", **state[name]}
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
            before = _digest(directory)
            # Invalidate an older receipt BEFORE a check which may fail/Stop.
            metadata.update(status="checking", sha256=None)
            _write_json(receipt_path, metadata)
            check = tool_run_bash(directory, command=command)
            after = _digest(directory)
            passed = check.get("ok") is True and check.get("exit_code") == 0 and before == after
            metadata.update(status="verified" if passed else "failed", sha256=after if passed else None,
                            check={"command": command, "exit_code": check.get("exit_code"),
                                   "source_unchanged": before == after})
            _write_json(receipt_path, metadata)
            logger.info("skill_candidate_checked name=%s candidate=%s passed=%s", name, candidate_id, passed)
            return {"ok": passed, "name": name, "candidate_id": candidate_id,
                    "status": "completed" if passed else ("cancelled" if check.get("error") == "cancelled" else "failed"),
                    "verification": metadata["check"],
                    "text": check.get("text", ""),
                    **({} if passed else {"error": check.get("error") or (
                        "package_changed_during_check" if before != after else "verification_failed"
                    )})}
        if operation == "skill_publish":
            if metadata.get("status") != "verified" or _digest(directory) != metadata.get("sha256"):
                raise ValueError("Verify the current candidate before activation; its files or dependencies changed")
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
            previous = {key: value for key, value in (state.get(name) or {}).items() if key != "previous"}
            state[name] = {"candidate_id": candidate_id, "revision": revision, "sha256": metadata["sha256"],
                           **({"previous": previous} if previous else {})}
            # This single rename is the publication point. Old in-flight skill
            # snapshots and script paths continue to refer to their old version.
            _write_json(ROOT / "active.json", state, activation=True)
            logger.info("skill_published name=%s candidate=%s revision=%s", name, candidate_id, revision)
            return {"ok": True, "name": name, "status": "active", "directory": str(directory),
                    **state[name], "next": "skill_load to use this version now; MCP still uses mcp_upsert/start."}
        raise ValueError(f"Unsupported skill development operation: {operation}")
