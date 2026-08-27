from __future__ import annotations

import os
import re
import shlex
import subprocess
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

from app.application.code_agent.tools._background_jobs import (
    RecoveredJobProcess,
    activate_job,
    discard_prepared_job,
    prepare_job,
    reconcile_job_records,
    write_terminal_result,
)
from app.application.code_agent.tools._sandbox import _truncate_middle
from app.application.code_agent.tools._shell import (
    _CURRENT_RUN_ID,
    _KILLED_RUN_IDS,
    _LIVE_SHELL_LOCK,
    _LIVE_SHELL_PROCS,
    _SHELL_STDERR_LIMIT,
    _SHELL_STDOUT_LIMIT,
    _kill_proc_tree,
    _new_process_group_kwargs,
    _register_shell_proc,
    _unregister_shell_proc,
    raw_ssh_redirect,
)


# Canonical subprocess-output decoder (Windows console mojibake fix) lives in one
# place now; kept as _decode_console here for the existing call sites and tests.
from app.infrastructure.encoding import decode_console as _decode_console
from app.core.redaction import redact_text


# Interpreters whose inline-script form (`python -c "<script>"`, `node -e …`)
# must bypass the shell. A MULTI-LINE script handed to `cmd.exe /c` on Windows is
# truncated at the first newline — the interpreter then runs an empty/garbled body
# (exit 0, no output), so the model sees nothing back and re-issues the same call
# until the loop-guard trips. Running argv directly (shell=False) delivers the
# whole script to the interpreter as ONE intact argument. Applied on every OS so
# behaviour is identical on Windows and Linux (on POSIX /bin/sh already coped, but
# uniformity beats a platform branch). Only EXACT `[interp, flag, script]` forms
# divert; pipes/redirects/`&&`/`dir` parse to more tokens and stay on the shell path.
_INLINE_SCRIPT_INTERPRETERS = frozenset(
    {"python", "python3", "py", "node", "nodejs", "deno", "ruby", "perl", "php"}
)


def _agent_child_env() -> dict[str, str]:
    """Full environment of Elira's current OS token for spawned tools.

    Product authorization is owned exclusively by the Workflow permission mode.
    The runtime does not silently remove credentials or Windows/toolchain state
    after the UI has authorized execution.
    """
    return dict(os.environ)
_INLINE_SCRIPT_FLAGS = frozenset({"-c", "-e", "--eval"})
_WINDOWS_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _job_process_group_kwargs(*, allow_breakaway: bool = True) -> dict[str, Any]:
    """Detach a durable worker from the backend console/process job.

    It still owns a new process group, so ``run_server(stop)``/Workflow Stop can
    terminate its full tree. ``CREATE_BREAKAWAY_FROM_JOB`` lets the worker
    survive hosts that place uvicorn in a kill-on-close Windows Job Object; some
    locked-down hosts reject breakaway, in which case the caller retries with
    console detachment only.
    """
    kwargs = _new_process_group_kwargs()
    if os.name != "nt":
        return kwargs
    flags = int(kwargs.get("creationflags") or 0)
    flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
    if allow_breakaway:
        flags |= _WINDOWS_CREATE_BREAKAWAY_FROM_JOB
    return {"creationflags": flags}


def _inline_script_argv(command: str) -> list[str] | None:
    """Return argv to run *command* WITHOUT a shell if it is a multi-line
    inline-script call (e.g. ``python -c "<script>"``); else None (keep the shell).

    Narrow by design: triggers ONLY on a newline-containing command that
    ``shlex``-parses to exactly ``[interpreter, flag, script]`` with a known
    interpreter and inline-script flag. Single-line commands are left alone (they
    already work), and anything with shell metacharacters yields extra tokens
    (``len != 3``) so pipes, redirects and ``dir`` keep flowing through the shell.
    """
    if "\n" not in command:
        return None
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(argv) != 3:
        return None
    interp = os.path.basename(argv[0]).lower()
    if interp.endswith(".exe"):
        interp = interp[:-4]
    if interp in _INLINE_SCRIPT_INTERPRETERS and argv[1] in _INLINE_SCRIPT_FLAGS:
        return argv
    return None


def tool_run_bash(project_root: Path, *, command: str, timeout: int = 60) -> dict[str, Any]:
    del timeout  # compatibility input; Workflow Stop owns termination
    cleaned_command = (command or "").strip()
    if not cleaned_command:
        return {"text": "ERROR: command is empty", "ok": False}
    # Raw SSH is allowed under the same approval policy as every other run_bash
    # command. Keep specialized-tool guidance only as a fallback after a real
    # non-zero exit; blocking it here produced artificial failures and loops.
    raw_ssh_hint = raw_ssh_redirect(cleaned_command)
    run_id = _CURRENT_RUN_ID.get()
    # Multi-line inline scripts (python -c "<…>", node -e …) are mangled by
    # cmd.exe /c, so run them via argv with no shell; everything else keeps the
    # shell path. The wait/kill/capture machinery below is identical either way.
    _argv = _inline_script_argv(cleaned_command)
    try:
        # Popen (not subprocess.run) so the live process is registered and can
        # be killed mid-flight by the Stop button.
        proc = subprocess.Popen(
            _argv if _argv is not None else cleaned_command,
            shell=_argv is None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Capture BYTES (not text=True): Windows console apps emit the OEM
            # codepage, and text=True would decode with the wrong ANSI default
            # (mojibake). Decoded via _decode_console below.
            cwd=str(project_root.resolve()),
            # Inherit the complete environment available to Elira's current
            # Windows token. Workflow permission is the authorization boundary.
            env=_agent_child_env(),
            # Close stdin: a shell tool must never block on input. Interactive
            # prompts (ssh host-key/password, apt, etc.) get EOF and fail fast.
            stdin=subprocess.DEVNULL,
            # Own process group so Stop can kill the whole tree, not just
            # the cmd.exe wrapper (which would orphan the real child on Windows).
            **_new_process_group_kwargs(),
        )
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}

    _register_shell_proc(run_id, proc)
    cancelled = False
    # Drain stdout/stderr in background threads so a chatty command can't fill
    # the OS pipe buffer and deadlock (child blocks on write → never exits →
    # poll() never completes). The main loop then only watches poll(),
    # which keeps the process killable mid-flight by the Stop button.
    out_buf: list[bytes] = []
    err_buf: list[bytes] = []

    def _drain(stream, sink: list[bytes]) -> None:
        try:
            for chunk in iter(lambda: stream.read(8192), b""):
                if not chunk:
                    break
                sink.append(chunk)
        except Exception:
            pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()
    try:
        # Poll so a Stop press (which proc.kill()s us from another thread) is
        # observed within ~0.1s.
        while True:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
    except Exception as exc:
        try:
            _kill_proc_tree(proc)
        except Exception:
            pass
        return {"text": f"ERROR: {exc}"}
    finally:
        # Let the readers finish flushing whatever the process wrote/buffered.
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        try:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
        _unregister_shell_proc(run_id, proc)
        # Stop is detected via the explicit kill registry (taskkill on Windows
        # yields a *positive* exit code, so the returncode alone can't tell a
        # Stop from a normal failure). Fall back to a negative code for the
        # POSIX direct-kill case where no run_id was bound.
        if run_id:
            with _LIVE_SHELL_LOCK:
                if run_id in _KILLED_RUN_IDS:
                    _KILLED_RUN_IDS.discard(run_id)
                    cancelled = True
        if not cancelled and proc.returncode is not None and proc.returncode < 0:
            cancelled = True

    out, err = _decode_console(b"".join(out_buf)), _decode_console(b"".join(err_buf))

    if cancelled:
        return {"text": f"$ {cleaned_command}\nПрервано пользователем (Стоп)."}

    stdout, stderr = out or "", err or ""
    parts = [f"$ {cleaned_command}", f"exit={proc.returncode}"]
    if stdout:
        parts.append(f"STDOUT:\n{_truncate_middle(stdout.rstrip(), _SHELL_STDOUT_LIMIT)}")
    if stderr:
        parts.append(f"STDERR:\n{_truncate_middle(stderr.rstrip(), _SHELL_STDERR_LIMIT)}")
    if proc.returncode not in (0, None) and raw_ssh_hint:
        parts.append(raw_ssh_hint)
    # exit_code travels in the meta so the UI can colour the call by SEMANTIC
    # success (a non-zero exit reads as failure) instead of "the process ran".
    # We keep the top-level `ok` unset (a non-zero exit isn't always a failure —
    # grep/findstr return 1 for "no match") so the executor's approval/verify
    # logic is unchanged; the UI decides how to render exit_code itself.
    return {"text": "\n".join(parts), "exit_code": proc.returncode}


# ─── run_server: background process launcher ────────────────────────────────
# Unlike run_bash (which waits until the command exits or Workflow Stop), run_server
# starts a long-lived process via Popen and returns IMMEDIATELY. The process is
# tracked in a module-level registry keyed by pid (tagged with the OWNING run_id)
# so it can be listed and stopped explicitly. Servers survive across turns and
# after a natural model answer. They stop only through run_server(action='stop')
# or an explicit Workflow Stop, which kills every process owned by that run.
# They are not in _LIVE_SHELL_PROCS; their own registry handles lifecycle.
# Output is
# captured to log files under the project's .elira/servers/ so the model can
# inspect startup without blocking.

_SERVER_LOG_DIRNAME = ".elira/servers"
_SERVER_STARTUP_GRACE = 1.5  # seconds to let the process crash-or-bind before reporting
_SERVER_URL_WAIT = 6.0       # extra seconds to wait for a dev server to PRINT its URL
_SERVER_LOG_TAIL_CHARS = 4000

# A dev server prints the URL it ACTUALLY bound — which differs from the requested
# port when it was taken (Vite auto-increments 5173→5174). Parsing the real URL is
# what lets the loopback verifier reach the agent's OWN server instead of guessing
# ports (or hitting a different app already on the requested port).
_SERVER_URL_RE = re.compile(
    r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|[\w.-]+):(\d{2,5})", re.IGNORECASE
)
_LOOPBACK_URL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "[::1]"})


def _parse_server_url(log_text: str) -> tuple[str, int] | None:
    """Extract the (canonical loopback URL, port) a dev server printed. Prefers a
    loopback 'Local:' URL over a LAN 'Network:' one, and the LAST match (so a Vite
    auto-increment to 5174 wins over the initial 5173 attempt). 0.0.0.0 is normalised
    to localhost for verification."""
    loopback: tuple[str, int] | None = None
    other: tuple[str, int] | None = None
    for m in _SERVER_URL_RE.finditer(log_text or ""):
        host, port = m.group(1).lower(), int(m.group(2))
        if host in _LOOPBACK_URL_HOSTS:
            loopback = (f"http://localhost:{port}", port)   # last loopback wins
        elif other is None:
            other = (f"http://{host}:{port}", port)
    return loopback or other


_WEB_DEV_MARKERS = (
    "vite", "next", "nuxt", "npm run dev", "yarn dev", "pnpm dev", "npm start",
    "webpack", "ng serve", "astro", "remix", "serve", "http-server", "live-server",
    "uvicorn", "flask run", "runserver", "gunicorn", "rails s", "php -s",
)


def _expects_web_url(command: str, port: int | None) -> bool:
    """Whether a run_server start is likely to bind an HTTP URL worth waiting for."""
    if port:
        return True
    low = (command or "").lower()
    return any(m in low for m in _WEB_DEV_MARKERS)


def _await_server_url(log_path: Path, deadline: float) -> tuple[str, int] | None:
    """Poll the server log for its bound URL until `deadline` (monotonic seconds)."""
    while True:
        found = _parse_server_url(_read_log_tail(log_path))
        if found:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.3)


class _ServerHandle:
    __slots__ = (
        "pid", "command", "proc", "log_path", "port", "url", "started_at",
        "run_id", "kind", "job_id", "job_record", "status", "exit_code",
        "finished_at", "recovered",
    )

    def __init__(self, pid: int, command: str, proc: Any,
                 log_path: Path, port: int | None, url: str | None = None,
                 run_id: str | None = None, kind: str = "server",
                 job_record: dict[str, Any] | None = None,
                 started_at: float | None = None,
                 recovered: bool = False) -> None:
        self.pid = pid
        self.command = command
        self.proc = proc
        self.log_path = log_path
        self.port = port
        self.url = url
        self.started_at = float(started_at or time.time())
        # The run that started this server owns it, so explicit Workflow Stop can
        # terminate the correct process tree without affecting unrelated servers.
        self.run_id = run_id
        self.kind = "job" if kind == "job" else "server"
        self.job_record = dict(job_record) if job_record else None
        self.job_id = str((job_record or {}).get("job_id") or "") or None
        self.status = str((job_record or {}).get("status") or "running")
        self.exit_code = (job_record or {}).get("exit_code")
        self.finished_at = (job_record or {}).get("finished_at")
        self.recovered = bool(recovered)


_LIVE_SERVERS: dict[int, _ServerHandle] = {}
_SERVERS_LOCK = threading.Lock()
_COMPLETED_JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60
_COMPLETED_JOB_RETAIN_LIMIT = 32


def _apply_job_record(handle: _ServerHandle, record: dict[str, Any]) -> str:
    handle.job_record = record
    handle.status = str(record.get("status") or "failed")
    handle.exit_code = record.get("exit_code")
    handle.finished_at = record.get("finished_at")
    return handle.status


def _refresh_job_handle(handle: _ServerHandle) -> str:
    if handle.kind != "job":
        return "running" if handle.proc.poll() is None else "failed"
    if handle.job_id:
        records, _summary = reconcile_job_records()
        record = next(
            (item for item in records if item.get("job_id") == handle.job_id),
            None,
        )
        if record is not None:
            return _apply_job_record(handle, record)
    returncode = handle.proc.poll()
    handle.exit_code = returncode
    handle.status = "running" if returncode is None else (
        "completed" if returncode == 0 else "failed"
    )
    if returncode is not None and handle.finished_at is None:
        handle.finished_at = time.time()
    return handle.status


def recover_background_jobs() -> dict[str, Any]:
    """Rebuild in-memory handles from the machine-local durable job journal."""
    records, summary = reconcile_job_records(wait_for_starting_s=5.0)
    recovered = 0
    with _SERVERS_LOCK:
        # A historical terminal record may share a reused PID with a newer live
        # worker. PID-addressed controls must always resolve to the live identity;
        # among terminal collisions retain the newest audit record.
        ordered = sorted(
            records,
            key=lambda item: (
                item.get("status") == "running",
                float(item.get("started_at") or 0),
            ),
            reverse=True,
        )
        for record in ordered:
            pid = int(record.get("pid") or 0)
            if pid <= 0:
                continue
            existing = _LIVE_SERVERS.get(pid)
            if existing is not None:
                if existing.kind == "job" and existing.job_id == record.get("job_id"):
                    existing.job_record = record
                    existing.status = str(record.get("status") or "failed")
                    existing.exit_code = record.get("exit_code")
                    existing.finished_at = record.get("finished_at")
                continue
            _LIVE_SERVERS[pid] = _ServerHandle(
                pid=pid,
                command=str(record.get("command") or ""),
                proc=RecoveredJobProcess(record),
                log_path=Path(str(record.get("log_path") or "")),
                port=None,
                run_id=str(record.get("run_id") or "") or None,
                kind="job",
                job_record=record,
                started_at=float(record.get("started_at") or time.time()),
                recovered=True,
            )
            recovered += 1
    return {"ok": True, "recovered": recovered, **summary}


def tracked_background_processes(run_id: str | None = None) -> list[dict[str, Any]]:
    """Structured observability view used by the API and Workflow UI."""
    _reap_dead_servers()
    with _SERVERS_LOCK:
        handles = list(_LIVE_SERVERS.values())
    processes: list[dict[str, Any]] = []
    for handle in handles:
        if run_id is not None and handle.run_id != run_id:
            continue
        if handle.kind == "job":
            status = handle.status
            exit_code = handle.exit_code
        else:
            exit_code = handle.proc.poll()
            status = "running" if exit_code is None else "failed"
        processes.append(
            {
                "pid": handle.pid,
                "job_id": handle.job_id,
                "kind": handle.kind,
                "status": status,
                "exit_code": exit_code,
                "port": handle.port,
                "url": handle.url,
                "command": handle.command,
                "run_id": handle.run_id,
                "log_path": str(handle.log_path),
                "age_s": max(0, int(time.time() - handle.started_at)),
                "recovered": handle.recovered,
            }
        )
    return sorted(processes, key=lambda item: int(item["age_s"]), reverse=True)


def _reap_dead_servers() -> None:
    """Drop exited servers; retain a bounded tail of completed job results."""
    with _SERVERS_LOCK:
        snapshot = list(_LIVE_SERVERS.values())
    job_handles = [handle for handle in snapshot if handle.kind == "job" and handle.job_id]
    if job_handles:
        records, _summary = reconcile_job_records()
        records_by_id = {str(record.get("job_id") or ""): record for record in records}
        for handle in job_handles:
            record = records_by_id.get(str(handle.job_id))
            if record is not None:
                _apply_job_record(handle, record)
    with _SERVERS_LOCK:
        now = time.time()
        dead = {
            pid for pid, h in _LIVE_SERVERS.items()
            if (h.proc.poll() is not None or h.status != "running") and (
                h.kind == "server"
                or now - float(h.finished_at or h.started_at)
                > _COMPLETED_JOB_RETENTION_SECONDS
            )
        }
        retained_jobs = sorted(
            (
                (pid, h) for pid, h in _LIVE_SERVERS.items()
                if pid not in dead and h.kind == "job" and h.status != "running"
            ),
            key=lambda item: float(item[1].finished_at or item[1].started_at),
            reverse=True,
        )
        dead.update(pid for pid, _ in retained_jobs[_COMPLETED_JOB_RETAIN_LIMIT:])
        for pid in dead:
            _LIVE_SERVERS.pop(pid, None)


def active_server_ports() -> set[int]:
    """Ports of dev servers this agent started and that are still alive."""
    _reap_dead_servers()
    with _SERVERS_LOCK:
        return {int(h.port) for h in _LIVE_SERVERS.values() if h.port}


# ─── R2 Server Lifecycle: runtime owns what it started ──────────────────────

def _port_listening(port: int, timeout: float = 0.5, host: str = "127.0.0.1") -> bool:
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _url_endpoint_listening(url: str, port: int) -> bool:
    """Probe the host the URL actually names — not a hardcoded 127.0.0.1. A server
    bound only to ::1 or to a LAN interface is ALIVE at its own address; probing the
    wrong loopback would read it as dead and invite a duplicate start (review #6/#9)."""
    m = re.match(r"https?://\[?([^/\]:]+)\]?", url or "")
    host = (m.group(1) if m else "127.0.0.1").lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "::"):
        return _port_listening(port) or _port_listening(port, host="::1")
    return _port_listening(port, host=host)


def url_is_live_server(url: str) -> bool:
    """True when `url` points at a tracked dev server whose PROCESS is alive and
    whose endpoint actually LISTENS. This is the liveness gate for server→browser
    redirects and auto-verifier browser probes — a stale URL from a stopped/crashed
    server must not steer verification at a dead endpoint."""
    m = _SERVER_URL_RE.search(url or "")
    if not m:
        return False
    port = int(m.group(2))
    _reap_dead_servers()
    with _SERVERS_LOCK:
        handles = [h for h in _LIVE_SERVERS.values() if h.port == port]
    if not any(h.proc.poll() is None for h in handles):
        return False
    return _url_endpoint_listening(url, port)


def run_owned_servers(run_id: str) -> list[dict[str, Any]]:
    """Alive background processes owned by this run, for reporting and Stop."""
    if not run_id:
        return []
    _reap_dead_servers()
    with _SERVERS_LOCK:
        handles = [h for h in _LIVE_SERVERS.values() if h.run_id == run_id]
    return [{
                "pid": h.pid, "port": h.port, "url": h.url,
                "command": h.command, "kind": h.kind,
            }
            for h in handles
            if (h.kind == "job" and h.status == "running")
            or (h.kind == "server" and h.proc.poll() is None)]


def _mark_job_cancelled(handle: _ServerHandle) -> None:
    if handle.kind != "job" or not handle.job_record:
        return
    payload = write_terminal_result(
        handle.job_record,
        status="cancelled",
        exit_code=None,
        error="cancelled by Workflow Stop or run_server(stop)",
    )
    handle.status = "cancelled"
    handle.exit_code = None
    handle.finished_at = payload["finished_at"]
    handle.job_record.update(payload)


def stop_run_servers(run_id: str) -> list[dict[str, Any]]:
    """Stop every live process owned by ``run_id``; retain cancelled job audit."""
    if not run_id:
        return []
    with _SERVERS_LOCK:
        owned = [(pid, h) for pid, h in _LIVE_SERVERS.items() if h.run_id == run_id]
    stopped: list[dict[str, Any]] = []
    for pid, h in owned:
        was_alive = False
        try:
            was_alive = h.proc.poll() is None
            if was_alive:
                _kill_proc_tree(h.proc)
                try:
                    h.proc.wait(timeout=5)
                except Exception:
                    pass
        except Exception:
            pass
        if h.proc.poll() is None:
            # The kill FAILED — keep the handle: an unkillable process must stay
            # tracked (list/stop_all), not silently leak.
            continue
        if was_alive:
            stopped.append({"pid": pid, "port": h.port, "url": h.url, "command": h.command})
        if h.kind == "job" and was_alive:
            _mark_job_cancelled(h)
        else:
            with _SERVERS_LOCK:
                _LIVE_SERVERS.pop(pid, None)
    return stopped


def _read_log_tail(log_path: Path, limit: int = _SERVER_LOG_TAIL_CHARS) -> str:
    try:
        # The server child writes RAW bytes to the log fd (OEM codepage on
        # Windows) — decode like every other console output, not as fixed UTF-8.
        data = _decode_console(log_path.read_bytes())
    except Exception:
        return ""
    return _truncate_middle(data.rstrip(), limit)


# ─── GUI auto-verification after run_server ─────────────────────────────────
# When the agent starts a GUI (a web dev-server *or* a native desktop window),
# it is blind to what actually rendered: a process that didn't crash can still
# have launched a broken/blank window. So right after a successful `start` we
# capture ONE screenshot and feed it back into the run, closing the perception
# loop the model otherwise lacks (this is the gap that made it "fix one error,
# create another" during GUI bring-up — it could not see the result of an edit).
#
#   * port set  → web app: screenshot http://localhost:<port> via Playwright
#                 (reuses skills.runtime.screenshot_url).
#   * no port   → native window: full-screen grab via Pillow.ImageGrab.
#
# Delivery is "screenshot + vision, with fallback": if VISION_ENABLED, the PNG
# is described by the vision model (:8004) and that text goes back to the agent
# so it can read what it built. If vision is off / unreachable, we degrade
# gracefully to the screenshot path + process status — never an exception, since
# this is best-effort instrumentation, not a gate.

_GUI_VERIFY_SETTLE = 1.0  # extra seconds for a window/page to paint before the shot


def _native_screenshot() -> dict[str, Any]:
    """Grab the full primary screen to a PNG in the shared generated-files dir.

    Mirrors the dict shape of skills.runtime.screenshot_url (ok/path/filename)
    so the caller treats web and native captures uniformly. Best-effort: returns
    {"ok": False, "error": ...} instead of raising when Pillow is missing or the
    grab fails (e.g. headless / no display)."""
    try:
        from PIL import ImageGrab
    except Exception as exc:  # pragma: no cover - optional dependency
        return {"ok": False, "error": f"native screenshot needs Pillow: {exc}"}
    try:
        from app.application.skills.runtime import OUTPUT_DIR
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": False, "error": f"output dir unavailable: {exc}"}

    fname = f"gui_native_{int(time.time())}.png"
    path = OUTPUT_DIR / fname
    try:
        img = ImageGrab.grab()
        img.save(str(path))
    except Exception as exc:
        return {"ok": False, "error": f"native grab failed: {exc}"}
    return {
        "ok": True,
        "path": str(path),
        "filename": fname,
        "download_url": f"/api/skills/download/{fname}",
        "view_url": f"/api/skills/view/{fname}",
    }


def _auto_verify_gui(handle: "_ServerHandle") -> str:
    """Capture + (optionally) describe the GUI a just-started server renders.

    Returns a human-readable block to append to the run_server result, or "" if
    capture is impossible. Never raises — GUI verification is best-effort and
    must not turn a healthy `start` into an error."""
    # Let the window/page paint a little past the bare crash-grace already spent.
    try:
        time.sleep(_GUI_VERIFY_SETTLE)
    except Exception:
        pass

    if handle.port:
        try:
            from app.application.skills.runtime import screenshot_url
            shot = screenshot_url(f"http://localhost:{handle.port}")
        except Exception as exc:
            shot = {"ok": False, "error": str(exc)}
        kind = f"web (http://localhost:{handle.port})"
    else:
        shot = _native_screenshot()
        kind = "native window"

    if not shot.get("ok"):
        return (
            f"🖼 GUI verification: could not capture {kind} "
            f"({shot.get('error') or 'unknown error'}). Process is running — "
            f"use run_server(action='logs', pid={handle.pid}) to inspect output."
        )

    shot_path = str(shot.get("path") or "")
    title = shot.get("title")
    header = f"🖼 GUI verification ({kind}): screenshot saved at {shot_path}"
    if title:
        header += f"\n  page title: {title}"

    # Vision channel with graceful fallback. Read the PNG bytes and describe via
    # the same :8004 path read_image uses; if vision is off/unreachable, return
    # the path + status so the agent can still open it later with read_image.
    try:
        from app.infrastructure.llm.vision_ocr import describe_image, is_vision_enabled
    except Exception:
        is_vision_enabled = None  # type: ignore[assignment]
        describe_image = None  # type: ignore[assignment]

    if is_vision_enabled and is_vision_enabled():
        try:
            contents = Path(shot_path).read_bytes()
        except Exception:
            contents = b""
        description = describe_image(Path(shot_path).name, contents) if (describe_image and contents) else None
        if description:
            return (
                f"{header}\n  vision sees:\n"
                f"{textwrap.indent(description.strip(), '    ')}"
            )
        return (
            f"{header}\n  (vision is on but returned no description — service "
            f"unreachable or empty. Try read_image('{shot_path}') to retry.)"
        )

    return (
        f"{header}\n  (vision is off: set VISION_ENABLED=1 on the server, or call "
        f"read_image('{shot_path}') once enabled, to get a text description of what rendered.)"
    )


def stop_all_servers() -> int:
    """Kill every tracked background server. Returns the count actually stopped.
    Intended for process/app shutdown, not the per-run Stop button. A handle whose
    kill FAILED stays in the registry — an unkillable process must remain tracked
    (list/stop), never silently leak (the old code
    cleared the registry unconditionally, reporting 'Stopped' for a live process)."""
    with _SERVERS_LOCK:
        handles = list(_LIVE_SERVERS.items())
    killed = 0
    for pid, h in handles:
        was_alive = False
        try:
            was_alive = h.proc.poll() is None
            if was_alive:
                _kill_proc_tree(h.proc)
                try:
                    h.proc.wait(timeout=5)
                except Exception:
                    pass
        except Exception:
            pass
        if h.proc.poll() is None:
            continue   # kill failed → keep it tracked
        if was_alive:
            killed += 1
        if h.kind == "job" and was_alive:
            _mark_job_cancelled(h)
        else:
            with _SERVERS_LOCK:
                _LIVE_SERVERS.pop(pid, None)
    return killed


def _server_verdict(text: str, handle: "_ServerHandle | None", action: str) -> dict[str, Any]:
    """Wrap a run_server result as a server_started VERIFIER verdict when a live server
    with a real URL is known (list/logs of a running server). No live server → a plain
    status result (ok True, no verifier), so it can't confirm a server criterion."""
    if handle is None or not handle.port:
        return {"text": text, "ok": True}
    url = handle.url or f"http://localhost:{handle.port}"
    return {
        "text": text, "ok": True, "verifier": True, "action": action,
        "server_started": True, "actual_port": handle.port, "actual_url": url,
        "evidence": f"dev server running via run_server: {url} (pid={handle.pid})",
    }


def tool_run_server(
    project_root: Path,
    *,
    action: str = "start",
    command: str = "",
    port: int | None = None,
    pid: int | None = None,
    kind: str = "server",
) -> dict[str, Any]:
    """Manage background servers and finite jobs through one process runtime.

    action:
        start  — launch `command` in the background, return immediately (pid + log).
        list   — show every running server this agent started (pid, port, command).
        logs   — tail the captured output of the server with the given `pid`.
        stop   — terminate the server with the given `pid`.
        stop_all — terminate live tracked processes; retain terminal job audit.
    """
    act = (action or "start").strip().lower()
    process_kind = (kind or "server").strip().lower()
    if process_kind not in {"server", "job"}:
        return {"text": "ERROR: kind must be 'server' or 'job'.", "ok": False}
    _reap_dead_servers()

    if act == "list":
        with _SERVERS_LOCK:
            handles = list(_LIVE_SERVERS.values())
        if not handles:
            return {"text": "No background processes are tracked.", "ok": False}
        lines = ["Tracked background processes:"]
        processes: list[dict[str, Any]] = []
        canonical = None
        for h in sorted(handles, key=lambda x: x.started_at):
            age = int(time.time() - h.started_at)
            if h.kind == "job":
                status = h.status
                returncode = h.exit_code
            else:
                returncode = h.proc.poll()
                status = "running" if returncode is None else "failed"
            port_s = f" port={h.port}" if h.port else ""
            exit_s = "" if returncode is None else f" exit={returncode}"
            lines.append(
                f"  pid={h.pid} kind={h.kind} status={status}{exit_s}{port_s} "
                f"age={age}s — {h.command}"
            )
            processes.append(
                {
                    "pid": h.pid,
                    "job_id": h.job_id,
                    "kind": h.kind,
                    "status": status,
                    "exit_code": returncode,
                    "port": h.port,
                    "url": h.url,
                    "command": h.command,
                    "run_id": h.run_id,
                    "log_path": str(h.log_path),
                    "recovered": h.recovered,
                }
            )
            if h.kind == "server" and h.port and returncode is None:
                canonical = h
        result = _server_verdict("\n".join(lines), canonical, "list")
        result["processes"] = processes
        return result

    if act == "stop_all":
        n = stop_all_servers()
        return {
            "text": (
                f"Stopped/cancelled {n} running background process(es); "
                "terminal job records remain available for audit."
            )
        }

    if act == "logs":
        if pid is None:
            return {"text": "ERROR: action 'logs' requires a pid.", "ok": False}
        with _SERVERS_LOCK:
            h = _LIVE_SERVERS.get(int(pid))
        if h is None:
            return {"text": f"ERROR: no tracked server with pid={pid}.", "ok": False}
        tail = _read_log_tail(h.log_path)
        if h.kind == "job":
            status = _refresh_job_handle(h)
            running = status == "running"
            exit_code = h.exit_code
        else:
            running = h.proc.poll() is None
            exit_code = h.proc.returncode
            status = "running" if running else ("completed" if exit_code == 0 else "failed")
        body = tail or "(no output captured yet)"
        # A log tail may reveal the URL a still-running server bound (e.g. Vite's
        # "Local:" line) — adopt it so the verifier reaches the right port.
        if running and not h.url:
            parsed = _parse_server_url(tail)
            if parsed:
                h.url, h.port = parsed
        text = (
            f"{h.kind} pid={pid} [{status}]"
            + ("" if running else f" exit={exit_code}")
            + f"\n$ {h.command}\n\n{body}"
        )
        if h.kind == "server":
            return _server_verdict(text, h if (running and h.port) else None, "logs")
        return {
            "text": text,
            "ok": status in {"running", "completed"},
            "action": "logs",
            "kind": "job",
            "pid": h.pid,
            "job_id": h.job_id,
            "status": status,
            "exit_code": exit_code,
            "log_path": str(h.log_path),
            "recovered": h.recovered,
        }

    if act == "stop":
        if pid is None:
            return {"text": "ERROR: action 'stop' requires a pid.", "ok": False}
        with _SERVERS_LOCK:
            h = _LIVE_SERVERS.get(int(pid))
        if h is None:
            return {"text": f"ERROR: no tracked server with pid={pid}.", "ok": False}
        # The handle leaves the registry only AFTER the process is confirmed dead —
        # otherwise a failed taskkill reported "Stopped" while the process lived on,
        # untracked and unstoppable (John's P1a review; same rule as stop_run_servers).
        if h.kind == "job" and _refresh_job_handle(h) != "running":
            return {
                "text": f"job pid={pid} already {h.status}; no running process to stop.",
                "ok": h.status in {"completed", "cancelled"},
                "action": "stop",
                "kind": "job",
                "pid": h.pid,
                "job_id": h.job_id,
                "status": h.status,
                "exit_code": h.exit_code,
                "log_path": str(h.log_path),
                "recovered": h.recovered,
            }
        was_alive = h.proc.poll() is None
        try:
            if was_alive:
                _kill_proc_tree(h.proc)
                try:
                    h.proc.wait(timeout=5)
                except Exception:
                    pass
        except Exception as exc:
            if h.proc.poll() is None:
                return {"text": f"ERROR stopping pid={pid}: {exc} — процесс ЖИВ и остаётся "
                                f"в списке (run_server list).", "ok": False}
        if h.proc.poll() is None:
            return {"text": f"ERROR: не удалось остановить pid={pid} — процесс ЖИВ и "
                            f"остаётся в списке (run_server list). Попробуй ещё раз или "
                            f"останови вручную.", "ok": False}
        if h.kind == "job":
            _refresh_job_handle(h)
            if was_alive and h.exit_code is None:
                _mark_job_cancelled(h)
            return {
                "text": f"Stopped job pid={pid} [{h.status}] — {h.command}",
                "ok": True,
                "action": "stop",
                "kind": "job",
                "pid": h.pid,
                "job_id": h.job_id,
                "status": h.status,
                "exit_code": h.exit_code,
                "log_path": str(h.log_path),
                "recovered": h.recovered,
            }
        with _SERVERS_LOCK:
            _LIVE_SERVERS.pop(int(pid), None)
        return {"text": f"Stopped {h.kind} pid={pid} — {h.command}"}

    if act != "start":
        return {
            "text": f"ERROR: unknown action '{action}'. Use start|list|logs|stop|stop_all.",
            "ok": False,
        }

    cleaned_command = (command or "").strip()
    if not cleaned_command:
        return {"text": "ERROR: action 'start' requires a command.", "ok": False}
    display_command = redact_text(cleaned_command)
    log_dir = (project_root.resolve() / _SERVER_LOG_DIRNAME)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"text": f"ERROR: cannot create server log dir: {exc}", "ok": False}
    log_prefix = "job" if process_kind == "job" else "server"
    log_path = log_dir / f"{log_prefix}-{int(time.time() * 1000)}.log"
    started_at = time.time()
    run_id = _CURRENT_RUN_ID.get()

    # R2 attribution: if the REQUESTED port is already taken by another process
    # BEFORE our start, our child cannot be the one bound there — the requested-port
    # fallback below must not adopt it, or liveness/verification would bless a
    # FOREIGN app on that port (review #2/#7/#19).
    _pre_bound = _port_listening(int(port)) if (process_kind == "server" and port) else False

    prepared_job: dict[str, Any] | None = None
    try:
        if process_kind == "job":
            prepared_job = prepare_job(
                cleaned_command,
                project_root,
                log_path=log_path,
                run_id=run_id,
                started_at=started_at,
            )
        # Binary: the server child writes its raw bytes straight to this fd; we
        # decode on read (_read_log_tail) so Windows OEM output isn't mangled.
        log_fh = open(log_path, "wb")
    except Exception as exc:
        if prepared_job:
            discard_prepared_job(prepared_job)
        return {"text": f"ERROR: cannot open log file: {exc}", "ok": False}

    spawn_kwargs = (
        _job_process_group_kwargs()
        if prepared_job
        else _new_process_group_kwargs()
    )
    try:
        proc = subprocess.Popen(
            prepared_job["argv"] if prepared_job else cleaned_command,
            shell=prepared_job is None,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(project_root.resolve()),
            # Strip secret-bearing env keys from the model's server child (FIX-1).
            env=_agent_child_env(),
            # Background servers never read stdin; close it so they don't block.
            stdin=subprocess.DEVNULL,
            # Own process group so `stop` kills the whole server tree, not just
            # the cmd.exe wrapper (which would orphan the real server process).
            **spawn_kwargs,
        )
    except OSError as exc:
        if not (
            prepared_job
            and os.name == "nt"
            and getattr(exc, "winerror", None) in {5, 87}
        ):
            if prepared_job:
                discard_prepared_job(prepared_job)
            try:
                log_fh.close()
            except Exception:
                pass
            return {"text": f"ERROR: {exc}", "ok": False}
        try:
            proc = subprocess.Popen(
                prepared_job["argv"],
                shell=False,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(project_root.resolve()),
                env=_agent_child_env(),
                stdin=subprocess.DEVNULL,
                **_job_process_group_kwargs(allow_breakaway=False),
            )
        except Exception as retry_exc:
            discard_prepared_job(prepared_job)
            try:
                log_fh.close()
            except Exception:
                pass
            return {"text": f"ERROR: {retry_exc}", "ok": False}
    except Exception as exc:
        if prepared_job:
            discard_prepared_job(prepared_job)
        try:
            log_fh.close()
        except Exception:
            pass
        return {"text": f"ERROR: {exc}", "ok": False}
    # Popen inherited/duplicated the descriptor it needs. Keeping the parent's
    # Python file object open locks the log on Windows even after a finite job
    # exits, so release the parent handle immediately.
    try:
        log_fh.close()
    except Exception:
        pass

    # R2: tag ownership — the executor's worker thread binds the run_id ContextVar,
    # so the runtime later knows which servers THIS run launched (report/stop them).
    job_record: dict[str, Any] | None = None
    if prepared_job:
        try:
            job_record = activate_job(
                prepared_job,
                pid=proc.pid,
            )
        except Exception as exc:
            try:
                _kill_proc_tree(proc)
                proc.wait(timeout=5)
            except Exception:
                pass
            discard_prepared_job(prepared_job)
            return {"text": f"ERROR: cannot persist background job: {exc}", "ok": False}
    handle = _ServerHandle(
        proc.pid,
        display_command,
        proc,
        log_path,
        port if process_kind == "server" else None,
        run_id=run_id,
        kind=process_kind,
        job_record=job_record,
        started_at=started_at,
    )
    with _SERVERS_LOCK:
        _LIVE_SERVERS[proc.pid] = handle

    # Give it a moment to either bind its port or crash, so we can report
    # something useful instead of a bare "started" for a command that died.
    time.sleep(_SERVER_STARTUP_GRACE if process_kind == "server" else 0.05)
    if process_kind == "job":
        _refresh_job_handle(handle)
    if proc.poll() is not None or (process_kind == "job" and handle.status != "running"):
        if process_kind == "server":
            with _SERVERS_LOCK:
                _LIVE_SERVERS.pop(proc.pid, None)
        try:
            log_fh.close()
        except Exception:
            pass
        tail = _read_log_tail(log_path)
        body = f"\n{tail}" if tail else ""
        if process_kind == "job":
            status = handle.status
            exit_code = handle.exit_code
            return {
                "text": (
                    f"job pid={proc.pid} [{status}] exit={exit_code}\n"
                    f"$ {display_command}{body}"
                ),
                "ok": status == "completed",
                "action": "start",
                "kind": "job",
                "pid": proc.pid,
                "job_id": handle.job_id,
                "status": status,
                "exit_code": exit_code,
                "log_path": str(log_path),
                "recovered": handle.recovered,
            }
        # A server start that died (incl. "Port … is already in use") is NOT ok — otherwise
        # the loop reads a missing `ok` as True and a failed start looks like success,
        # and a server_started criterion would falsely confirm.
        return {"text": (
            f"ERROR: server exited immediately (code={proc.returncode}).\n"
            f"$ {display_command}{body}"
        ), "ok": False}

    # Learn the URL the server ACTUALLY bound (Vite may have auto-incremented off a
    # taken port). This becomes the canonical URL, so the verifier reaches THIS
    # server — not a guessed port or a
    # different app already on the requested one. Only wait when a web URL is expected
    # (a port was requested or the command is a known dev server) so a plain
    # background process doesn't pay the poll.
    parsed = None
    if process_kind == "server" and _expects_web_url(cleaned_command, port):
        parsed = _await_server_url(handle.log_path, time.monotonic() + _SERVER_URL_WAIT)
    if parsed:
        actual_url, actual_port = parsed
        handle.url, handle.port = actual_url, actual_port
    elif port and not _pre_bound:
        handle.url = f"http://localhost:{port}"
    elif port and _pre_bound:
        # The requested port was listening BEFORE this start — whatever answers there
        # is NOT our child. No URL adopted: verification must not target a foreign app.
        handle.port = None
    actual_url = handle.url
    actual_port = handle.port

    if process_kind == "job":
        return {
            "text": (
                "Job started in background.\n"
                f"  pid={proc.pid}\n"
                f"  log={log_path}\n"
                f"  $ {display_command}\n"
                f"Use run_server(action='logs', pid={proc.pid}) for status/output, "
                f"run_server(action='stop', pid={proc.pid}) to stop it."
            ),
            "ok": True,
            "action": "start",
            "kind": "job",
            "pid": proc.pid,
            "job_id": handle.job_id,
            "status": "running",
            "log_path": str(log_path),
            "recovered": handle.recovered,
        }

    if actual_url:
        url_line = (
            f"  URL: {actual_url}  ← verify against THIS url (browser/http_api). "
            f"Do NOT guess other ports.\n"
        )
        if port and actual_port and int(actual_port) != int(port):
            url_line += (
                f"  (note: requested port {port} was taken — the server bound "
                f"{actual_port} instead)\n"
            )
    elif port and _pre_bound:
        url_line = (
            f"  ⚠ порт {port} был занят ЧУЖИМ процессом ещё до старта — URL не принят. "
            f"Проверь run_server(action='logs', pid={proc.pid}): сервер мог упасть или "
            f"подняться на другом порту.\n"
        )
    else:
        url_line = ""
    text = (
        f"Server started in background.\n"
        f"{url_line}"
        f"  pid={proc.pid}\n"
        f"  $ {display_command}\n"
        f"Use run_server(action='logs', pid={proc.pid}) to read output, "
        f"run_server(action='stop', pid={proc.pid}) to stop it. "
        f"It keeps running until run_server(action='stop') or explicit Workflow Stop."
    )

    # Auto GUI verification: capture what just launched and feed it back so the
    # model can "see" its build instead of flying blind through UI bring-up.
    try:
        gui_block = _auto_verify_gui(handle)
    except Exception as exc:  # never let instrumentation break a healthy start
        gui_block = f"🖼 GUI verification skipped (internal error: {exc})."
    if gui_block:
        text = f"{text}\n\n{gui_block}"

    # Structured evidence: the process survived the crash-grace → a real server
    # started. verifier=True + actual_url makes this a server_started verdict the
    # CriteriaTracker can confirm (a failed start above returned ok=False, no verifier).
    return {
        "text": text,
        "ok": True,
        "verifier": True,
        "action": "start",
        "server_started": True,
        "pid": proc.pid,
        "port": actual_port,
        "actual_port": actual_port,
        "actual_url": actual_url,
        "local_url": actual_url,
        "evidence": (
            f"dev server started via run_server: {actual_url or '(no url)'} "
            f"(pid={proc.pid}, port={actual_port or '?'})"
        ),
    }
