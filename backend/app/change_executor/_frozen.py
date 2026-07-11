"""FROZEN executor dependencies — deliberately self-contained (stdlib only).

The privileged executor must run from executor-owned code that the main Elira user
(under which `run_bash`/the model execute) cannot modify. If the executor imported from
the main `app/` tree, a main-user write could rewrite the drift check, the SSH argv, or
the systemctl parser. So the small pieces the executor needs — a SQLite connector, a
console decoder, and the systemctl-show parser + unit-name validator — are COPIED here
and the package imports nothing from the rest of `app/`. Deploy this package as
executor-owned code/venv, ACL-denied to the main user.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path

_LOG = logging.getLogger(__name__)

# ── SQLite connector (frozen copy of infrastructure.db.connection.connect_sqlite) ──
_DEFAULT_TIMEOUT = 5.0


def connect_sqlite(db_path: str | Path, *, timeout: float = _DEFAULT_TIMEOUT,
                   row_factory=sqlite3.Row) -> sqlite3.Connection:
    path = Path(db_path)
    if not str(path).strip():
        raise ValueError("db_path is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout)
    if row_factory is not None:
        conn.row_factory = row_factory
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        conn.close()
        raise
    return conn


# ── console decoder (frozen copy of infrastructure.encoding.decode_console) ──
def decode_console(data: bytes | str | None) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    encodings = ("oem", "cp866", "cp1251") if os.name == "nt" else ("cp1251",)
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace")


# ── systemctl-show parser + unit validation (frozen from it_ops.systemd_inspect) ──
SHOW_PROPERTIES: tuple[str, ...] = (
    "Id", "LoadState", "ActiveState", "SubState", "UnitFileState",
    "MainPID", "ExecMainStatus", "NRestarts", "FragmentPath",
)
_PROP_TO_FIELD: dict[str, str] = {
    "Id": "id", "LoadState": "load_state", "ActiveState": "active_state",
    "SubState": "sub_state", "UnitFileState": "unit_file_state", "MainPID": "main_pid",
    "ExecMainStatus": "exec_main_status", "NRestarts": "n_restarts", "FragmentPath": "fragment_path",
}
_NUMERIC_FIELDS = frozenset({"main_pid", "exec_main_status", "n_restarts"})
_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.@\-]{0,126}\.service$")


def unit_name_ok(unit: str) -> bool:
    u = str(unit or "").strip()
    if not u or len(u) > 128:
        return False
    if "/" in u or ".." in u:
        return False
    return bool(_UNIT_RE.match(u))


def show_remote_command(unit: str) -> list[str]:
    return ["systemctl", "show", "-p", ",".join(SHOW_PROPERTIES), unit]


def parse_show_output(text: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        field = _PROP_TO_FIELD.get(key.strip())
        if field is None:
            continue
        val = val.strip()
        if field in _NUMERIC_FIELDS:
            try:
                out[field] = int(val)
            except ValueError:
                out[field] = None
        else:
            out[field] = val
    return out
