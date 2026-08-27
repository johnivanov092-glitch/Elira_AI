from __future__ import annotations

import contextvars
import os
import re
import shlex
import subprocess
import sys
import threading
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
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
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


def kill_run_processes(run_id: str) -> int:
    """Kill every live shell process spawned by `run_id`. Called by the agent
    loop's cancel path so Stop aborts a hung command immediately. Returns the
    number of processes signalled."""
    with _LIVE_SHELL_LOCK:
        procs = list(_LIVE_SHELL_PROCS.get(run_id, ()))
        _KILLED_RUN_IDS.add(run_id)
    killed = 0
    for proc in procs:
        try:
            if proc.poll() is None:
                _kill_proc_tree(proc)
                killed += 1
        except Exception:
            # Process may have already exited between poll and kill.
            pass
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


# ─── Raw-SSH-via-run_bash failure guidance ──────────────────────────────────
#
# A raw `ssh <host> "<remote command>"` issued through run_bash can be a quoting
# trap, especially against Windows. The ssh_* tools avoid those layers. This is
# guidance, not a security boundary: run_bash is already governed by the active
# approval mode, and the old hard redirect created failures even when raw SSH was
# valid. The caller now appends this hint only after the real command fails.
_RAW_SSH_OVERRIDE = "#!raw-ssh"
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPED_RAW_SSH_RE = re.compile(
    r"(?:^|\s)(?:-command|-c|/c)\s+[`\"']*\s*(ssh\s+.+)$",
    re.IGNORECASE | re.DOTALL,
)
# ssh options that consume the NEXT token as their argument (so the host isn't
# mistaken for an option value). `-p 22`, `-o BatchMode=yes`, `-i key`, `-J jump`…
_SSH_OPTS_WITH_ARG = frozenset({
    "-o", "-i", "-p", "-l", "-F", "-L", "-R", "-D", "-e", "-b", "-c", "-m",
    "-O", "-S", "-W", "-J", "-B", "-E", "-I", "-Q",
})
# Diagnostic forms that never carry a remote command → not the trap.
_SSH_DIAG_ONLY = frozenset({"-V", "-G"})


def _ssh_is_remote_exec(command: str) -> bool:
    """True when `command` is an ``ssh [opts] <host> <remote command>`` invocation —
    the quoting-hell trap. Handles option flags (incl. arg-taking ones like -o/-i/
    -p and their glued -oX/-p22 forms) and leading env assignments, so `ssh -o
    BatchMode=yes host "cmd"` (the live-run format the old regex missed) is caught.
    A bare `ssh host` (interactive, no command) and `ssh -V/-G` are NOT the trap."""
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        toks = command.split()
    i = 0
    while i < len(toks) and _ENV_ASSIGN_RE.match(toks[i]):
        i += 1
    if i >= len(toks) or toks[i].lower() != "ssh":
        return False  # not ssh (also excludes ssh-keygen/ssh-copy-id/scp/sshpass)
    i += 1
    while i < len(toks) and toks[i].startswith("-"):
        opt = toks[i]
        if opt in _SSH_DIAG_ONLY:
            return False
        i += 2 if opt in _SSH_OPTS_WITH_ARG else 1  # skip arg for -o/-i/-p/… (exact form)
    if i >= len(toks):
        return False  # options but no host
    i += 1  # skip the host
    return i < len(toks)  # a remote command follows the host → the trap


def _wrapped_raw_ssh_payload(command: str) -> str | None:
    """Return the nested `ssh ...` payload from shell wrappers such as
    `powershell.exe -Command "ssh host 'cmd ...'"` or `cmd /c ssh host ...`.

    This intentionally does NOT scan arbitrary command strings for `ssh`; it only
    fires when the wrapper payload itself starts with ssh, which is the same raw
    remote-exec trap hidden one shell layer deeper.
    """
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        toks = command.split()
    if not toks:
        return None
    exe = toks[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if exe not in {"powershell", "pwsh", "cmd"}:
        return None
    m = _WRAPPED_RAW_SSH_RE.search(command)
    if not m:
        return None
    return m.group(1).strip().strip("\"'")


def raw_ssh_redirect(command: str) -> str | None:
    """If *command* is a raw ``ssh [opts] <host> <cmd>`` remote-exec invocation,
    return actionable fallback guidance pointing at the ssh_* tools; else None.
    The caller does not block execution. The explicit ``#!raw-ssh`` marker merely
    suppresses the fallback hint."""
    cmd = (command or "").strip()
    if not cmd or _RAW_SSH_OVERRIDE in cmd:
        return None
    wrapped = _wrapped_raw_ssh_payload(cmd)
    if not (_ssh_is_remote_exec(cmd) or (wrapped and _ssh_is_remote_exec(wrapped))):
        return None
    return (
        "Подсказка: raw `ssh …` через run_bash может ломать экранирование: тело команды "
        "проходит 4 слоя (локальный shell → ssh → cmd.exe → PowerShell), и кавычки/"
        "пайпы/`$_` рвутся по дороге. Используй специализированные инструменты "
        "(экранировать НЕ нужно):\n"
        "• ssh_run(host, command) — команда на удалённом хосте;\n"
        "• ssh_read(host, path) — прочитать удалённый файл;\n"
        "• ssh_write(host, path, content) — записать файл (контент идёт через stdin);\n"
        "• ssh_run_ps(host, script) — PowerShell-скрипт на Windows-хосте (base64, "
        "без quoting).\n"
        "host бери из ssh_list_hosts."
    )
