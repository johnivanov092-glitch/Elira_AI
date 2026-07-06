"""SSH provider — agent tools for running commands and r/w files on
remote machines via the local `ssh` binary.

Why not paramiko/fabric: the user already has working SSH (keys,
~/.ssh/config, known_hosts, sometimes ProxyJump). Reusing the OS
ssh binary inherits every bit of that setup automatically and
costs zero new dependencies. paramiko would re-implement half of
ssh in Python and would NOT pick up the user's config without
extra glue.

Security model:
  * Provider is DISABLED unless the user has explicitly added at
    least one host to `data/ssh_acl.json` (managed via API or by
    hand). See ssh_acl.py.
  * Every tool call validates `host` against the allowlist BEFORE
    invoking ssh. Even if the agent hallucinates a hostname, the
    provider returns an error meta instead of executing.
  * `ssh -o BatchMode=yes` — never prompt for a password. The
    user must have key-based auth set up for the host.
  * `ssh -o StrictHostKeyChecking=accept-new` — first-time hosts
    get auto-recorded in known_hosts; existing host-key changes
    are rejected (no silent MITM).
  * No `-t` (TTY allocation) — purely batch mode.
"""
from __future__ import annotations

import base64
import logging
import re
import subprocess
from typing import Any

from app.application.tool_providers.ssh_acl import (
    get_allowed_hosts,
    is_host_allowed,
    is_ssh_enabled,
)
from app.infrastructure.encoding import decode_console
from app.infrastructure.text import truncate_middle


logger = logging.getLogger(__name__)


# Reasonable upper bounds so a hallucinating LLM can't try to
# `ssh_read` a 10GB log file or write a runaway content blob.
_MAX_READ_BYTES = 100_000
_MAX_WRITE_BYTES = 100_000
# Tool output back to the LLM is also capped (separate from the
# read cap, which limits what we fetch over the wire).
_LLM_OUTPUT_LIMIT = 16_000


def _truncate_for_llm(text: str, limit: int = _LLM_OUTPUT_LIMIT) -> str:
    # Was a head-only cut that dropped the exit code / last error at the bottom of
    # a remote command's output — now the shared head+tail truncation keeps both.
    return truncate_middle(text, limit)


def _ssh_args(host: str) -> list[str]:
    """Shared ssh flags every tool uses. Order matters here — flags
    before the destination."""
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        host,
    ]


def _validate_host(host: str) -> str | None:
    """Return None if host is allowed, else an error message."""
    if not isinstance(host, str) or not host.strip():
        return "host is empty"
    h = host.strip()
    # Block obvious metacharacters that could confuse argv parsing
    # elsewhere or be a sign the agent is trying something weird.
    if any(c in h for c in (" ", "\t", "\n", "\r", ";", "|", "&", "$", "`", "<", ">")):
        return f"host contains invalid characters: {host!r}"
    if not is_host_allowed(h):
        return (
            f"host '{h}' is not in the SSH allowlist. "
            f"Add it via Settings → SSH or the /api/code-agent/ssh/config endpoint."
        )
    return None


# ── Tool implementations ────────────────────────────────────────


def tool_ssh_run(*, host: str, command: str, timeout: int = 60) -> dict[str, Any]:
    """Run a shell command on a remote host via SSH. Returns stdout +
    stderr + exit_code, just like the local run_bash tool but the
    other side."""
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}", "ok": False}
    if not isinstance(command, str) or not command.strip():
        return {"text": "ERROR: command is empty", "ok": False}

    safe_timeout = max(5, min(int(timeout) if timeout else 60, 600))
    try:
        proc = subprocess.run(
            [*_ssh_args(host), command],
            capture_output=True,  # bytes → decode_console (remote may emit non-ANSI)
            timeout=safe_timeout,
        )
    except subprocess.TimeoutExpired:
        return {"text": f"ERROR: ssh {host} command timed out after {safe_timeout}s", "ok": False}
    except FileNotFoundError:
        return {"text": "ERROR: `ssh` binary not found on this machine", "ok": False}
    except Exception as exc:
        logger.exception("ssh_run failed for host=%s", host)
        return {"text": f"ERROR: {exc}", "ok": False}

    _out, _err = decode_console(proc.stdout), decode_console(proc.stderr)
    parts = [f"$ ssh {host} -- {command}", f"exit={proc.returncode}"]
    if _out:
        parts.append(f"STDOUT:\n{_truncate_for_llm(_out.rstrip())}")
    if _err:
        parts.append(f"STDERR:\n{_truncate_for_llm(_err.rstrip())}")
    # exit_code + semantic ok so a non-zero remote command reads as a FAILURE
    # (red dot / no false grounding fact), not "it ran".
    return {
        "text": "\n".join(parts),
        "touched_host": host,
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
    }


def _looks_like_windows_no_cmd(stderr: Any) -> bool:
    """True when the remote cmd.exe rejected a POSIX tool (`head`) as an unknown
    command — the tell that the host is Windows and we must read via PowerShell.
    Matches the English and Russian cmd.exe 'not recognized' messages."""
    text = decode_console(stderr) if isinstance(stderr, (bytes, bytearray)) else str(stderr or "")
    low = text.lower()
    return "is not recognized" in low or "не является внутренн" in low


def _windows_read_encoded(path: str, limit: int) -> str:
    """`powershell -EncodedCommand …` that reads `path` as bytes, bounded to
    `limit`, and writes them raw to stdout. base64 (UTF-16LE) so cmd.exe on the
    remote never mangles quotes/pipes — the same trap a raw PowerShell-over-ssh
    command hits. This is what makes ssh_read work on Windows hosts."""
    lit = path.replace("'", "''")  # PowerShell single-quoted literal
    ps = (
        "$ErrorActionPreference='Stop';"
        f"$b=[System.IO.File]::ReadAllBytes('{lit}');"
        f"$n=[Math]::Min($b.Length,{int(limit)});"
        "$o=[Console]::OpenStandardOutput();$o.Write($b,0,$n);$o.Flush()"
    )
    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"


# Shared read/write cores — used by ssh_read/ssh_write AND the higher-level
# primitives (ssh_replace/ssh_assert_*), so the Windows byte-read fallback and the
# stdin-write (no body escaping) live in ONE place, not copied per tool.


def _read_remote_bytes(host: str, path: str, limit: int) -> tuple[bytes | None, str | None]:
    """Fetch up to `limit` bytes of a remote file. Returns (bytes, None) or
    (None, error). Auto-falls back to a base64 PowerShell byte-read on Windows
    remotes (cmd.exe has no `head`)."""
    remote_cmd = f"head -c {limit} -- {_shell_quote(path)}"
    try:
        proc = subprocess.run([*_ssh_args(host), remote_cmd], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return None, f"ssh {host} read timed out"
    except FileNotFoundError:
        return None, "`ssh` binary not found on this machine"
    if proc.returncode != 0 and _looks_like_windows_no_cmd(proc.stderr):
        try:
            proc = subprocess.run(
                [*_ssh_args(host), _windows_read_encoded(path, limit)],
                capture_output=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return None, f"ssh {host} read timed out"
    if proc.returncode != 0:
        return None, f"remote read failed (exit {proc.returncode}): {decode_console(proc.stderr).rstrip()}"
    return proc.stdout or b"", None


# A drive-letter path (`C:\…`, `C:/…`) or a UNC path (`\\host\share`) can only be
# a Windows remote — so we write it via PowerShell WITHOUT ever probing with a
# POSIX `cat >`, which on cmd.exe would truncate the target to empty before the
# fallback even runs.
_WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _is_windows_path(path: str) -> bool:
    return bool(_WINDOWS_PATH_RE.match((path or "").strip()))


def _windows_write_encoded(path: str, *, append: bool) -> str:
    """`powershell -EncodedCommand …` that copies raw STDIN bytes into `path`. The
    body travels via stdin (no command-line length limit, no quote mangling); only
    the tiny script is base64'd. Overwrite is ATOMIC and non-destructive: it writes
    a temp file and only then Move-replaces the target, so a failed/partial write
    leaves the original untouched (never zeroed). Append opens-or-creates and seeks
    to end (inherently additive)."""
    lit = path.replace("'", "''")  # PowerShell single-quoted literal
    if append:
        ps = (
            "$ErrorActionPreference='Stop';"
            f"$fs=[System.IO.File]::Open('{lit}',[System.IO.FileMode]::Append,[System.IO.FileAccess]::Write);"
            "$in=[Console]::OpenStandardInput();$in.CopyTo($fs);$fs.Close()"
        )
    else:
        ps = (
            "$ErrorActionPreference='Stop';"
            f"$t='{lit}.elira-tmp';"
            "$fs=[System.IO.File]::Create($t);"
            "$in=[Console]::OpenStandardInput();$in.CopyTo($fs);$fs.Close();"
            f"Move-Item -LiteralPath $t -Destination '{lit}' -Force"
        )
    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"


def _write_remote_bytes(host: str, path: str, data: bytes, *, append: bool = False) -> str | None:
    """Write raw bytes to a remote file via stdin — the body never touches shell
    parsing, only the path is quoted. A Windows-looking path goes STRAIGHT to the
    PowerShell writer (never a destructive `cat >` probe); a POSIX path uses
    `cat > path`, with a PowerShell fallback ONLY when the remote turns out to have
    no `cat` (and `cat` on a Windows-invalid POSIX path errors before truncating a
    real target). Returns None on success or an error string."""
    if _is_windows_path(path):
        try:
            proc = subprocess.run(
                [*_ssh_args(host), _windows_write_encoded(path, append=append)],
                input=data, capture_output=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return f"ssh {host} write timed out"
        except FileNotFoundError:
            return "`ssh` binary not found on this machine"
        if proc.returncode != 0:
            return f"remote write failed (exit {proc.returncode}): {decode_console(proc.stderr).rstrip()}"
        return None

    op = ">>" if append else ">"
    remote_cmd = f"cat {op} {_shell_quote(path)}"
    try:
        proc = subprocess.run(
            [*_ssh_args(host), remote_cmd], input=data, capture_output=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return f"ssh {host} write timed out"
    except FileNotFoundError:
        return "`ssh` binary not found on this machine"
    if proc.returncode != 0 and _looks_like_windows_no_cmd(proc.stderr):
        try:
            proc = subprocess.run(
                [*_ssh_args(host), _windows_write_encoded(path, append=append)],
                input=data, capture_output=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return f"ssh {host} write timed out"
    if proc.returncode != 0:
        return f"remote write failed (exit {proc.returncode}): {decode_console(proc.stderr).rstrip()}"
    return None


def tool_ssh_read(*, host: str, path: str, max_chars: int | None = None) -> dict[str, Any]:
    """Read a remote file by SSHing in and `cat`ing it. Capped at
    100KB by default — pipe a larger file through head/tail/grep on
    the remote side instead of pulling it all back."""
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}"}
    if not isinstance(path, str) or not path.strip():
        return {"text": "ERROR: path is empty"}

    cap = max(100, min(int(max_chars) if max_chars else _MAX_READ_BYTES, _MAX_READ_BYTES))
    raw, rerr = _read_remote_bytes(host, path, cap + 1)  # +1 to detect truncation
    if rerr is not None:
        return {"text": f"ERROR: {rerr}"}
    truncated = len(raw) > cap
    body = decode_console(raw[:cap] if truncated else raw)
    head = f"[ssh:{host}:{path}]"
    if truncated:
        head += f"  (truncated at {cap} bytes — file is longer)"
    return {
        "text": f"{head}\n\n{body}",
        "touched_host": host,
        "touched_path": path,
    }


def tool_ssh_write(*, host: str, path: str, content: str, append: bool = False) -> dict[str, Any]:
    """Write `content` to `path` on the remote host. Default is
    overwrite; pass append=True to use `>>` instead of `>`. Content
    is passed via stdin so no shell-escaping of the body is needed —
    only the destination path is shell-quoted."""
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}"}
    if not isinstance(path, str) or not path.strip():
        return {"text": "ERROR: path is empty"}
    if not isinstance(content, str):
        return {"text": "ERROR: content must be a string"}
    if len(content) > _MAX_WRITE_BYTES:
        return {"text": f"ERROR: content exceeds {_MAX_WRITE_BYTES} bytes (got {len(content)})"}

    werr = _write_remote_bytes(host, path, content.encode("utf-8"), append=append)
    if werr is not None:
        return {"text": f"ERROR: {werr}"}
    verb = "Appended to" if append else "Wrote"
    return {
        "text": f"{verb} ssh:{host}:{path} ({len(content)} chars)",
        "touched_host": host,
        "touched_path": path,
    }


def tool_ssh_replace(*, host: str, path: str, old: str, new: str) -> dict[str, Any]:
    """Replace every literal occurrence of `old` with `new` in a remote file — the
    high-level primitive that removes the need to hand-build read/filter/write
    PowerShell over ssh. Reads via the safe byte-read, edits locally, writes back
    via stdin (no escaping). `touched_path` is set only when the file actually
    changed, so a no-op (pattern absent) reads as no-progress, not success."""
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}"}
    if not isinstance(path, str) or not path.strip():
        return {"text": "ERROR: path is empty"}
    if not isinstance(old, str) or old == "":
        return {"text": "ERROR: `old` must be a non-empty string"}
    if not isinstance(new, str):
        return {"text": "ERROR: `new` must be a string"}

    raw, rerr = _read_remote_bytes(host, path, _MAX_READ_BYTES + 1)
    if rerr is not None:
        return {"text": f"ERROR: {rerr}"}
    if len(raw) > _MAX_READ_BYTES:
        return {"text": f"ERROR: file too large to edit safely (> {_MAX_READ_BYTES} bytes) — narrow it first"}
    # surrogateescape round-trips arbitrary bytes losslessly, so untouched content
    # keeps its exact encoding; only `old`→`new` is applied as UTF-8.
    text = raw.decode("utf-8", errors="surrogateescape")
    count = text.count(old)
    if count == 0:
        return {
            "text": f"ssh_replace {host}:{path}: подстрока «{old[:60]}» НЕ найдена — файл не изменён.",
            "ok": False,
        }
    new_bytes = text.replace(old, new).encode("utf-8", errors="surrogateescape")
    werr = _write_remote_bytes(host, path, new_bytes)
    if werr is not None:
        return {"text": f"ERROR: {werr}"}
    return {
        "text": f"ssh_replace {host}:{path}: заменено {count}× «{old[:40]}» → «{new[:40]}».",
        "touched_host": host,
        "touched_path": path,
    }


def _ssh_assert(host: str, path: str, pattern: str, *, want: bool) -> dict[str, Any]:
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}"}
    if not isinstance(path, str) or not path.strip():
        return {"text": "ERROR: path is empty"}
    if not isinstance(pattern, str) or pattern == "":
        return {"text": "ERROR: `pattern` must be a non-empty string"}
    raw, rerr = _read_remote_bytes(host, path, _MAX_READ_BYTES + 1)
    if rerr is not None:
        return {"text": f"ERROR: {rerr}"}
    present = pattern in decode_console(raw)
    ok = present is want
    verdict = "НАЙДЕНО" if present else "НЕ НАЙДЕНО"
    kind = "contains" if want else "not_contains"
    return {
        "text": f"ssh_assert_{kind} {host}:{path} «{pattern[:60]}»: {verdict} → {'OK' if ok else 'FAIL'}",
        "ok": ok,
        "touched_host": host,
    }


def tool_ssh_assert_contains(*, host: str, path: str, pattern: str) -> dict[str, Any]:
    """Verifier: assert a remote file CONTAINS `pattern`. Returns ok=True/False —
    a real success criterion, not raw output the model has to eyeball."""
    return _ssh_assert(host, path, pattern, want=True)


def tool_ssh_assert_not_contains(*, host: str, path: str, pattern: str) -> dict[str, Any]:
    """Verifier: assert a remote file does NOT contain `pattern` (e.g. proving a
    line was removed). Returns ok=True/False."""
    return _ssh_assert(host, path, pattern, want=False)


def tool_ssh_port_check(*, host: str, port: int) -> dict[str, Any]:
    """Verifier: is `port` LISTENING on the remote host? Windows-first (PowerShell
    Get-NetTCPConnection via base64, no quoting), with a POSIX `ss`/`netstat`
    fallback. Returns ok=True when the port is listening, with the owning pid as
    evidence."""
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}"}
    try:
        p = int(port)
    except (TypeError, ValueError):
        return {"text": "ERROR: `port` must be an integer"}
    if not (1 <= p <= 65535):
        return {"text": "ERROR: `port` out of range (1–65535)"}

    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$c=Get-NetTCPConnection -State Listen -LocalPort {p};"
        "if($c){$c|ForEach-Object{'LISTENING '+$_.LocalAddress+':'+$_.LocalPort+' pid='+$_.OwningProcess}}"
        "else{'NOT-LISTENING'}"
    )
    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    win_cmd = f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"
    try:
        proc = subprocess.run([*_ssh_args(host), win_cmd], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"text": f"ERROR: ssh {host} port check timed out"}
    except FileNotFoundError:
        return {"text": "ERROR: `ssh` binary not found on this machine"}
    out = decode_console(proc.stdout)
    # PowerShell missing (POSIX remote) → fall back to ss/netstat.
    if proc.returncode != 0 and _looks_like_windows_no_cmd(proc.stderr):
        posix = f"ss -ltn 2>/dev/null | grep -w ':{p}' || netstat -ltn 2>/dev/null | grep -w ':{p}'"
        try:
            proc = subprocess.run([*_ssh_args(host), posix], capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            return {"text": f"ERROR: ssh {host} port check timed out"}
        out = decode_console(proc.stdout)
        listening = bool(out.strip())
    else:
        # "NOT-LISTENING" contains "LISTENING" as a substring — check for the
        # negative sentinel first so a not-listening port isn't read as listening.
        listening = "NOT-LISTENING" not in out and "LISTENING" in out
    return {
        "text": f"ssh_port_check {host}:{p}: {'LISTENING' if listening else 'НЕ слушает'}\n{_truncate_for_llm(out.rstrip())}",
        "ok": listening,
        "touched_host": host,
    }


def tool_ssh_run_ps(*, host: str, script: str, timeout: int = 120) -> dict[str, Any]:
    """Run a PowerShell SCRIPT on a remote Windows host — the safe way.

    The script is base64-encoded (UTF-16LE) and handed to
    `powershell -EncodedCommand`, so NOTHING in it is parsed by the local
    shell / ssh / cmd.exe on the way there. This is the primitive that lets the
    agent run real PowerShell (quotes, pipes, `$_`, here-strings, multi-line) in
    ONE call — instead of losing dozens of tries to 4-layer quoting the way a
    hand-escaped `ssh host "powershell …"` through run_bash does. For editing a
    remote file prefer ssh_write; for POSIX remotes use ssh_run."""
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}", "ok": False}
    if not isinstance(script, str) or not script.strip():
        return {"text": "ERROR: script is empty", "ok": False}

    safe_timeout = max(5, min(int(timeout) if timeout else 120, 600))
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    remote_cmd = (
        "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {b64}"
    )
    try:
        proc = subprocess.run(
            [*_ssh_args(host), remote_cmd],
            capture_output=True,  # bytes → decode_console (remote may emit non-ANSI)
            timeout=safe_timeout,
        )
    except subprocess.TimeoutExpired:
        return {"text": f"ERROR: ssh {host} PowerShell timed out after {safe_timeout}s", "ok": False}
    except FileNotFoundError:
        return {"text": "ERROR: `ssh` binary not found on this machine", "ok": False}
    except Exception as exc:
        logger.exception("ssh_run_ps failed for host=%s", host)
        return {"text": f"ERROR: {exc}", "ok": False}

    _out, _err = decode_console(proc.stdout), decode_console(proc.stderr)
    parts = [
        f"$ ssh {host} -- powershell -EncodedCommand (script: {len(script)} chars)",
        f"exit={proc.returncode}",
    ]
    if _out:
        parts.append(f"STDOUT:\n{_truncate_for_llm(_out.rstrip())}")
    if _err:
        parts.append(f"STDERR:\n{_truncate_for_llm(_err.rstrip())}")
    return {
        "text": "\n".join(parts),
        "touched_host": host,
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
    }


def tool_ssh_list_hosts() -> dict[str, Any]:
    """List the currently allowed SSH hosts. Useful for the agent
    when the user says 'check the production server' and there's
    only one match in the allowlist."""
    hosts = get_allowed_hosts()
    if not hosts:
        return {"text": "SSH is disabled — no hosts in the allowlist. The user must add hosts in Settings → SSH first."}
    return {"text": "Allowed SSH hosts:\n" + "\n".join(f"- {h}" for h in hosts)}


def _shell_quote(value: str) -> str:
    """Single-quote a string for safe inclusion in a remote shell
    command. Used for the file PATH only; payload content is sent
    via stdin so it never touches shell parsing."""
    # POSIX-compatible: close the quoting, escape every embedded
    # single quote, reopen. Works in bash, sh, zsh, dash.
    return "'" + value.replace("'", "'\"'\"'") + "'"


# ── Schemas ────────────────────────────────────────────────────


def _schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "ssh_run",
                "description": (
                    "Run a shell command on a remote machine via SSH. "
                    "The host must be in the user's SSH allowlist (see "
                    "ssh_list_hosts). Returns stdout + stderr + exit code."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "Host alias or address (must be in allowlist)."},
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "description": "Seconds; clamped to [5, 600]. Default 60."},
                    },
                    "required": ["host", "command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_read",
                "description": (
                    "Read a file from a remote host via SSH. Capped at "
                    "100 KB; for larger files, run `head`/`tail`/`grep` "
                    "via ssh_run instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string", "description": "Absolute or remote-relative path."},
                        "max_chars": {"type": "integer", "description": "Cap (default & max 100000)."},
                    },
                    "required": ["host", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_write",
                "description": (
                    "Write content to a remote file via SSH. Default is "
                    "overwrite; set append=true to append. Capped at 100 KB. "
                    "Content is sent via stdin so embedded quotes/newlines "
                    "are safe."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "append": {"type": "boolean", "description": "Append (>>) instead of overwrite (>). Default false."},
                    },
                    "required": ["host", "path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_run_ps",
                "description": (
                    "Run a PowerShell script on a remote WINDOWS host via SSH. "
                    "The script is sent base64-encoded (EncodedCommand) so quotes, "
                    "pipes, $_ , and here-strings are NEVER mangled by ssh/cmd.exe — "
                    "use this instead of hand-escaping `ssh host \"powershell …\"` "
                    "through run_bash. To edit a remote file prefer ssh_write; for "
                    "POSIX remotes use ssh_run."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "script": {
                            "type": "string",
                            "description": "Raw PowerShell source — no escaping needed.",
                        },
                        "timeout": {"type": "integer", "description": "Seconds; clamped to [5, 600]. Default 120."},
                    },
                    "required": ["host", "script"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_replace",
                "description": (
                    "Replace every literal occurrence of `old` with `new` in a "
                    "remote file. High-level edit primitive — use this instead of "
                    "hand-building Get-Content|Where-Object|Set-Content over ssh. "
                    "No-op (pattern absent) is reported, not a silent success."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string"},
                        "old": {"type": "string", "description": "Literal substring to remove/replace."},
                        "new": {"type": "string", "description": "Replacement (use \"\" to delete `old`)."},
                    },
                    "required": ["host", "path", "old", "new"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_assert_contains",
                "description": (
                    "Verifier: assert a remote file CONTAINS a substring. Returns "
                    "ok=true/false — a real pass/fail check, not raw text to eyeball."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string"},
                        "pattern": {"type": "string"},
                    },
                    "required": ["host", "path", "pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_assert_not_contains",
                "description": (
                    "Verifier: assert a remote file does NOT contain a substring "
                    "(e.g. proving a line was removed). Returns ok=true/false."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string"},
                        "pattern": {"type": "string"},
                    },
                    "required": ["host", "path", "pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_port_check",
                "description": (
                    "Verifier: is a TCP port LISTENING on the remote host? "
                    "Returns ok=true with the owning pid as evidence. Windows and "
                    "POSIX remotes both handled."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                    },
                    "required": ["host", "port"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_list_hosts",
                "description": (
                    "Return the list of hosts the user has whitelisted for "
                    "SSH. Call this first when you're unsure what host to "
                    "use."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


_DISPATCH = {
    "ssh_run": tool_ssh_run,
    "ssh_read": tool_ssh_read,
    "ssh_write": tool_ssh_write,
    "ssh_run_ps": tool_ssh_run_ps,
    "ssh_replace": tool_ssh_replace,
    "ssh_assert_contains": tool_ssh_assert_contains,
    "ssh_assert_not_contains": tool_ssh_assert_not_contains,
    "ssh_port_check": tool_ssh_port_check,
    "ssh_list_hosts": lambda **_: tool_ssh_list_hosts(),
}


# ── Provider class ─────────────────────────────────────────────


class SshToolProvider:
    """Implements the ToolProvider Protocol. Auto-disabled when the
    allowlist is empty — the registry will then skip it entirely."""

    name = "ssh"

    def is_enabled(self) -> bool:
        return is_ssh_enabled()

    def get_schemas(self) -> list[dict[str, Any]]:
        return _schemas()

    def owns(self, tool_name: str) -> bool:
        return tool_name in _DISPATCH

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = _DISPATCH.get(tool_name)
        if handler is None:
            return {"text": f"ERROR: unknown SSH tool '{tool_name}'"}
        try:
            return handler(**args)
        except TypeError as exc:
            return {"text": f"ERROR: bad arguments to {tool_name}: {exc}"}
        except Exception as exc:
            logger.exception("ssh tool %s crashed", tool_name)
            return {"text": f"ERROR: {exc}"}
