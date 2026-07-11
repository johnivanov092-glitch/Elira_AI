"""Phase 4a slice 1 — read-only systemd SERVICE inspect helpers.

Scope (deliberately narrow): a SINGLE `systemctl show` for a FIXED property set on one
human-selected `.service` unit, over the SSH alias of an enabled Linux asset. The output
is parsed into a small typed projection — never raw stdout — and one evidence row is
written.

Explicitly NOT in v1 (a later, separate read-only slice): the unit-file CONTENT, the
`systemctl status` text, and `journalctl` logs — any of which can carry Environment=,
tokens, or sensitive log lines. And NOTHING that changes the host: no start / stop /
restart / enable / disable / edit.
"""
from __future__ import annotations

import re

# The ONLY systemctl show properties collected in v1. All are non-secret metadata: unit
# identity, load/active/sub/unit-file state, main pid, last exit status, restart count,
# and the unit-file PATH (a path, not its content).
SHOW_PROPERTIES: tuple[str, ...] = (
    "Id", "LoadState", "ActiveState", "SubState", "UnitFileState",
    "MainPID", "ExecMainStatus", "NRestarts", "FragmentPath",
)
INSPECT_TIMEOUT = 15   # one `systemctl show` is fast; ≤15s including SSH connect

# systemctl property → the snake_case field name persisted in evidence.
_PROP_TO_FIELD: dict[str, str] = {
    "Id": "id", "LoadState": "load_state", "ActiveState": "active_state",
    "SubState": "sub_state", "UnitFileState": "unit_file_state", "MainPID": "main_pid",
    "ExecMainStatus": "exec_main_status", "NRestarts": "n_restarts", "FragmentPath": "fragment_path",
}
_NUMERIC_FIELDS = frozenset({"main_pid", "exec_main_status", "n_restarts"})

# Strict unit-name allowlist: a well-formed systemd .service unit token. Starts
# alphanumeric, then only [A-Za-z0-9:_.@-] (the characters real units use, e.g.
# getty@tty1.service, systemd-journald@x.service), and MUST end in `.service`. No shell
# metacharacters, no whitespace, no path characters — the name is later placed in a fixed
# `systemctl show` argv, so a strict allowlist is the injection boundary.
_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.@\-]{0,126}\.service$")


def unit_name_ok(unit: str) -> bool:
    """True only for a strictly well-formed `.service` unit name (v1 inspects services
    only). Rejects empty, over-long (>128), path-like (`/` or `..`), and anything with a
    character outside the allowlist."""
    u = str(unit or "").strip()
    if not u or len(u) > 128:
        return False
    if "/" in u or ".." in u:            # no paths / traversal (belt-and-suspenders)
        return False
    return bool(_UNIT_RE.match(u))


def show_remote_command(unit: str) -> list[str]:
    """The FIXED remote argv (no shell): `systemctl show -p <fixed props> <unit>`. The
    caller must have already validated `unit` with unit_name_ok."""
    return ["systemctl", "show", "-p", ",".join(SHOW_PROPERTIES), unit]


def parse_show_output(text: str) -> dict[str, object]:
    """Parse `systemctl show` KEY=VALUE lines into the TYPED projection: only the known
    properties survive (unknown keys dropped), numeric fields are coerced to int, and the
    rest are strings. Never returns raw stdout — the caller persists only this dict."""
    out: dict[str, object] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        field = _PROP_TO_FIELD.get(key.strip())
        if field is None:
            continue                     # anything outside the fixed set is discarded
        val = val.strip()
        if field in _NUMERIC_FIELDS:
            try:
                out[field] = int(val)
            except ValueError:
                out[field] = None        # keep the field present, but not a bogus int
        else:
            out[field] = val
    return out
