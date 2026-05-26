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

import logging
import subprocess
from pathlib import Path
from typing import Any

from app.application.tool_providers.ssh_acl import (
    get_allowed_hosts,
    is_host_allowed,
    is_ssh_enabled,
)


logger = logging.getLogger(__name__)


# Reasonable upper bounds so a hallucinating LLM can't try to
# `ssh_read` a 10GB log file or write a runaway content blob.
_MAX_READ_BYTES = 100_000
_MAX_WRITE_BYTES = 100_000
# Tool output back to the LLM is also capped (separate from the
# read cap, which limits what we fetch over the wire).
_LLM_OUTPUT_LIMIT = 16_000


def _truncate_for_llm(text: str, limit: int = _LLM_OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated {len(text) - limit} chars]"


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
        return {"text": f"ERROR: {err}"}
    if not isinstance(command, str) or not command.strip():
        return {"text": "ERROR: command is empty"}

    safe_timeout = max(5, min(int(timeout) if timeout else 60, 600))
    try:
        proc = subprocess.run(
            [*_ssh_args(host), command],
            capture_output=True,
            text=True,
            timeout=safe_timeout,
        )
    except subprocess.TimeoutExpired:
        return {"text": f"ERROR: ssh {host} command timed out after {safe_timeout}s"}
    except FileNotFoundError:
        return {"text": "ERROR: `ssh` binary not found on this machine"}
    except Exception as exc:
        logger.exception("ssh_run failed for host=%s", host)
        return {"text": f"ERROR: {exc}"}

    parts = [f"$ ssh {host} -- {command}", f"exit={proc.returncode}"]
    if proc.stdout:
        parts.append(f"STDOUT:\n{_truncate_for_llm(proc.stdout.rstrip())}")
    if proc.stderr:
        parts.append(f"STDERR:\n{_truncate_for_llm(proc.stderr.rstrip())}")
    return {"text": "\n".join(parts), "touched_host": host}


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
    # `head -c N` is a safe way to bound the wire transfer
    remote_cmd = f"head -c {cap + 1} -- {_shell_quote(path)}"
    try:
        proc = subprocess.run(
            [*_ssh_args(host), remote_cmd],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"text": f"ERROR: ssh {host} read timed out"}
    except FileNotFoundError:
        return {"text": "ERROR: `ssh` binary not found on this machine"}

    if proc.returncode != 0:
        return {"text": f"ERROR: remote read failed (exit {proc.returncode}): {proc.stderr.rstrip()}"}

    body = proc.stdout
    truncated = len(body) > cap
    if truncated:
        body = body[:cap]
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

    op = ">>" if append else ">"
    remote_cmd = f"cat {op} {_shell_quote(path)}"
    try:
        proc = subprocess.run(
            [*_ssh_args(host), remote_cmd],
            input=content,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"text": f"ERROR: ssh {host} write timed out"}
    except FileNotFoundError:
        return {"text": "ERROR: `ssh` binary not found on this machine"}

    if proc.returncode != 0:
        return {"text": f"ERROR: remote write failed (exit {proc.returncode}): {proc.stderr.rstrip()}"}

    verb = "Appended to" if append else "Wrote"
    return {
        "text": f"{verb} ssh:{host}:{path} ({len(content)} chars)",
        "touched_host": host,
        "touched_path": path,
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
