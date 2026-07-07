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

from app.application.code_agent.tools._sandbox import _truncate_middle
from app.application.code_agent.tools._shell import (
    _CURRENT_RUN_ID,
    _KILLED_RUN_IDS,
    _LIVE_SHELL_LOCK,
    _LIVE_SHELL_PROCS,
    _SHELL_STDERR_LIMIT,
    _SHELL_STDOUT_LIMIT,
    _SHELL_TIMEOUT_MAX,
    _blocked_shell_fragment,
    _kill_proc_tree,
    _new_process_group_kwargs,
    _register_shell_proc,
    _unregister_shell_proc,
    raw_ssh_redirect,
)


# Canonical subprocess-output decoder (Windows console mojibake fix) lives in one
# place now; kept as _decode_console here for the existing call sites and tests.
from app.infrastructure.encoding import decode_console as _decode_console


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


# Secret-bearing env keys that agent-spawned child processes must NOT inherit.
# The backend loads tokens into its own environment (GitHub PAT + HF for the MCP
# servers, the Elira API token, the llama-server key); without this filter every
# run_bash / run_server child the model launches would inherit them via the default
# `env=None` (full os.environ) and could exfiltrate them. We strip by explicit name
# plus a conservative secret pattern, but keep PATH and the rest of the environment
# so normal toolchain commands still work (a strict allow-list would break language
# toolchains on the user's own machine).
_SECRET_ENV_EXPLICIT = frozenset({
    "GITHUB_PERSONAL_ACCESS_TOKEN", "HUGGINGFACE_TOKEN", "HF_TOKEN",
    "ELIRA_API_TOKEN", "VITE_ELIRA_API_TOKEN",
    "LLAMA_SERVER_API_KEY", "LOCAL_EMBED_API_KEY",
})
_SECRET_ENV_RE = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_KEY|_API_KEY|APIKEY)",
    re.IGNORECASE,
)


def _agent_child_env() -> dict[str, str]:
    """Parent environment minus secret-bearing keys, for agent-spawned children."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _SECRET_ENV_EXPLICIT and not _SECRET_ENV_RE.search(key)
    }
_INLINE_SCRIPT_FLAGS = frozenset({"-c", "-e", "--eval"})


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
    cleaned_command = (command or "").strip()
    if not cleaned_command:
        return {"text": "ERROR: command is empty"}
    blocked = _blocked_shell_fragment(cleaned_command)
    if blocked:
        return {"text": f"ERROR: blocked dangerous shell command fragment: {blocked}"}
    # Redirect (not ban) raw `ssh host "…"` to the ssh_* tools — the quoting-hell
    # trap that burned a whole run. ok=False so it reads as no-progress and the
    # progress controller / loop-guard see a stuck strategy if the model ignores it.
    redirect = raw_ssh_redirect(cleaned_command)
    if redirect is not None:
        return {"text": redirect, "ok": False}
    safe_timeout = max(1, min(int(timeout), _SHELL_TIMEOUT_MAX))

    run_id = _CURRENT_RUN_ID.get()
    # Multi-line inline scripts (python -c "<…>", node -e …) are mangled by
    # cmd.exe /c, so run them via argv with no shell; everything else keeps the
    # shell path. The wait/kill/capture machinery below is identical either way.
    _argv = _inline_script_argv(cleaned_command)
    try:
        # Popen (not subprocess.run) so the live process is registered and can
        # be killed mid-flight by the Stop button. We drive the wait ourselves
        # via communicate() with a deadline, killing on timeout OR cancel.
        proc = subprocess.Popen(
            _argv if _argv is not None else cleaned_command,
            shell=_argv is None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Capture BYTES (not text=True): Windows console apps emit the OEM
            # codepage, and text=True would decode with the wrong ANSI default
            # (mojibake). Decoded via _decode_console below.
            cwd=str(project_root.resolve()),
            # Strip secret-bearing env keys so the model's shell child can't read
            # Elira's GitHub/HF/API tokens (FIX-1).
            env=_agent_child_env(),
            # Close stdin: a shell tool must never block on input. Interactive
            # prompts (ssh host-key/password, apt, etc.) get EOF and fail fast
            # instead of hanging until the timeout. For real SSH use the ssh tool.
            stdin=subprocess.DEVNULL,
            # Own process group so Stop/timeout can kill the whole tree, not just
            # the cmd.exe wrapper (which would orphan the real child on Windows).
            **_new_process_group_kwargs(),
        )
    except Exception as exc:
        return {"text": f"ERROR: {exc}"}

    _register_shell_proc(run_id, proc)
    cancelled = False
    timed_out = False
    # Drain stdout/stderr in background threads so a chatty command can't fill
    # the OS pipe buffer and deadlock (child blocks on write → never exits →
    # poll() never completes). The main loop then only watches poll()/deadline,
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
        deadline = time.monotonic() + safe_timeout
        # Poll so a Stop press (which proc.kill()s us from another thread) is
        # observed within ~0.1s instead of waiting out the whole timeout.
        while True:
            if proc.poll() is not None:
                break
            if time.monotonic() >= deadline:
                _kill_proc_tree(proc)
                timed_out = True
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
        # Stop from a normal failure). A timeout also kills the proc, but that
        # is a tool error, not a Stop. Fall back to a negative code for the
        # POSIX direct-kill case where no run_id was bound.
        if not timed_out:
            if run_id:
                with _LIVE_SHELL_LOCK:
                    if run_id in _KILLED_RUN_IDS:
                        _KILLED_RUN_IDS.discard(run_id)
                        cancelled = True
            if not cancelled and proc.returncode is not None and proc.returncode < 0:
                cancelled = True

    out, err = _decode_console(b"".join(out_buf)), _decode_console(b"".join(err_buf))

    if timed_out:
        tail = f"\n{_truncate_middle(err.rstrip(), _SHELL_STDERR_LIMIT)}" if err else ""
        return {"text": f"ERROR: command timed out after {safe_timeout}s{tail}"}

    if cancelled:
        return {"text": f"$ {cleaned_command}\nПрервано пользователем (Стоп)."}

    stdout, stderr = out or "", err or ""
    parts = [f"$ {cleaned_command}", f"exit={proc.returncode}"]
    if stdout:
        parts.append(f"STDOUT:\n{_truncate_middle(stdout.rstrip(), _SHELL_STDOUT_LIMIT)}")
    if stderr:
        parts.append(f"STDERR:\n{_truncate_middle(stderr.rstrip(), _SHELL_STDERR_LIMIT)}")
    # exit_code travels in the meta so the UI can colour the call by SEMANTIC
    # success (a non-zero exit reads as failure) instead of "the process ran".
    # We keep the top-level `ok` unset (a non-zero exit isn't always a failure —
    # grep/findstr return 1 for "no match") so the executor's approval/verify
    # logic is unchanged; the UI decides how to render exit_code itself.
    return {"text": "\n".join(parts), "exit_code": proc.returncode}


# ─── run_server: background process launcher ────────────────────────────────
# Unlike run_bash (which blocks until the command exits or times out), run_server
# starts a long-lived process via Popen and returns IMMEDIATELY. The process is
# tracked in a module-level registry keyed by pid so it can be listed and stopped
# explicitly later. These servers deliberately OUTLIVE the agent run that started
# them, so they are NOT registered in _LIVE_SHELL_PROCS and are NOT killed by the
# Stop button — only by `stop`/`stop_all`. Output is captured to log files under
# the project's .elira/servers/ so the model can inspect startup without blocking.

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
    __slots__ = ("pid", "command", "proc", "log_path", "port", "url", "started_at")

    def __init__(self, pid: int, command: str, proc: subprocess.Popen,
                 log_path: Path, port: int | None, url: str | None = None) -> None:
        self.pid = pid
        self.command = command
        self.proc = proc
        self.log_path = log_path
        self.port = port
        self.url = url
        self.started_at = time.time()


_LIVE_SERVERS: dict[int, _ServerHandle] = {}
_SERVERS_LOCK = threading.Lock()


def _reap_dead_servers() -> None:
    """Drop handles whose process has exited so `list` stays honest."""
    with _SERVERS_LOCK:
        dead = [pid for pid, h in _LIVE_SERVERS.items() if h.proc.poll() is not None]
        for pid in dead:
            _LIVE_SERVERS.pop(pid, None)


def active_server_ports() -> set[int]:
    """Loopback ports of dev servers THIS agent started and are still alive. The
    SSRF guard uses this to let http_api/browser verify the agent's OWN dev server
    on localhost — and nothing else (arbitrary internal infra stays blocked)."""
    _reap_dead_servers()
    with _SERVERS_LOCK:
        return {int(h.port) for h in _LIVE_SERVERS.values() if h.port}


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
    """Kill every tracked background server. Returns the count signalled.
    Intended for process/app shutdown, not the per-run Stop button."""
    with _SERVERS_LOCK:
        handles = list(_LIVE_SERVERS.values())
    killed = 0
    for h in handles:
        try:
            if h.proc.poll() is None:
                _kill_proc_tree(h.proc)
                killed += 1
        except Exception:
            pass
    with _SERVERS_LOCK:
        _LIVE_SERVERS.clear()
    return killed


def tool_run_server(
    project_root: Path,
    *,
    action: str = "start",
    command: str = "",
    port: int | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Manage long-lived background processes (dev servers, watchers).

    action:
        start  — launch `command` in the background, return immediately (pid + log).
        list   — show every running server this agent started (pid, port, command).
        logs   — tail the captured output of the server with the given `pid`.
        stop   — terminate the server with the given `pid`.
        stop_all — terminate every tracked server.
    """
    act = (action or "start").strip().lower()
    _reap_dead_servers()

    if act == "list":
        with _SERVERS_LOCK:
            handles = list(_LIVE_SERVERS.values())
        if not handles:
            return {"text": "No background servers are running."}
        lines = ["Running background servers:"]
        for h in sorted(handles, key=lambda x: x.started_at):
            age = int(time.time() - h.started_at)
            port_s = f" port={h.port}" if h.port else ""
            lines.append(f"  pid={h.pid}{port_s} age={age}s — {h.command}")
        return {"text": "\n".join(lines)}

    if act == "stop_all":
        n = stop_all_servers()
        return {"text": f"Stopped {n} background server(s)."}

    if act == "logs":
        if pid is None:
            return {"text": "ERROR: action 'logs' requires a pid."}
        with _SERVERS_LOCK:
            h = _LIVE_SERVERS.get(int(pid))
        if h is None:
            return {"text": f"ERROR: no tracked server with pid={pid}."}
        tail = _read_log_tail(h.log_path)
        status = "running" if h.proc.poll() is None else f"exited (code={h.proc.returncode})"
        body = tail or "(no output captured yet)"
        return {"text": f"server pid={pid} [{status}]\n$ {h.command}\n\n{body}"}

    if act == "stop":
        if pid is None:
            return {"text": "ERROR: action 'stop' requires a pid."}
        with _SERVERS_LOCK:
            h = _LIVE_SERVERS.pop(int(pid), None)
        if h is None:
            return {"text": f"ERROR: no tracked server with pid={pid}."}
        try:
            if h.proc.poll() is None:
                _kill_proc_tree(h.proc)
                try:
                    h.proc.wait(timeout=5)
                except Exception:
                    pass
            return {"text": f"Stopped server pid={pid} — {h.command}"}
        except Exception as exc:
            return {"text": f"ERROR stopping pid={pid}: {exc}"}

    if act != "start":
        return {"text": f"ERROR: unknown action '{action}'. Use start|list|logs|stop|stop_all."}

    cleaned_command = (command or "").strip()
    if not cleaned_command:
        return {"text": "ERROR: action 'start' requires a command."}
    blocked = _blocked_shell_fragment(cleaned_command)
    if blocked:
        return {"text": f"ERROR: blocked dangerous shell command fragment: {blocked}"}

    log_dir = (project_root.resolve() / _SERVER_LOG_DIRNAME)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"text": f"ERROR: cannot create server log dir: {exc}"}
    log_path = log_dir / f"server-{int(time.time() * 1000)}.log"

    try:
        # Binary: the server child writes its raw bytes straight to this fd; we
        # decode on read (_read_log_tail) so Windows OEM output isn't mangled.
        log_fh = open(log_path, "wb")
    except Exception as exc:
        return {"text": f"ERROR: cannot open log file: {exc}"}

    try:
        proc = subprocess.Popen(
            cleaned_command,
            shell=True,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(project_root.resolve()),
            # Strip secret-bearing env keys from the model's server child (FIX-1).
            env=_agent_child_env(),
            # Background servers never read stdin; close it so they don't block.
            stdin=subprocess.DEVNULL,
            # Own process group so `stop` kills the whole server tree, not just
            # the cmd.exe wrapper (which would orphan the real server process).
            **_new_process_group_kwargs(),
        )
    except Exception as exc:
        try:
            log_fh.close()
        except Exception:
            pass
        return {"text": f"ERROR: {exc}"}

    handle = _ServerHandle(proc.pid, cleaned_command, proc, log_path, port)
    with _SERVERS_LOCK:
        _LIVE_SERVERS[proc.pid] = handle

    # Give it a moment to either bind its port or crash, so we can report
    # something useful instead of a bare "started" for a command that died.
    time.sleep(_SERVER_STARTUP_GRACE)
    if proc.poll() is not None:
        with _SERVERS_LOCK:
            _LIVE_SERVERS.pop(proc.pid, None)
        try:
            log_fh.close()
        except Exception:
            pass
        tail = _read_log_tail(log_path)
        body = f"\n{tail}" if tail else ""
        return {"text": (
            f"ERROR: server exited immediately (code={proc.returncode}).\n"
            f"$ {cleaned_command}{body}"
        )}

    # Learn the URL the server ACTUALLY bound (Vite may have auto-incremented off a
    # taken port). This becomes the canonical URL: the loopback allowlist keys on the
    # REAL port, so the verifier reaches THIS server — not a guessed port or a
    # different app already on the requested one. Only wait when a web URL is expected
    # (a port was requested or the command is a known dev server) so a plain
    # background process doesn't pay the poll.
    parsed = None
    if _expects_web_url(cleaned_command, port):
        parsed = _await_server_url(handle.log_path, time.monotonic() + _SERVER_URL_WAIT)
    if parsed:
        actual_url, actual_port = parsed
        handle.url, handle.port = actual_url, actual_port
    elif port:
        handle.url = f"http://localhost:{port}"
    actual_url = handle.url
    actual_port = handle.port

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
    else:
        url_line = ""
    text = (
        f"Server started in background.\n"
        f"{url_line}"
        f"  pid={proc.pid}\n"
        f"  $ {cleaned_command}\n"
        f"Use run_server(action='logs', pid={proc.pid}) to read output, "
        f"run_server(action='stop', pid={proc.pid}) to stop it. "
        f"It keeps running across turns and is NOT killed by Stop."
    )

    # Auto GUI verification: capture what just launched and feed it back so the
    # model can "see" its build instead of flying blind through UI bring-up.
    try:
        gui_block = _auto_verify_gui(handle)
    except Exception as exc:  # never let instrumentation break a healthy start
        gui_block = f"🖼 GUI verification skipped (internal error: {exc})."
    if gui_block:
        text = f"{text}\n\n{gui_block}"

    # Structured evidence: the loop/UI and the progress router get the actual URL +
    # a first-start signal without re-parsing the text.
    return {
        "text": text,
        "ok": True,
        "action": "start",
        "server_started": True,
        "pid": proc.pid,
        "port": actual_port,
        "actual_port": actual_port,
        "actual_url": actual_url,
        "local_url": actual_url,
    }
