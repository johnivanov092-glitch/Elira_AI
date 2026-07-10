"""IT-Ops SSH vertical v1 — enrollment + verify for an EXISTING OpenSSH alias.

Hard scope (Phase 0):
  * v1 works only with an alias already configured in the user's OpenSSH. This
    module NEVER creates or modifies %USERPROFILE%\\.ssh\\config, known_hosts,
    keys, or ssh-agent, and never touches Credential Manager or a secret value.
  * `resolve_alias` reads the effective host/port/user/key path via `ssh -G`
    (no network).
  * `observe_fingerprint` runs `ssh-keyscan` — an OBSERVED fingerprint, NOT proof
    of identity. The UI must make the user compare it out-of-band.
  * `verify_alias` runs `ssh -o BatchMode=yes -o StrictHostKeyChecking=yes <alias>
    hostname`. No accept-new, no sshpass, no password, no temp files. Success is
    only possible if the user already trusts the host (known_hosts) and has a
    working key/agent; otherwise the profile stays unverified with a clear reason.
"""
from __future__ import annotations

import re
import subprocess

from app.infrastructure.encoding import decode_console

# An alias must be a plain OpenSSH Host token — NOT an ssh option. It must start
# with an alphanumeric (so a leading '-' can never turn it into -oProxyCommand=…,
# -F<config>, etc.) and contain only [A-Za-z0-9._-]. This blocks option injection
# and shell metacharacters in one rule.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SSH = "ssh"
_KEYSCAN = "ssh-keyscan"
_KEYGEN = "ssh-keygen"


class SshEnrollError(ValueError):
    """A malformed alias / input — rejected before any subprocess."""


def alias_ok(alias: str) -> bool:
    return isinstance(alias, str) and bool(_ALIAS_RE.match(alias.strip()))


def _require_alias(alias: str) -> str:
    a = (alias or "").strip()
    if not alias_ok(a):
        raise SshEnrollError(f"invalid ssh alias: {alias!r}")
    return a


def resolve_alias(alias: str) -> dict:
    """Effective config for `alias` via `ssh -G` (NO network, NO connection).
    Returns {ok, hostname, port, user, identity_files, raw?} or {ok:False, error}."""
    a = _require_alias(alias)
    try:
        proc = subprocess.run([_SSH, "-G", a], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"ssh -G failed: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "error": f"ssh -G exit {proc.returncode}: "
                f"{decode_console(proc.stderr).strip()[:200]}"}
    hostname = port = user = ""
    identities: list[str] = []
    for line in decode_console(proc.stdout).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key, val = parts[0].lower(), parts[1].strip()
        if key == "hostname":
            hostname = val
        elif key == "port":
            port = val
        elif key == "user":
            user = val
        elif key == "identityfile":
            identities.append(val)
    return {"ok": True, "hostname": hostname, "port": port or "22", "user": user,
            "identity_files": identities}


def observe_fingerprint(hostname: str, port: str = "22") -> dict:
    """OBSERVED host-key fingerprint via ssh-keyscan | ssh-keygen -lf - (stdin, no
    temp file). ADVISORY only — the caller/UI must state it is not proof of identity
    and require an out-of-band comparison. Returns {ok, fingerprints:[...], note}."""
    host = (hostname or "").strip()
    if not host or not re.match(r"^[A-Za-z0-9._:-]+$", host):   # IP / FQDN chars only
        return {"ok": False, "error": "invalid hostname for keyscan", "fingerprints": []}
    p = (str(port) or "22").strip()
    if not p.isdigit():
        p = "22"
    try:
        scan = subprocess.run([_KEYSCAN, "-T", "5", "-p", p, host], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"ssh-keyscan failed: {exc}", "fingerprints": []}
    keys = scan.stdout or b""
    if not keys.strip():
        return {"ok": False, "error": "no host key observed (host unreachable or filtered)",
                "fingerprints": []}
    try:
        fp = subprocess.run([_KEYGEN, "-lf", "-"], input=keys, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"ssh-keygen failed: {exc}", "fingerprints": []}
    lines = [ln.strip() for ln in decode_console(fp.stdout).splitlines() if ln.strip()]
    return {"ok": bool(lines), "fingerprints": lines,
            "note": "OBSERVED fingerprint only — NOT proof of identity. Compare it against a "
                    "trusted out-of-band source before confirming."}


def verify_alias(alias: str) -> dict:
    """Verify SSH to `alias` with STRICT host-key checking (no accept-new): run a
    trivial `hostname`. Returns {ok, output?, reason?, exit}. A host not already in
    the user's known_hosts, or missing key/agent, yields ok=False with a clear
    reason — nothing is modified."""
    a = _require_alias(alias)
    argv = [_SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes", a, "hostname"]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "connection timed out", "exit": None}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": f"ssh could not run: {exc}", "exit": None}
    out = decode_console(proc.stdout).strip()
    err = decode_console(proc.stderr).strip()
    if proc.returncode == 0:
        return {"ok": True, "output": out, "exit": 0}
    return {"ok": False, "reason": _interpret(err, proc.returncode),
            "exit": proc.returncode, "stderr": err[:300]}


def _interpret(stderr: str, code: int) -> str:
    low = stderr.lower()
    if "host key verification failed" in low or "no matching host key" in low:
        return ("host key not trusted — the host is not in your known_hosts (or its key "
                "changed). v1 does not modify known_hosts; add/verify it yourself first.")
    if "permission denied" in low:
        return "key authentication failed — no working key/agent for this alias."
    if "could not resolve hostname" in low:
        return "hostname could not be resolved."
    if "connection refused" in low or "connection timed out" in low or "no route" in low:
        return "host unreachable (connection refused/timed out)."
    return f"ssh failed (exit {code})"
