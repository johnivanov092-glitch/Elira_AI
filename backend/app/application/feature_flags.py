"""Deferred-track feature flags — persisted, env-overridable, OFF by default.

The deferred-track capabilities are gated:

  * D1 — remote (HTTP) MCP transport   → flag ``remote_mcp``     (env ``ELIRA_REMOTE_MCP``)
  * D3 — structured action envelopes    → flag ``action_envelopes`` (env ``ELIRA_ACTION_ENVELOPES``)

Originally each gate read its own ``os.getenv`` directly. That cannot be
toggled from the UI: an env var set in a running process does not survive a
backend restart, and writing to ``os.environ`` would create a second source
of truth that drifts from any persisted file.

This module is the single source of truth instead. State persists in
``data/feature_flags.json`` and a UI toggle just writes that file — no
restart needed (D3 is read on every turn; D1 on the next MCP-server start).

Resolution order in :func:`flag_enabled` keeps the original operator
contract intact:

  1. the matching **env var**, if set to a truthy value — an explicit
     operator override always wins (so prod deployments and the existing
     ``monkeypatch.setenv`` tests behave exactly as before);
  2. otherwise the **persisted file** value;
  3. otherwise **False** (off by default).

Leaf module: imports nothing from the agent loop / MCP runtime, so it stays
unit-testable in isolation and introduces no import cycle.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from app.core.data_files import data_file


logger = logging.getLogger(__name__)

# Same truthy set the original gates used.
_TRUTHY = frozenset({"1", "on", "true", "yes"})

# flag name → env var that overrides it.
_ENV_VAR: dict[str, str] = {
    "remote_mcp": "ELIRA_REMOTE_MCP",
    "action_envelopes": "ELIRA_ACTION_ENVELOPES",
    # Living Persona step C — master switch for Elira's proactivity (default OFF).
    "proactive": "ELIRA_PROACTIVE",
    # R1 — the verifier catalog as a runtime ASSIST (unsupported-labels, closure-hint
    # notes, classification-drift log). NOT a classifier; bit-identical when off.
    "catalog_assist": "ELIRA_CATALOG_ASSIST",
}

# Canonical flag set + defaults (all OFF). The file is normalised to exactly
# these keys on read, so an unknown/missing key can never leak through.
_DEFAULTS: dict[str, bool] = {name: False for name in _ENV_VAR}

CONFIG_PATH: Path = data_file("feature_flags.json")
_LOCK = threading.Lock()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def _read_file() -> dict[str, bool]:
    """Persisted flags, normalised to the canonical key set (all default OFF).

    A missing / unreadable / malformed file is treated as "all off" rather
    than raising — the gates must never fail closed-then-crash.
    """
    flags = dict(_DEFAULTS)
    if not CONFIG_PATH.exists():
        return flags
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("feature_flags: failed to read %s: %s — treating as all off", CONFIG_PATH, exc)
        return flags
    if isinstance(raw, dict):
        for name in _DEFAULTS:
            flags[name] = bool(raw.get(name, False))
    return flags


def _write_file(flags: dict[str, bool]) -> None:
    """Atomically persist the canonical flag set (temp file + os.replace)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: bool(flags.get(name, False)) for name in _DEFAULTS}
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def flag_enabled(name: str) -> bool:
    """True if deferred-track flag *name* is on. Env override → file → False."""
    env_var = _ENV_VAR.get(name)
    if env_var is not None:
        raw = os.getenv(env_var)
        if raw is not None and raw.strip() != "":
            # An explicit env value (truthy or falsy) is an operator override.
            return _truthy(raw)
    with _LOCK:
        return _read_file().get(name, False)


def get_flags() -> dict[str, bool]:
    """Effective state of every flag (env override applied), for the UI."""
    with _LOCK:
        file_flags = _read_file()
    result: dict[str, bool] = {}
    for name in _DEFAULTS:
        env_var = _ENV_VAR[name]
        raw = os.getenv(env_var)
        if raw is not None and raw.strip() != "":
            result[name] = _truthy(raw)
        else:
            result[name] = file_flags.get(name, False)
    return result


def set_flag(name: str, value: bool) -> dict[str, bool]:
    """Persist one flag to the file and return the new effective state.

    Writes only the file; never mutates os.environ. If an env override is
    set for this flag it keeps winning in :func:`get_flags` — surfaced to the
    UI so the operator can see the file write didn't take effect.
    """
    if name not in _DEFAULTS:
        raise KeyError(name)
    with _LOCK:
        flags = _read_file()
        flags[name] = bool(value)
        _write_file(flags)
    return get_flags()
