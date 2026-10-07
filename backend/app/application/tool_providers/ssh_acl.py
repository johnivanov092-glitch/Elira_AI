"""Saved SSH host aliases for discovery and UI convenience.

The local workflow may connect to any host token. This file is a recent/favorite
list, not a permission boundary.

The compatibility file lives at `data/ssh_acl.json`:
    {"allowed_hosts": ["prod-1", "staging.example.com"]}

Saved values provide friendly alias matching only. They never decide whether
the local workflow may connect to a destination.
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
    """Current saved/favorite hosts. Empty means no favorites, not disabled SSH."""
    raw = _read_raw()
    hosts = raw.get("allowed_hosts", [])
    if not isinstance(hosts, list):
        return []
    # Defensive: filter to non-empty strings only
    return [h.strip() for h in hosts if isinstance(h, str) and h.strip()]


def resolve_allowed_host(host: str) -> str | None:
    """Resolve a human-facing host name to one existing saved alias.

    Saved aliases get friendly matching; otherwise the requested host is used
    directly.
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
        return requested
    key_matches = [
        candidate for candidate in hosts
        if re.sub(r"[^a-z0-9]+", "", candidate.casefold()) == key
    ]
    return key_matches[0] if len(key_matches) == 1 else requested


def is_ssh_enabled() -> bool:
    """SSH tools are always available to the local workflow."""
    return True
