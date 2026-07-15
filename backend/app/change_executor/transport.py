"""Executor SSH transport.

The executor builds its OWN argv from the service-owned Target (never a stored alias):
`StrictHostKeyChecking=yes` with the pinned known_hosts and the executor-owned identity
file. The original systemd target has two commands:
  * inspect — `systemctl show -p <fixed props> <unit>` (read-only), parsed via the shared
    it_ops systemd projection;
  * apply — the FIXED absolute `sudo -n /usr/bin/systemctl restart <unit>` that the host's
    narrow NOPASSWD sudoers rule authorizes.

The typed Netdata config target adds exactly two helper calls: `inspect` and `apply`.
The helper path is fixed by the registry contract; the only dynamic apply values are the
server-created ChangeRun id and the two plan-time SHA-256 CAS values.
"""
from __future__ import annotations

import hashlib
import subprocess

from ._frozen import show_remote_command
from .registry import Target

_SSH = "ssh"
INSPECT_TIMEOUT = 15
APPLY_TIMEOUT = 30
CONFIG_APPLY_TIMEOUT = 330
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
    binding = {"host": target.host, "port": target.port, "remote_user": target.remote_user,
               "unit": target.unit, "operation": target.operation,
               "known_hosts_sha256": known_hosts_sha256(target)}
    if target.target_kind == "netdata_config":
        binding.update({"target_kind": target.target_kind, "config_id": target.config_id,
                        "helper_path": target.helper_path,
                        "helper_sha256": target.helper_sha256})
    return binding


def apply_argv(target: Target) -> list[str]:
    """The FIXED privileged apply: `sudo -n /usr/bin/systemctl restart <unit>`. No shell,
    absolute path, operation is a server constant (v1: restart)."""
    return _ssh_base(target) + ["sudo", "-n", _SYSTEMCTL_ABS, target.operation, target.unit]


def config_inspect_argv(target: Target) -> list[str]:
    """Read the fixed typed config projection through the pinned root helper."""
    return _ssh_base(target) + ["sudo", "-n", target.helper_path, "inspect"]


def config_apply_argv(target: Target, *, change_run_id: str,
                      before_sha256: str, after_sha256: str) -> list[str]:
    """Apply the one fixed config mutation. No path/key/value is caller-controlled."""
    return _ssh_base(target) + [
        "sudo", "-n", target.helper_path, "apply",
        "--run-id", change_run_id,
        "--before-sha256", before_sha256,
        "--after-sha256", after_sha256,
    ]


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
