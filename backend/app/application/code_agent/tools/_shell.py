from __future__ import annotations

import contextvars
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any


_SHELL_STDOUT_LIMIT = 16000
_SHELL_STDERR_LIMIT = 6000

# Read-only command prefixes that auto-execute without user approval.
# A command is safe if it starts with one of these prefixes (case-insensitive).
# Commands outside this list follow the selected Workflow permission mode.
# ─── Cancellable shell processes ────────────────────────────────────────────
#
# The Stop button used to do nothing while a shell tool was running: the
# executor runs each tool in a daemon worker thread and blocks on it, so a
# `threading.Event` cancel flag set by the HTTP /cancel route is never *read*
# until the blocking subprocess returns. A Python
# event cannot interrupt a foreign synchronous call.
#
# Fix: tool_run_bash launches the shell via Popen (not subprocess.run) and
# registers the live process against the current run_id. request_cancel can
# then reach in and proc.kill() the actual OS process, so Stop aborts a hung
# command in a fraction of a second.
#
# The run_id reaches the tool via a ContextVar set by the executor's worker
# thread (same thread that calls the tool synchronously — no cross-thread
# propagation needed). When unset (e.g. direct unit calls) the tool still runs,
# just without cancel registration.
_CURRENT_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "code_agent_current_run_id", default=None
)

# run_id -> set of live Popen objects spawned by that run.
_LIVE_SHELL_PROCS: dict[str, set[subprocess.Popen]] = {}
_LIVE_SHELL_LOCK = threading.Lock()
_RUN_CANCEL_CALLBACKS: dict[str, dict[int, Callable[[], None]]] = {}
_NEXT_CANCEL_CALLBACK_ID = 0
# run_ids whose processes were explicitly killed by the Stop path. The tool
# checks this to report "Прервано пользователем" regardless of the OS exit code
# (taskkill on Windows yields a positive code, so we can't infer Stop from it).
_KILLED_RUN_IDS: set[str] = set()

_IS_WINDOWS = sys.platform.startswith("win")


def _new_process_group_kwargs() -> dict[str, Any]:
    """Popen kwargs that put the child in its own killable process group.

    On Windows a ``shell=True`` launch wraps the command in ``cmd.exe /c``;
    ``proc.kill()`` would only kill cmd.exe and orphan the real child. Giving
    the child its own process group lets ``taskkill /T`` (resp. ``killpg`` on
    POSIX) take down the whole tree — which is what makes Stop actually work.
    """
    if _IS_WINDOWS:
        # CREATE_NEW_PROCESS_GROUP so the cmd.exe + its children form a group.
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """Kill *proc* and every descendant it spawned.

    Bare ``proc.kill()`` is not enough on Windows: a ``shell=True`` command runs
    under ``cmd.exe``, and killing cmd.exe orphans the real worker (python.exe,
    node.exe, a dev server, …) which keeps running and holding the run alive.
    """
    if proc.poll() is not None:
        return
    pid = proc.pid
    if _IS_WINDOWS:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if result.returncode == 0:
                return
        except Exception:
            # taskkill missing/failed — fall back to the single-process kill.
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), 9)  # SIGKILL the whole group
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def set_current_run_id(run_id: str | None) -> contextvars.Token:
    """Bind run_id to the calling thread so shell tools can register their
    process for cancellation. Returns the token for later reset()."""
    return _CURRENT_RUN_ID.set(run_id)


def get_current_run_id() -> str:
    """The run_id bound to the current thread by the executor — authoritative,
    set from ToolExecutionRequest.run_id, NEVER from model-supplied tool args.
    Empty string if unset. Use this when a tool handler needs its own run_id."""
    return str(_CURRENT_RUN_ID.get() or "")


def reset_current_run_id(token: contextvars.Token) -> None:
    try:
        _CURRENT_RUN_ID.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. set on another thread) — ignore.
        pass


def _register_shell_proc(run_id: str | None, proc: subprocess.Popen) -> None:
    if not run_id:
        return
    stop_already_requested = False
    with _LIVE_SHELL_LOCK:
        # Stop may arrive after tool_started but before the executor worker has
        # spawned/registered its process. Keep registration and the cancelled
        # marker check under one lock so that race cannot orphan a new child.
        stop_already_requested = run_id in _KILLED_RUN_IDS
        if not stop_already_requested:
            _LIVE_SHELL_PROCS.setdefault(run_id, set()).add(proc)
    if stop_already_requested:
        _kill_proc_tree(proc)


def _unregister_shell_proc(run_id: str | None, proc: subprocess.Popen) -> None:
    if not run_id:
        return
    with _LIVE_SHELL_LOCK:
        procs = _LIVE_SHELL_PROCS.get(run_id)
        if procs is not None:
            procs.discard(proc)
            if not procs:
                _LIVE_SHELL_PROCS.pop(run_id, None)


def register_run_process(proc: subprocess.Popen) -> str:
    """Register any tool-owned subprocess so Workflow Stop can kill its tree."""
    run_id = get_current_run_id()
    _register_shell_proc(run_id, proc)
    return run_id


def unregister_run_process(run_id: str, proc: subprocess.Popen) -> None:
    """Remove a process previously registered with register_run_process."""
    _unregister_shell_proc(run_id, proc)


def register_run_cancel_callback(
    callback: Callable[[], None],
) -> tuple[str, int] | None:
    """Bind a provider-level abort hook to the current Workflow run.

    This is for blocking transports without a directly owned ``Popen`` (for
    example HTTP MCP). Registration shares the shell Stop marker so a Stop that
    races ahead of provider setup invokes the callback immediately.
    """
    run_id = get_current_run_id()
    if not run_id:
        return None
    global _NEXT_CANCEL_CALLBACK_ID
    call_now = False
    with _LIVE_SHELL_LOCK:
        if run_id in _KILLED_RUN_IDS:
            call_now = True
            token = None
        else:
            _NEXT_CANCEL_CALLBACK_ID += 1
            callback_id = _NEXT_CANCEL_CALLBACK_ID
            _RUN_CANCEL_CALLBACKS.setdefault(run_id, {})[callback_id] = callback
            token = (run_id, callback_id)
    if call_now:
        callback()
    return token


def unregister_run_cancel_callback(token: tuple[str, int] | None) -> None:
    if token is None:
        return
    run_id, callback_id = token
    with _LIVE_SHELL_LOCK:
        callbacks = _RUN_CANCEL_CALLBACKS.get(run_id)
        if callbacks is None:
            return
        callbacks.pop(callback_id, None)
        if not callbacks:
            _RUN_CANCEL_CALLBACKS.pop(run_id, None)


def cancel_run_callbacks(run_id: str) -> int:
    """Invoke every provider abort hook, retaining failed hooks for retry."""
    with _LIVE_SHELL_LOCK:
        _KILLED_RUN_IDS.add(run_id)
        callbacks = list(_RUN_CANCEL_CALLBACKS.get(run_id, {}).items())
    invoked = 0
    errors: list[Exception] = []
    for callback_id, callback in callbacks:
        try:
            callback()
            invoked += 1
        except Exception as exc:
            errors.append(exc)
            continue
        with _LIVE_SHELL_LOCK:
            registered = _RUN_CANCEL_CALLBACKS.get(run_id)
            if registered and registered.get(callback_id) is callback:
                registered.pop(callback_id, None)
                if not registered:
                    _RUN_CANCEL_CALLBACKS.pop(run_id, None)
    if errors:
        raise RuntimeError(
            f"{len(errors)} provider cancellation callback(s) failed: {errors[0]}"
        ) from errors[0]
    return invoked


def kill_run_processes(run_id: str) -> int:
    """Kill every live shell process spawned by `run_id`. Called by the agent
    loop's cancel path so Stop aborts a hung command immediately. Returns the
    number of processes confirmed stopped and raises if any remain alive."""
    with _LIVE_SHELL_LOCK:
        procs = list(_LIVE_SHELL_PROCS.get(run_id, ()))
        _KILLED_RUN_IDS.add(run_id)
    killed = 0
    failures: list[Exception] = []
    for proc in procs:
        try:
            if proc.poll() is None:
                _kill_proc_tree(proc)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    # A process may exit between wait() raising and poll().
                    pass
                if proc.poll() is None:
                    failures.append(
                        RuntimeError(
                            f"process tree {getattr(proc, 'pid', '?')} is still alive"
                        )
                    )
                else:
                    killed += 1
        except Exception as exc:
            try:
                still_alive = proc.poll() is None
            except Exception:
                still_alive = True
            if still_alive:
                failures.append(exc)
            else:
                killed += 1
    if failures:
        raise RuntimeError(
            f"{len(failures)} process tree(s) could not be terminated: {failures[0]}"
        ) from failures[0]
    return killed


def clear_run_stop_marker(run_id: str) -> None:
    """Start a fresh execution generation for a durable run ID.

    Resume intentionally reuses the run ID. A Stop received while no shell was
    live leaves only the marker, so registration of the resumed generator must
    clear it before any new tool process can be owned by that run.
    """
    with _LIVE_SHELL_LOCK:
        _KILLED_RUN_IDS.discard(str(run_id or ""))


def run_was_stopped(run_id: str | None = None) -> bool:
    """Whether Workflow Stop was signalled for this tool run."""
    effective_run_id = str(run_id or get_current_run_id() or "")
    if not effective_run_id:
        return False
    with _LIVE_SHELL_LOCK:
        return effective_run_id in _KILLED_RUN_IDS
