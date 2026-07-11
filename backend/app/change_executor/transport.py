"""Executor SSH transport.

The executor builds its OWN argv from the service-owned Target (never a stored alias):
`StrictHostKeyChecking=yes` with the pinned known_hosts and the executor-owned identity
file. Two commands only:
  * inspect — `systemctl show -p <fixed props> <unit>` (read-only), parsed via the shared
    it_ops systemd projection;
  * apply — the FIXED absolute `sudo -n /usr/bin/systemctl restart <unit>` that the host's
    narrow NOPASSWD sudoers rule authorizes.

`run` is injectable so the engine's every apply/post-check/drift path is exercised over a
fake transport in tests — the real host is only ever driven by the success + reject smoke.
"""
from __future__ import annotations

import hashlib
import subprocess

from ._frozen import show_remote_command
from .registry import Target

_SSH = "ssh"
INSPECT_TIMEOUT = 15
APPLY_TIMEOUT = 30
_SYSTEMCTL_ABS = "/usr/bin/systemctl"     # absolute path — matches the exact sudoers rule


def _ssh_base(target: Target) -> list[str]:
    """HERMETIC SSH argv — no ambient ssh_config, no global known_hosts, no agent, no
    default/agent keys, no host-key updates. Only the service-owned config is honoured, so
    a poisoned user config / agent cannot redirect the change or weaken host-key pinning."""
    return [
        _SSH,
        "-F", "none",                                     # ignore any ssh_config
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "GlobalKnownHostsFile=none",
        "-o", f"UserKnownHostsFile={target.known_hosts}",  # service-owned pin
        "-o", "IdentitiesOnly=yes",                       # only the -i key
        "-o", "IdentityAgent=none",                       # no ssh-agent
        "-o", "UpdateHostKeys=no",                        # server can't rotate the pin
        "-o", "ConnectTimeout=10",
        "-i", target.identity_file,
        "-p", str(target.port),
        f"{target.remote_user}@{target.host}",
    ]


def inspect_argv(target: Target) -> list[str]:
    """Read-only `systemctl show -p <fixed props> <unit>` over the executor's identity."""
    return _ssh_base(target) + show_remote_command(target.unit)


def known_hosts_sha256(target: Target) -> str:
    """SHA-256 of the pinned known_hosts CONTENT (empty string if unreadable) — part of the
    immutable planned binding, so a swapped host-key pin is detected before any SSH."""
    try:
        with open(target.known_hosts, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def target_binding(target: Target) -> dict:
    """The immutable binding an apply/resolve is pinned to. Compared against the current
    registry binding before SSH; any drift → aborted_before_apply, no SSH."""
    return {"host": target.host, "port": target.port, "remote_user": target.remote_user,
            "unit": target.unit, "operation": target.operation,
            "known_hosts_sha256": known_hosts_sha256(target)}


def apply_argv(target: Target) -> list[str]:
    """The FIXED privileged apply: `sudo -n /usr/bin/systemctl restart <unit>`. No shell,
    absolute path, operation is a server constant (v1: restart)."""
    return _ssh_base(target) + ["sudo", "-n", _SYSTEMCTL_ABS, target.operation, target.unit]


class SshResult:
    """A completed SSH invocation: a DEFINITE exit code + captured output. The engine maps
    exit 255 (ssh transport error) / a raised Timeout to `apply_unknown`, and other
    non-zero to `command_failed`."""

    def __init__(self, exit_code: int, stdout: bytes, stderr: bytes):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def run(argv: list[str], timeout: int) -> SshResult:
    """Default runner. Raises subprocess.TimeoutExpired on timeout and OSError if ssh could
    not be launched — the engine classifies both. Never raises on a non-zero exit."""
    proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    return SshResult(proc.returncode, proc.stdout, proc.stderr)
