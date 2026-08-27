"""SSH provider — agent tools for running commands and r/w files on
remote machines via the local `ssh` binary.

Why not paramiko/fabric: the user already has working SSH (keys,
~/.ssh/config, known_hosts, sometimes ProxyJump). Reusing the OS
ssh binary inherits every bit of that setup automatically and
costs zero new dependencies. paramiko would re-implement half of
ssh in Python and would NOT pick up the user's config without
extra glue.

Runtime model:
  * Provider is always available to the local workflow; saved hosts are
    discovery shortcuts, not an allowlist.
  * Every non-empty host token is passed as its own argv item to ssh.
  * `ssh -o BatchMode=yes` — never prompt for a password. The
    user must have key-based auth set up for the host.
  * `ssh -o StrictHostKeyChecking=accept-new` — first-time hosts
    get auto-recorded in known_hosts; existing host-key changes
    are rejected (no silent MITM).
  * Command-only calls use `ssh -n` so a remote program that accidentally reads
    stdin receives EOF instead of keeping the workflow open forever. File writes
    explicitly forward stdin and omit `-n`.
  * No `-t` (TTY allocation) — purely batch mode.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from app.application.tool_providers.ssh_acl import (
    get_allowed_hosts,
    is_ssh_enabled,
    resolve_allowed_host,
)
from app.infrastructure.encoding import decode_console
from app.infrastructure.text import truncate_middle
from app.application.code_agent.tools._shell import (
    _kill_proc_tree,
    _new_process_group_kwargs,
    register_run_process,
    unregister_run_process,
)


logger = logging.getLogger(__name__)


_DEFAULT_READ_BYTES = 100_000
# Tool output back to the LLM is also capped (separate from the
# read cap, which limits what we fetch over the wire).
_LLM_OUTPUT_LIMIT = 16_000
_FOREGROUND_SLEEP_LIMIT_SECONDS = 30.0
_REMOTE_PID_MARKER = "__ELIRA_REMOTE_PID__="
_REMOTE_CLEANUP_TIMEOUT_SECONDS = 20.0
_POWERSHELL_COMMAND_PREFIX_RE = re.compile(
    r"^\s*(?:(?:cmd(?:\.exe)?\s+(?:/c|/s\s+/c)\s+)?"
    r"(?:powershell|pwsh)(?:\.exe)?\b)",
    re.IGNORECASE,
)


def _contains_long_sleep(command: str) -> bool:
    normalized = " ".join(str(command or "").split()).lower()
    duration_patterns = (
        (r"\bstart-sleep\s+(?:-seconds?\s+)?(\d+(?:\.\d+)?)", 1.0),
        (r"\bstart-sleep\s+-milliseconds?\s+(\d+(?:\.\d+)?)", 0.001),
        (r"(?<![-\w])sleep\s+(\d+(?:\.\d+)?)", 1.0),
        (r"\btimeout\s+/t\s+(\d+(?:\.\d+)?)", 1.0),
    )
    for pattern, multiplier in duration_patterns:
        for match in re.finditer(pattern, normalized):
            if float(match.group(1)) * multiplier >= _FOREGROUND_SLEEP_LIMIT_SECONDS:
                return True
    return False


def _foreground_block_reason(command: str) -> str | None:
    """Return why *command* must not own a synchronous SSH tool call.

    Keep this deliberately narrow: these PowerShell primitives explicitly wait
    for another process and caused real workflows to remain inside one tool call
    for minutes. Ordinary finite remote commands still use the foreground path.
    """
    normalized = " ".join(str(command or "").split()).lower()
    if "start-process" in normalized and re.search(
        r"(?:^|\s)-wait(?=$|[\s;|&)])",
        normalized,
    ):
        return "powershell_start_process_wait"
    if re.search(r"\.waitforexit\s*\(", normalized):
        return "powershell_wait_for_exit"
    if re.search(r"\.waitforstatus\s*\(", normalized):
        return "powershell_wait_for_status"
    if re.search(r"(?:^|[;|&]\s*|\s)wait-process(?:\s|$)", normalized):
        return "powershell_wait_process"
    if re.search(r"(?:^|[;|&]\s*|\s)(?:start|restart|stop)-service(?:\s|$)", normalized):
        return "powershell_service_control"
    if _contains_long_sleep(normalized):
        return "long_sleep"
    return None


def _needs_background_result(
    reason: str,
    *,
    recommended_tool: str = "run_server",
    recommended_arguments: dict[str, Any] | None = None,
    missing_required_arguments: list[str] | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    feedback = {
        "status": "needs_background",
        "error": "long_running_foreground",
        "reason": reason,
        "recommended_tool": recommended_tool,
        "recommended_arguments": recommended_arguments
        or {"action": "start", "kind": "job"},
        "next_action": next_action
        or (
            "Launch the equivalent SSH command with run_server(kind='job'), "
            "then poll run_server(action='logs', pid=...). Do not retry it "
            "through a synchronous SSH tool."
        ),
    }
    if missing_required_arguments:
        feedback["missing_required_arguments"] = list(missing_required_arguments)
    return {
        **feedback,
        "ok": False,
        "text": json.dumps(feedback, ensure_ascii=False, indent=2),
    }


def _background_ssh_argv(tool_name: str, args: dict[str, Any]) -> list[str]:
    host = str(args.get("host") or "").strip()
    if tool_name == "ssh_run_ps":
        return _ssh_command_argv(
            host,
            _powershell_managed_job_command(str(args.get("script") or "")),
        )

    connect_timeout = max(1, int(args.get("timeout", 60)))
    return _ssh_command_argv(
        host,
        str(args.get("command") or ""),
        connect_timeout_seconds=connect_timeout,
    )


def _background_ssh_display(tool_name: str, args: dict[str, Any]) -> str:
    host = resolve_allowed_host(str(args.get("host") or "")) or str(
        args.get("host") or ""
    ).strip()
    if tool_name == "ssh_run_ps":
        return (
            f"ssh {host} -- powershell -EncodedCommand "
            f"(script: {len(str(args.get('script') or ''))} chars)"
        )
    return f"ssh {host} -- {str(args.get('command') or '')}"


def _start_background_ssh_job(
    project_root: Path,
    *,
    tool_name: str,
    args: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    from app.application.code_agent.tools._run import start_background_argv_job

    try:
        argv = _background_ssh_argv(tool_name, args)
    except (TypeError, ValueError):
        return {"text": "ERROR: timeout must be an integer", "ok": False}

    runtime_metadata = (
        {
            "remote_cleanup": {
                "kind": "ssh_windows_process_tree",
                "host": resolve_allowed_host(str(args.get("host") or ""))
                or str(args.get("host") or "").strip(),
                "remote_pid_marker": _REMOTE_PID_MARKER,
            }
        }
        if tool_name == "ssh_run_ps"
        else None
    )
    result = start_background_argv_job(
        project_root,
        argv=argv,
        display_command=_background_ssh_display(tool_name, args),
        runtime_metadata=runtime_metadata,
    )
    remote_cleanup_supported = bool(
        result.get("ok") and tool_name == "ssh_run_ps"
    )
    redirected = {
        **result,
        "backgrounded": bool(result.get("ok")),
        "redirected_from": tool_name,
        "redirect_reason": reason,
        "recommended_tool": "run_server",
        "remote_cleanup_supported": remote_cleanup_supported,
    }
    if not result.get("ok"):
        return redirected
    pid = int(result["pid"])
    structured_feedback = {
        "status": str(result.get("status") or "running"),
        "backgrounded": True,
        "pid": pid,
        "kind": "job",
        "redirected_from": tool_name,
        "redirect_reason": reason,
        "recommended_tool": "run_server",
        "recommended_arguments": {"action": "logs", "kind": "job", "pid": pid},
        "remote_pid": result.get("remote_pid"),
        "remote_pid_pending": bool(result.get("remote_pid_pending")),
        "remote_cleanup_supported": remote_cleanup_supported,
        "remote_process_identity_captured": bool(
            result.get("remote_process_identity_captured")
        ),
        "remote_cleanup_ready": bool(result.get("remote_cleanup_ready")),
        "next_actions": [
            {
                "when": "monitor",
                "tool": "run_server",
                "arguments": {"action": "logs", "kind": "job", "pid": pid},
            },
            {
                "when": "cancel",
                "tool": "run_server",
                "arguments": {"action": "stop", "kind": "job", "pid": pid},
            },
        ],
    }
    redirected["recommended_arguments"] = structured_feedback[
        "recommended_arguments"
    ]
    redirected["text"] = (
        f"Blocking {tool_name} call was moved to the managed background job runtime.\n"
        f"{result.get('text', '')}\n"
        f"Next: run_server(action='logs', kind='job', pid={pid}) until status is terminal.\n\n"
        "STRUCTURED_TOOL_RESULT:\n"
        f"{json.dumps(structured_feedback, ensure_ascii=False, indent=2)}"
    )
    return redirected


def _truncate_for_llm(text: str, limit: int = _LLM_OUTPUT_LIMIT) -> str:
    # Was a head-only cut that dropped the exit code / last error at the bottom of
    # a remote command's output — now the shared head+tail truncation keeps both.
    return truncate_middle(text, limit)


def _powershell_remote_command(script: str) -> str:
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return (
        "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {b64}"
    )


def _powershell_managed_job_command(script: str) -> str:
    payload = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    wrapper = (
        "$ErrorActionPreference='Stop';"
        f"$payload='{payload}';"
        "$arguments=@('-NoProfile','-NonInteractive','-ExecutionPolicy',"
        "'Bypass','-EncodedCommand',$payload);"
        "$process=Start-Process -FilePath 'powershell.exe' "
        "-ArgumentList $arguments -PassThru -NoNewWindow;"
        "$startedTicks=$process.StartTime.ToUniversalTime().Ticks;"
        f"Write-Output ('{_REMOTE_PID_MARKER}'+$process.Id+':'+$startedTicks);"
        "[Console]::Out.Flush();"
        "$process.WaitForExit();"
        "exit $process.ExitCode"
    )
    return _powershell_remote_command(wrapper)


def _ssh_args(
    host: str,
    *,
    forward_stdin: bool = False,
    connect_timeout_seconds: int | None = None,
) -> list[str]:
    """Shared ssh flags every tool uses. Order matters here — flags
    before the destination."""
    # Saved aliases are only friendly discovery metadata; an arbitrary direct
    # hostname/IP is preserved and passed as one argv item.
    canonical = resolve_allowed_host(host) or host.strip()
    args = ["ssh"]
    if not forward_stdin:
        # OpenSSH's native null-stdin mode is required in addition to Popen's
        # DEVNULL on Windows: it closes the SSH channel's input, so a malformed
        # remote command that reads stdin cannot remain blocked in pipe_read.
        args.append("-n")
    if connect_timeout_seconds is not None:
        args.extend(["-o", f"ConnectTimeout={max(1, int(connect_timeout_seconds))}"])
    compatibility_args: list[str] = []
    try:
        from app.application.it_ops.mikrotik_registry import ssh_compatibility_args

        compatibility_args = ssh_compatibility_args(canonical)
    except Exception:
        logger.warning("failed to resolve per-target SSH compatibility", exc_info=True)
    return [
        *args,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        *compatibility_args,
        canonical,
    ]


def _ssh_command_argv(
    host: str,
    command: str,
    *,
    connect_timeout_seconds: int | None = None,
) -> list[str]:
    return [
        *_ssh_args(
            host,
            connect_timeout_seconds=connect_timeout_seconds,
        ),
        command,
    ]


def _validate_host(host: str) -> str | None:
    """Validate only argv/protocol integrity; this is not an authorization gate."""
    if not isinstance(host, str) or not host.strip():
        return "host is empty"
    if host.lstrip().startswith("-") or "\x00" in host or "\r" in host or "\n" in host:
        return "host is not a valid ssh destination token"
    return None


def run_registered_process(
    argv: list[str],
    *,
    input: bytes | None = None,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a child until exit/Workflow Stop, optionally with an internal bound."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_new_process_group_kwargs(),
    )
    run_id = register_run_process(proc)
    try:
        try:
            stdout, stderr = proc.communicate(
                input=input,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            _kill_proc_tree(proc)
            if proc.poll() is None:
                proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise
    finally:
        unregister_run_process(run_id, proc)
    return subprocess.CompletedProcess(argv, int(proc.returncode), stdout or b"", stderr or b"")


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
    block_reason = _foreground_block_reason(command)
    if block_reason is not None:
        if _POWERSHELL_COMMAND_PREFIX_RE.search(command):
            return _needs_background_result(
                block_reason,
                recommended_tool="ssh_run_ps",
                recommended_arguments={"host": host},
                missing_required_arguments=["script"],
                next_action=(
                    "Extract the PowerShell source from this rejected raw command "
                    "and call ssh_run_ps(host, script). The typed tool preserves "
                    "quoting, captures the remote PID and uses a managed job."
                ),
            )
        return _needs_background_result(block_reason)

    try:
        # This is only the OpenSSH connection-establishment timeout. Once the
        # remote command starts, it has no execution deadline; Workflow Stop is
        # the sole cancellation owner.
        connect_timeout = max(1, int(timeout))
        proc = run_registered_process(_ssh_command_argv(
            host,
            command,
            connect_timeout_seconds=connect_timeout,
        ))
    except (TypeError, ValueError):
        return {"text": "ERROR: timeout must be an integer", "ok": False}
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


def _windows_read_encoded(path: str, limit: int | None) -> str:
    """`powershell -EncodedCommand …` that reads `path` as bytes, bounded to
    `limit`, and writes them raw to stdout. base64 (UTF-16LE) so cmd.exe on the
    remote never mangles quotes/pipes — the same trap a raw PowerShell-over-ssh
    command hits. This is what makes ssh_read work on Windows hosts."""
    lit = path.replace("'", "''")  # PowerShell single-quoted literal
    length = (
        f"$n=[Math]::Min($b.Length,{int(limit)});"
        if limit is not None
        else "$n=$b.Length;"
    )
    ps = (
        "$ErrorActionPreference='Stop';"
        f"$b=[System.IO.File]::ReadAllBytes('{lit}');"
        + length
        + "$o=[Console]::OpenStandardOutput();$o.Write($b,0,$n);$o.Flush()"
    )
    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"


# Shared read/write cores — used by ssh_read/ssh_write AND the higher-level
# primitives (ssh_replace/ssh_assert_*), so the Windows byte-read fallback and the
# stdin-write (no body escaping) live in ONE place, not copied per tool.


def _read_remote_bytes(host: str, path: str, limit: int | None) -> tuple[bytes | None, str | None]:
    """Fetch a remote file, optionally limiting bytes. Returns (bytes, None) or
    (None, error). Auto-falls back to a base64 PowerShell byte-read on Windows
    remotes (cmd.exe has no `head`)."""
    remote_cmd = (
        f"head -c {int(limit)} -- {_shell_quote(path)}"
        if limit is not None
        else f"cat -- {_shell_quote(path)}"
    )
    try:
        proc = run_registered_process([*_ssh_args(host), remote_cmd])
    except FileNotFoundError:
        return None, "`ssh` binary not found on this machine"
    if proc.returncode != 0 and _looks_like_windows_no_cmd(proc.stderr):
        proc = run_registered_process(
            [*_ssh_args(host), _windows_read_encoded(path, limit)]
        )
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
            proc = run_registered_process(
                [
                    *_ssh_args(host, forward_stdin=True),
                    _windows_write_encoded(path, append=append),
                ],
                input=data,
            )
        except FileNotFoundError:
            return "`ssh` binary not found on this machine"
        if proc.returncode != 0:
            return f"remote write failed (exit {proc.returncode}): {decode_console(proc.stderr).rstrip()}"
        return None

    op = ">>" if append else ">"
    remote_cmd = f"cat {op} {_shell_quote(path)}"
    try:
        proc = run_registered_process(
            [*_ssh_args(host, forward_stdin=True), remote_cmd],
            input=data,
        )
    except FileNotFoundError:
        return "`ssh` binary not found on this machine"
    if proc.returncode != 0 and _looks_like_windows_no_cmd(proc.stderr):
        proc = run_registered_process(
            [
                *_ssh_args(host, forward_stdin=True),
                _windows_write_encoded(path, append=append),
            ],
            input=data,
        )
    if proc.returncode != 0:
        return f"remote write failed (exit {proc.returncode}): {decode_console(proc.stderr).rstrip()}"
    return None


def tool_ssh_read(*, host: str, path: str, max_chars: int | None = None) -> dict[str, Any]:
    """Read a remote file by SSHing in and `cat`ing it. Capped at
    100KB by default — pipe a larger file through head/tail/grep on
    the remote side instead of pulling it all back."""
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}", "ok": False}
    if not isinstance(path, str) or not path.strip():
        return {"text": "ERROR: path is empty", "ok": False}

    cap = max(1, int(max_chars) if max_chars else _DEFAULT_READ_BYTES)
    raw, rerr = _read_remote_bytes(host, path, cap + 1)  # +1 to detect truncation
    if rerr is not None:
        # Couldn't read (missing / permission) → ok=False, NO verifier: not a verdict,
        # so it neither confirms nor fails a file_exists criterion.
        return {"text": f"ERROR: {rerr}", "ok": False}
    truncated = len(raw) > cap
    body = decode_console(raw[:cap] if truncated else raw)
    head = f"[ssh:{host}:{path}]"
    if truncated:
        head += f"  (truncated at {cap} bytes — file is longer)"
    # A successful read PROVES the file exists — a file_exists verdict (never absence).
    return {
        "text": f"{head}\n\n{body}",
        "ok": True,
        "verifier": True,
        "evidence": f"{path}: прочитан ({len(raw)} байт) — существует",
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

    raw, rerr = _read_remote_bytes(host, path, None)
    if rerr is not None:
        return {"text": f"ERROR: {rerr}"}
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
    # ERROR branches: ok=False but NO verifier flag — the verifier couldn't RUN, so
    # it's not a verdict (a matched criterion stays unconfirmed, not failed).
    err = _validate_host(host)
    if err is not None:
        return {"text": f"ERROR: {err}", "ok": False}
    if not isinstance(path, str) or not path.strip():
        return {"text": "ERROR: path is empty", "ok": False}
    if not isinstance(pattern, str) or pattern == "":
        return {"text": "ERROR: `pattern` must be a non-empty string", "ok": False}
    raw, rerr = _read_remote_bytes(host, path, None)
    if rerr is not None:
        return {"text": f"ERROR: {rerr}", "ok": False}
    present = pattern in decode_console(raw)
    ok = present is want
    verdict = "НАЙДЕНО" if present else "НЕ НАЙДЕНО"
    kind = "contains" if want else "not_contains"
    # A real verdict → verifier=True + evidence, so the criteria tracker can mark
    # the matching criterion confirmed (ok) or failed (not ok).
    return {
        "text": f"ssh_assert_{kind} {host}:{path} «{pattern[:60]}»: {verdict} → {'OK' if ok else 'FAIL'}",
        "ok": ok,
        "verifier": True,
        "evidence": f"«{pattern[:60]}» {verdict} в {path}",
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
        return {"text": f"ERROR: {err}", "ok": False}
    try:
        p = int(port)
    except (TypeError, ValueError):
        return {"text": "ERROR: `port` must be an integer", "ok": False}
    if not (1 <= p <= 65535):
        return {"text": "ERROR: `port` out of range (1–65535)", "ok": False}

    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$c=Get-NetTCPConnection -State Listen -LocalPort {p};"
        "if($c){$c|ForEach-Object{'LISTENING '+$_.LocalAddress+':'+$_.LocalPort+' pid='+$_.OwningProcess}}"
        "else{'NOT-LISTENING'}"
    )
    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    win_cmd = f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"
    try:
        proc = run_registered_process([*_ssh_args(host), win_cmd])
    except FileNotFoundError:
        return {"text": "ERROR: `ssh` binary not found on this machine", "ok": False}
    out = decode_console(proc.stdout)
    # PowerShell missing (POSIX remote) → fall back to ss/netstat.
    if proc.returncode != 0 and _looks_like_windows_no_cmd(proc.stderr):
        posix = f"ss -ltn 2>/dev/null | grep -w ':{p}' || netstat -ltn 2>/dev/null | grep -w ':{p}'"
        proc = run_registered_process([*_ssh_args(host), posix])
        out = decode_console(proc.stdout)
        listening = bool(out.strip())
    else:
        # "NOT-LISTENING" contains "LISTENING" as a substring — check for the
        # negative sentinel first so a not-listening port isn't read as listening.
        listening = "NOT-LISTENING" not in out and "LISTENING" in out
    return {
        "text": f"ssh_port_check {host}:{p}: {'LISTENING' if listening else 'НЕ слушает'}\n{_truncate_for_llm(out.rstrip())}",
        "ok": listening,
        "verifier": True,
        "evidence": f"порт {p} {'LISTENING' if listening else 'не слушает'}: {out.strip()[:120]}",
        "touched_host": host,
    }


def _ssh_probe_exists(host: str, path: str) -> tuple[bool | None, str, dict[str, Any] | None]:
    """Probe whether `path` exists on `host` (file or directory). Windows-first
    (PowerShell Test-Path via base64, no quoting) with a POSIX `test` fallback.
    Returns (exists, kind, error_result): a real verdict → (True/False, kind, None);
    couldn't run → (None, "", {ERROR result, ok=False, no verifier})."""
    err = _validate_host(host)
    if err is not None:
        return None, "", {"text": f"ERROR: {err}", "ok": False}
    if not isinstance(path, str) or not path.strip():
        return None, "", {"text": "ERROR: path is empty", "ok": False}

    esc = path.replace("'", "''")  # PowerShell single-quote literal escaping
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"if(Test-Path -LiteralPath '{esc}'){{"
        f"if(Test-Path -LiteralPath '{esc}' -PathType Container){{'EXISTS DIR'}}else{{'EXISTS FILE'}}"
        "}else{'MISSING'}"
    )
    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    win_cmd = f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"
    try:
        proc = run_registered_process([*_ssh_args(host), win_cmd])
    except FileNotFoundError:
        return None, "", {"text": "ERROR: `ssh` binary not found on this machine", "ok": False}
    out = decode_console(proc.stdout)
    # PowerShell missing (POSIX remote) → fall back to `test`.
    if proc.returncode != 0 and _looks_like_windows_no_cmd(proc.stderr):
        q = _shell_quote(path)
        posix = f"if [ -d {q} ]; then echo 'EXISTS DIR'; elif [ -e {q} ]; then echo 'EXISTS FILE'; else echo 'MISSING'; fi"
        proc = run_registered_process([*_ssh_args(host), posix])
        out = decode_console(proc.stdout)
    exists = "EXISTS" in out
    kind = "директория" if "EXISTS DIR" in out else ("файл" if "EXISTS FILE" in out else "нет")
    return exists, kind, None


def tool_ssh_exists(*, host: str, path: str) -> dict[str, Any]:
    """Verifier: does `path` EXIST on the remote host (file or directory)? Returns
    ok=True when the path exists, with the kind as evidence — so a "файл создан"/"папка
    существует" criterion is confirmed by a verdict, not the model's word. The ERROR
    branch (bad host / empty path / probe failed) returns ok=False WITHOUT a verifier
    flag, so a matching criterion stays unconfirmed rather than marked failed."""
    exists, kind, err = _ssh_probe_exists(host, path)
    if err is not None:
        return err
    return {
        "text": f"ssh_exists {host}:{path}: {'ЕСТЬ (' + kind + ')' if exists else 'НЕ найден'} → {'OK' if exists else 'FAIL'}",
        "ok": bool(exists),
        "verifier": True,
        "evidence": f"{path}: {'существует (' + kind + ')' if exists else 'не найден'}",
        "touched_host": host,
    }


def tool_ssh_not_exists(*, host: str, path: str) -> dict[str, Any]:
    """Verifier for CLEANUP: assert `path` is GONE. Returns ok=True when the path is
    ABSENT (cleanup succeeded) — so a "временный файл удалён" criterion is confirmed by
    a verdict, and a still-present path is a real FAIL, not a tool error. The mirror of
    ssh_exists; ERROR branch (couldn't probe) → ok=False, no verifier."""
    exists, kind, err = _ssh_probe_exists(host, path)
    if err is not None:
        return err
    gone = not exists
    return {
        "text": f"ssh_not_exists {host}:{path}: {'УДАЛЁН' if gone else 'НЕ УДАЛЁН (' + kind + ')'} → {'OK' if gone else 'FAIL'}",
        "ok": gone,
        "verifier": True,
        "evidence": f"{path}: {'отсутствует — cleanup ок' if gone else 'ещё существует (' + kind + ')'}",
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
    block_reason = _foreground_block_reason(script)
    if block_reason is not None:
        return _needs_background_result(block_reason)

    del timeout  # compatibility input; Workflow Stop owns termination
    remote_cmd = _powershell_remote_command(script)
    try:
        proc = run_registered_process(_ssh_command_argv(host, remote_cmd))
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


def stop_remote_windows_process_tree(
    *,
    host: str,
    remote_pid: int,
    remote_started_ticks: int | None = None,
) -> dict[str, Any]:
    """Stop and verify one managed remote Windows process tree over SSH."""
    err = _validate_host(host)
    if err is not None:
        return {
            "ok": False,
            "remote_pid": remote_pid,
            "remote_cleanup_status": "invalid_host",
            "text": f"ERROR: {err}",
        }
    try:
        pid = int(remote_pid)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return {
            "ok": False,
            "remote_pid": remote_pid,
            "remote_cleanup_status": "invalid_pid",
            "text": "ERROR: remote_pid must be a positive integer",
        }
    try:
        expected_started_ticks = int(remote_started_ticks or 0)
    except (TypeError, ValueError):
        expected_started_ticks = 0
    if expected_started_ticks <= 0:
        return {
            "ok": False,
            "remote_pid": pid,
            "remote_cleanup_status": "invalid_identity",
            "text": "ERROR: remote process-start identity is required",
        }

    script = (
        "$ErrorActionPreference='Stop';"
        f"$targetPid={pid};"
        f"$expectedStartTicks={expected_started_ticks};"
        "$process=Get-Process -Id $targetPid -ErrorAction SilentlyContinue;"
        "if($null -eq $process){"
        "Write-Output ('__ELIRA_REMOTE_STOP__=root_missing_unverified:'+$targetPid);"
        "exit 4};"
        "$actualStartTicks=$process.StartTime.ToUniversalTime().Ticks;"
        "if($expectedStartTicks -gt 0 -and $actualStartTicks -ne $expectedStartTicks){"
        "Write-Output ('__ELIRA_REMOTE_STOP__=identity_mismatch:'+$actualStartTicks);"
        "exit 3};"
        "$tracked=@([pscustomobject]@{"
        "Pid=[int]$targetPid;StartTicks=[int64]$actualStartTicks});"
        "$queue=New-Object System.Collections.Queue;"
        "$queue.Enqueue($targetPid);"
        "while($queue.Count -gt 0){"
        "$parentPid=[int]$queue.Dequeue();"
        "$children=@(Get-CimInstance Win32_Process "
        "-Filter ('ParentProcessId='+$parentPid));"
        "foreach($child in $children){"
        "$childPid=[int]$child.ProcessId;"
        "if(-not ($tracked | Where-Object {$_.Pid -eq $childPid})){"
        "$childProcess=Get-Process -Id $childPid -ErrorAction SilentlyContinue;"
        "if($null -ne $childProcess){"
        "$childTicks=$childProcess.StartTime.ToUniversalTime().Ticks;"
        "$tracked+=@([pscustomobject]@{"
        "Pid=$childPid;StartTicks=[int64]$childTicks});"
        "$queue.Enqueue($childPid)}}}};"
        "$rootNow=Get-Process -Id $targetPid -ErrorAction SilentlyContinue;"
        "if($null -eq $rootNow){"
        "Write-Output ('__ELIRA_REMOTE_STOP__=root_missing_unverified:'+$targetPid);"
        "exit 4};"
        "if($rootNow.StartTime.ToUniversalTime().Ticks -ne $expectedStartTicks){"
        "Write-Output ('__ELIRA_REMOTE_STOP__=identity_mismatch:'+"
        "$rootNow.StartTime.ToUniversalTime().Ticks);exit 3};"
        "$taskkillOutput=& taskkill.exe /PID $targetPid /T /F 2>&1;"
        "$taskkillOutput | ForEach-Object { Write-Output $_ };"
        "Start-Sleep -Milliseconds 250;"
        "$survivors=@($tracked | Where-Object {"
        "$current=Get-Process -Id $_.Pid -ErrorAction SilentlyContinue;"
        "$null -ne $current -and "
        "$current.StartTime.ToUniversalTime().Ticks -eq $_.StartTicks});"
        "if($survivors.Count -gt 0){"
        "foreach($survivor in @($survivors | Sort-Object Pid -Descending)){"
        "$current=Get-Process -Id $survivor.Pid -ErrorAction SilentlyContinue;"
        "if($null -ne $current -and "
        "$current.StartTime.ToUniversalTime().Ticks -eq $survivor.StartTicks){"
        "Stop-Process -Id $survivor.Pid -Force -ErrorAction SilentlyContinue}};"
        "Start-Sleep -Milliseconds 250};"
        "$survivors=@($tracked | Where-Object {"
        "$current=Get-Process -Id $_.Pid -ErrorAction SilentlyContinue;"
        "$null -ne $current -and "
        "$current.StartTime.ToUniversalTime().Ticks -eq $_.StartTicks});"
        "$lateChildren=@(Get-CimInstance Win32_Process "
        "-Filter ('ParentProcessId='+$targetPid));"
        "if($survivors.Count -gt 0 -or $lateChildren.Count -gt 0){"
        "throw ('remote process tree is still running: '+"
        "(($survivors | ForEach-Object {[string]$_.Pid}) -join ','))};"
        "Write-Output ('__ELIRA_REMOTE_STOP__=stopped:'+$targetPid)"
    )
    try:
        proc = run_registered_process(_ssh_command_argv(
            host,
            _powershell_remote_command(script),
            connect_timeout_seconds=15,
        ), timeout_seconds=_REMOTE_CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "remote_pid": pid,
            "remote_cleanup_status": "timeout",
            "text": (
                "ERROR: remote cleanup did not finish within "
                f"{int(_REMOTE_CLEANUP_TIMEOUT_SECONDS)} seconds"
            ),
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "remote_pid": pid,
            "remote_cleanup_status": "ssh_unavailable",
            "text": "ERROR: `ssh` binary not found on this machine",
        }
    except Exception as exc:
        logger.exception("remote SSH cleanup failed for host=%s pid=%s", host, pid)
        return {
            "ok": False,
            "remote_pid": pid,
            "remote_cleanup_status": "ssh_error",
            "text": f"ERROR: {exc}",
        }

    stdout = decode_console(proc.stdout)
    stderr = decode_console(proc.stderr)
    stopped_match = re.search(r"__ELIRA_REMOTE_STOP__=stopped:(\d+)", stdout)
    if stopped_match is not None:
        status = "stopped"
        pid = int(stopped_match.group(1))
    elif "__ELIRA_REMOTE_STOP__=identity_mismatch" in stdout:
        status = "identity_mismatch"
    elif "__ELIRA_REMOTE_STOP__=root_missing_unverified" in stdout:
        status = "root_missing_unverified"
    else:
        status = "failed"
    ok = proc.returncode == 0 and status == "stopped"
    details = [f"remote Windows process tree pid={pid}: {status}"]
    if stdout:
        details.append(_truncate_for_llm(stdout.rstrip()))
    if stderr:
        details.append(_truncate_for_llm(stderr.rstrip()))
    return {
        "ok": ok,
        "remote_pid": pid,
        "remote_cleanup_status": status,
        "exit_code": proc.returncode,
        "text": "\n".join(details),
    }


def tool_ssh_list_hosts() -> dict[str, Any]:
    """List the currently saved SSH hosts. Useful for the agent
    when the user says 'check the production server' and there's
    only one matching favorite."""
    hosts = get_allowed_hosts()
    if not hosts:
        return {
            "ok": True,
            "hosts": [],
            "text": "Сохранённых SSH-хостов нет; можно передать любой alias, hostname или IP напрямую.",
        }

    asset_by_id: dict[str, dict[str, Any]] = {}
    profile_by_alias: dict[str, dict[str, Any]] = {}
    try:
        from app.infrastructure.it_ops import store as _itops_store

        asset_by_id = {
            str(asset.get("asset_id") or ""): asset
            for asset in _itops_store.list_assets()
        }
        profile_by_alias = {
            str(profile.get("ssh_alias") or ""): profile
            for profile in _itops_store.list_connection_profiles()
            if str(profile.get("ssh_alias") or "") in hosts
        }
    except Exception as exc:
        # Asset metadata is enrichment only. Saved SSH favorites remain usable
        # if the IT-Ops store is disabled or temporarily unavailable.
        logger.warning("ssh_list_hosts: asset enrichment unavailable: %s", type(exc).__name__)

    try:
        from app.application.it_ops.ssh_enroll import resolve_alias as _resolve_alias
    except Exception:
        _resolve_alias = None

    items: list[dict[str, Any]] = []
    lines = ["Saved SSH targets (or pass any alias, hostname or IP directly):"]
    for alias in hosts:
        profile = profile_by_alias.get(alias) or {}
        asset = asset_by_id.get(str(profile.get("asset_id") or "")) or {}
        item: dict[str, Any] = {"alias": alias}
        for key in ("asset_id", "label", "kind", "lifecycle_state"):
            value = asset.get(key)
            if isinstance(value, str) and value:
                item[key] = value
        if _resolve_alias is not None:
            try:
                effective = _resolve_alias(alias)
            except Exception:
                effective = {}
            if effective.get("ok"):
                for key in ("hostname", "port", "user"):
                    value = effective.get(key)
                    if isinstance(value, str) and value:
                        item[key] = value
        items.append(item)
        details = [alias]
        if item.get("label"):
            details.append(f"asset={item['label']}")
        if item.get("kind"):
            details.append(f"kind={item['kind']}")
        if item.get("lifecycle_state"):
            details.append(f"state={item['lifecycle_state']}")
        if item.get("hostname"):
            endpoint = str(item["hostname"])
            if item.get("port"):
                endpoint += f":{item['port']}"
            if item.get("user"):
                endpoint = f"{item['user']}@{endpoint}"
            details.append(f"target={endpoint}")
        lines.append("- " + " | ".join(details))
    return {"ok": True, "hosts": items, "text": "\n".join(lines)}


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
                    "The host may be any SSH alias, hostname or IP address. "
                    "Returns stdout + stderr + exit code. A raw PowerShell command "
                    "with a known blocking wait returns structured status "
                    "needs_background and recommended_tool=ssh_run_ps; call that "
                    "typed tool with the original PowerShell source. Other known "
                    "blocking waits are moved to an argv-safe managed run_server "
                    "job when runtime context is available; otherwise they return "
                    "a structured run_server recommendation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "SSH alias, hostname or IP address."},
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "description": "Compatibility field; execution continues until exit or Workflow Stop."},
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
                    "Read a file from a remote host via SSH. max_chars controls "
                    "how much is fetched; it is not an authorization limit."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string", "description": "Absolute or remote-relative path."},
                        "max_chars": {"type": "integer", "description": "Bytes to fetch; default 100000."},
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
                    "overwrite; set append=true to append. "
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
                    "POSIX remotes use ssh_run. Known blocking wait constructs are "
                    "automatically moved to the managed run_server(kind='job') "
                    "runtime. The result exposes the local managed pid, remote_pid "
                    "and remote_cleanup_supported. Poll with "
                    "run_server(action='logs', kind='job', pid=...), or cancel the "
                    "remote Windows process tree with run_server(action='stop', "
                    "kind='job', pid=...). A low-level caller without job runtime "
                    "context receives structured status needs_background."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "script": {
                            "type": "string",
                            "description": "Raw PowerShell source — no escaping needed.",
                        },
                        "timeout": {"type": "integer", "description": "Compatibility field; execution continues until exit or Workflow Stop."},
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
                    "ok=true/false — a real pass/fail check, not raw text to eyeball. "
                    "Use only after the exact file and expected pattern are known; "
                    "never use this to search or discover files."
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
                    "(e.g. proving a line was removed). Returns ok=true/false. "
                    "Use only after the exact file and expected absence are known; "
                    "never use this to search or discover files."
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
                "name": "ssh_exists",
                "description": (
                    "Verifier: does a path EXIST on the remote host (file or "
                    "directory)? Returns ok=true with the kind as evidence — use "
                    "this to prove a file/folder was actually created, instead of "
                    "eyeballing a Test-Path in ssh_run_ps. This checks one known "
                    "path; it is not a discovery/search tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["host", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_not_exists",
                "description": (
                    "Verifier for CLEANUP: assert a path is GONE on the remote host. "
                    "Returns ok=true when the path is ABSENT (cleanup succeeded) — use "
                    "this after deleting a file/folder so the absence is a PASS, not a "
                    "tool error. A still-present path is a real fail."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["host", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ssh_list_hosts",
                "description": (
                    "Return saved SSH host shortcuts. Arbitrary explicit hosts "
                    "remain valid; call this only when you need a known shortcut."
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
    "ssh_exists": tool_ssh_exists,
    "ssh_not_exists": tool_ssh_not_exists,
    "ssh_list_hosts": lambda **_: tool_ssh_list_hosts(),
}


# ── Provider class ─────────────────────────────────────────────


class SshToolProvider:
    """Implements the always-available local SSH ToolProvider."""

    name = "ssh"

    def __init__(self, project_root: Path | str | None = None) -> None:
        self._project_root = (
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else None
        )

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
            result = handler(**args)
            if (
                self._project_root is not None
                and (
                    tool_name == "ssh_run_ps"
                    or (
                        tool_name == "ssh_run"
                        and result.get("recommended_tool") == "run_server"
                    )
                )
                and result.get("status") == "needs_background"
            ):
                return _start_background_ssh_job(
                    self._project_root,
                    tool_name=tool_name,
                    args=args,
                    reason=str(result.get("reason") or "long_running_foreground"),
                )
            return result
        except TypeError as exc:
            return {"text": f"ERROR: bad arguments to {tool_name}: {exc}"}
        except Exception as exc:
            logger.exception("ssh tool %s crashed", tool_name)
            return {"text": f"ERROR: {exc}"}
