from __future__ import annotations

import contextvars
import os
import re
import shlex
import subprocess
import sys
import threading
from typing import Any


# Per-run_bash hard cap. Raised 120→300 once the tool-execution heartbeat
# (agent_loop._exec_with_heartbeat) began keeping the SSE alive during a long tool,
# so a legitimately-medium command (build, full test suite, medium download) can
# finish instead of being cut at 120s. Genuinely long / background work (big
# downloads, watchers, dev servers) still belongs in run_server, not a blocking
# run_bash — see the prompt guidance.
_SHELL_TIMEOUT_MAX = 300
_SHELL_STDOUT_LIMIT = 16000
_SHELL_STDERR_LIMIT = 6000
# Catastrophic / irreversible — BLOCKED entirely, never run (even with approval
# or bypass). System-wipe, mass recursive delete, host shutdown, fork bomb.
_BLOCKED_SHELL_FRAGMENTS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "format c:",
    "shutdown",
    "reboot",
    ":(){:|:&};:",
    "deltree",
    "remove-item -recurse",
    "del /s",
    "rd /s",
    "rmdir /s",
)

# Destructive-but-LEGITIMATE — allowed, but must ALWAYS be confirmed by the user,
# even in bypass mode (ordinary side-effects auto-approve in bypass; these do
# not). "ask, never auto." Delete files, discard/rewrite git state, drop/truncate
# DB, docker rm/prune, kill processes, uninstall packages, recursive chmod/chown.
_CRITICAL_SHELL_PREFIXES = (
    "rm ", "del ", "erase ", "rmdir", "remove-item",
    "git reset", "git clean", "git checkout --", "git push --force",
    "git push -f", "git push --f", "git branch -d", "git tag -d",
    "git stash drop", "git stash clear",
    "docker rm", "docker rmi", "docker volume rm", "docker network rm",
    "docker system prune", "docker image prune", "docker container prune",
    "docker compose down", "docker-compose down",
    "kill ", "pkill", "killall", "taskkill",
    "pip uninstall", "npm uninstall", "npm unpublish",
    "chmod -r", "chown -r",
)
_CRITICAL_SHELL_SUBSTR = (
    "drop table", "drop database", "truncate table", "delete from",
)


# Read-only command prefixes that auto-execute without user approval.
# A command is safe if it starts with one of these prefixes (case-insensitive).
# Anything not in this list and not in _BLOCKED_SHELL_FRAGMENTS requires approval.
_SHELL_READONLY_PREFIXES: tuple[str, ...] = (
    # VCS read-only
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "git stash list", "git tag", "git fetch --dry-run",
    "git ls-files", "git describe", "git rev-parse",
    "git config --get ", "git config --list", "git cat-file ",
    "git blame ", "git shortlog", "git reflog",
    # File listing / reading
    "ls", "dir", "find ", "tree",
    "cat ", "head ", "tail ", "less ", "more ", "type ",
    "wc ", "file ", "stat ", "realpath ", "readlink ",
    "basename ", "dirname ",
    # Search
    "grep ", "egrep ", "fgrep ", "rg ", "ag ",
    # System info / status
    "echo ", "pwd", "whoami", "id", "hostname",
    "which ", "where ", "command -v",
    "env", "printenv", "set",
    "ps ", "ps aux", "top -bn1",
    "df ", "du -sh", "free ",
    # Python / package status
    "python --version", "python3 --version", "python -V", "python3 -V",
    "pip list", "pip show ", "pip freeze", "pip check",
    "uv list", "poetry show",
    # Testing — collect only
    "pytest --collect-only", "pytest -v --collect-only",
    "jest --listTests", "cargo test -- --list",
    # Node / npm status
    "node --version", "npm list", "yarn list", "pnpm list",
    "npm outdated", "npm audit",
    # Docker status
    "docker ps", "docker images", "docker stats", "docker info",
    "docker compose ps", "docker-compose ps",
    # Rust / Go / etc.
    "cargo --version", "rustc --version", "go version",
    # Network / DNS read-only
    "nslookup ", "dig ", "host ", "ping ",
)


# ─── Cancellable shell processes ────────────────────────────────────────────
#
# The Stop button used to do nothing while a shell tool was running: the
# executor runs each tool in a daemon worker thread and blocks on it, so a
# `threading.Event` cancel flag set by the HTTP /cancel route is never *read*
# until the blocking subprocess returns (up to the shell timeout). A Python
# event cannot interrupt a foreign synchronous call.
#
# Fix: tool_run_bash launches the shell via Popen (not subprocess.run) and
# registers the live process against the current run_id. request_cancel can
# then reach in and proc.kill() the actual OS process, so Stop aborts a hung
# command in a fraction of a second instead of waiting out the timeout.
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


def reset_current_run_id(token: contextvars.Token) -> None:
    try:
        _CURRENT_RUN_ID.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. set on another thread) — ignore.
        pass


def _register_shell_proc(run_id: str | None, proc: subprocess.Popen) -> None:
    if not run_id:
        return
    with _LIVE_SHELL_LOCK:
        _LIVE_SHELL_PROCS.setdefault(run_id, set()).add(proc)


def _unregister_shell_proc(run_id: str | None, proc: subprocess.Popen) -> None:
    if not run_id:
        return
    with _LIVE_SHELL_LOCK:
        procs = _LIVE_SHELL_PROCS.get(run_id)
        if procs is not None:
            procs.discard(proc)
            if not procs:
                _LIVE_SHELL_PROCS.pop(run_id, None)


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


def is_shell_safe(command: str) -> bool:
    """Return True if *command* is in the read-only shell allowlist.

    Commands in this set auto-execute without user approval. Matching is
    prefix-based and case-insensitive so ``git status --short`` passes as
    a ``git status`` prefix.

    Prefixes that end with a space (e.g. ``"cat "``) match any command that
    starts with that string (``cat README.md``). Prefixes without a trailing
    space (e.g. ``"git status"``) are matched as exact or word-boundary
    (``git status``, ``git status --short``).
    """
    cmd = (command or "").strip().lower()
    if not cmd:
        return False
    # Reject any command containing shell composition or redirection metacharacters.
    # These could chain an unsafe subcommand past the prefix check.
    _UNSAFE_METACHAR = ("&&", "||", ";;", "|", ";", ">", "<", "`", "$(", "\n", "\r")
    if any(meta in cmd for meta in _UNSAFE_METACHAR):
        return False
    # Windows cmd.exe expands %VAR% to environment-variable values at exec time
    # (e.g. ``echo %TOKEN%`` / ``find %USERPROFILE%``), which would leak env
    # values or inject expanded arguments past the approval gate. On POSIX "%"
    # is harmless (``git log --format=%H``, ``printf %s``), so only guard it on
    # Windows.
    if sys.platform == "win32" and "%" in cmd:
        return False
    for prefix in _SHELL_READONLY_PREFIXES:
        p = prefix.lower()
        if p.endswith(" "):
            if cmd.startswith(p) or cmd == p.rstrip():
                return True
        else:
            if cmd == p or cmd.startswith(p + " ") or cmd.startswith(p + "\t"):
                return True
    return False


def _blocked_shell_fragment(command: str) -> str | None:
    lowered = (command or "").strip().lower()
    return next((fragment for fragment in _BLOCKED_SHELL_FRAGMENTS if fragment in lowered), None)


def is_shell_critical(command: str) -> bool:
    """True for destructive-but-legitimate commands that must ALWAYS be confirmed
    by the user, even under bypass (delete files, git reset/clean/checkout--/
    force-push/branch-delete/stash-drop, drop/truncate DB, docker rm/prune/down,
    kill processes, package uninstall, recursive chmod/chown). Catastrophic
    commands are blocked entirely (_blocked_shell_fragment); this is the "ask,
    never auto" tier. Errs toward asking (a false positive just adds one prompt)."""
    cmd = (command or "").strip().lower()
    if not cmd:
        return False
    for sub in _CRITICAL_SHELL_SUBSTR:
        if sub in cmd:
            return True
    # Check each shell segment so "echo hi && rm x" is caught by the rm segment.
    for seg in re.split(r"&&|\|\||;|\||\n|\r|`|\$\(", cmd):
        seg = seg.strip()
        if any(seg.startswith(pat) for pat in _CRITICAL_SHELL_PREFIXES):
            return True
    return False


# ─── Raw-SSH-via-run_bash redirect ──────────────────────────────────────────
#
# A raw `ssh <host> "<remote command>"` issued through run_bash is the quoting-
# hell trap that burned a whole real run: the body is parsed by the LOCAL shell →
# ssh → the REMOTE shell (cmd.exe → PowerShell on Windows), so every quote / pipe /
# `$_` has to survive four layers. The ssh_* provider tools sidestep this entirely
# (content over stdin for ssh_write; base64 EncodedCommand for ssh_run_ps/ssh_read),
# turning ~80 escaping attempts into ONE clean call. So run_bash REDIRECTS a raw
# ssh invocation to the right tool — it does NOT ban it: the model can still force
# the raw pipe (tunnels, scp-style one-offs) with an explicit `#!raw-ssh` marker.
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
    return an actionable redirect message pointing at the ssh_* tools; else None.
    Returns None when the explicit ``#!raw-ssh`` override marker is present."""
    cmd = (command or "").strip()
    if not cmd or _RAW_SSH_OVERRIDE in cmd:
        return None
    wrapped = _wrapped_raw_ssh_payload(cmd)
    if not (_ssh_is_remote_exec(cmd) or (wrapped and _ssh_is_remote_exec(wrapped))):
        return None
    return (
        "ERROR: raw `ssh …` через run_bash — ловушка экранирования: тело команды "
        "проходит 4 слоя (локальный shell → ssh → cmd.exe → PowerShell), и кавычки/"
        "пайпы/`$_` рвутся по дороге. Используй специализированные инструменты "
        "(экранировать НЕ нужно):\n"
        "• ssh_run(host, command) — команда на удалённом хосте;\n"
        "• ssh_read(host, path) — прочитать удалённый файл;\n"
        "• ssh_write(host, path, content) — записать файл (контент идёт через stdin);\n"
        "• ssh_run_ps(host, script) — PowerShell-скрипт на Windows-хосте (base64, "
        "без quoting).\n"
        "host бери из ssh_list_hosts. Если raw ssh нужен ОСОЗНАННО (туннель/scp) — "
        f"добавь в конец команды маркер {_RAW_SSH_OVERRIDE} ."
    )
