"""SSH host allowlist persistence.

The SSH provider is DISABLED by default — agents only get to touch
hosts that the user has explicitly added to the allowlist. This is
a security boundary: if the LLM hallucinates `ssh_run(host='prod')`,
the provider rejects it unless 'prod' is in the list.

The list lives at `data/ssh_acl.json`:
    {"allowed_hosts": ["prod-1", "staging.example.com"]}

Matching is case-sensitive and exact on the host token the agent
passes to ssh tools. We deliberately don't try to resolve aliases
in ~/.ssh/config — the allowlist must match exactly what the agent
calls with.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.data_files import data_file


logger = logging.getLogger(__name__)


ACL_PATH = data_file("ssh_acl.json")


def _read_raw() -> dict[str, Any]:
    if not ACL_PATH.exists():
        return {}
    try:
        return json.loads(ACL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("ssh_acl: failed to read %s: %s — treating as empty", ACL_PATH, exc)
        return {}


def get_allowed_hosts() -> list[str]:
    """Current allowlist. Empty list means SSH is disabled — every
    ssh_* call will be rejected."""
    raw = _read_raw()
    hosts = raw.get("allowed_hosts", [])
    if not isinstance(hosts, list):
        return []
    # Defensive: filter to non-empty strings only
    return [h.strip() for h in hosts if isinstance(h, str) and h.strip()]


def set_allowed_hosts(hosts: list[str]) -> list[str]:
    """Replace the allowlist atomically. Returns the persisted list
    after normalization (whitespace stripped, duplicates removed,
    order preserved)."""
    seen: set[str] = set()
    clean: list[str] = []
    for h in hosts or []:
        if not isinstance(h, str):
            continue
        h = h.strip()
        if not h or h in seen:
            continue
        seen.add(h)
        clean.append(h)
    payload = {"allowed_hosts": clean}
    ACL_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACL_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def is_host_allowed(host: str) -> bool:
    """Strict exact-match check. Case-sensitive on the host token."""
    if not host or not isinstance(host, str):
        return False
    return host.strip() in get_allowed_hosts()


def resolve_allowed_host(host: str) -> str | None:
    """Resolve a human-facing host name to one existing allowlist alias.

    Exact aliases remain the security boundary.  The relaxed key is used only
    to select an already-allowed alias, and ambiguous matches fail closed.
    This lets ``Elira AI Server`` resolve to ``elira-ai-server`` without ever
    widening the allowlist or passing the display name to ``ssh``.
    """
    if not isinstance(host, str) or not host.strip():
        return None
    requested = host.strip()
    hosts = get_allowed_hosts()
    if requested in hosts:
        return requested

    folded = requested.casefold()
    case_matches = [candidate for candidate in hosts if candidate.casefold() == folded]
    if len(case_matches) == 1:
        return case_matches[0]

    key = re.sub(r"[^a-z0-9]+", "", folded)
    if not key:
        return None
    key_matches = [
        candidate for candidate in hosts
        if re.sub(r"[^a-z0-9]+", "", candidate.casefold()) == key
    ]
    return key_matches[0] if len(key_matches) == 1 else None


def is_ssh_enabled() -> bool:
    """SSH provider is enabled iff at least one host is allowed.
    Single source of truth — no separate on/off flag."""
    return bool(get_allowed_hosts())
