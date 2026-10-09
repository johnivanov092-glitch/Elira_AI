"""Phase 5 slice 1: typed, read-only inspection of named configuration targets.

The caller supplies only a server-owned ``config_id``. Paths, formats, commands and
safe value projections live in this module; no path or config key comes from the model.
Raw configuration text is never returned or persisted because configuration files may
contain credentials even when the first admitted target currently does not.
"""
from __future__ import annotations

import configparser
import hashlib
from dataclasses import dataclass
from typing import Any


MAX_CONFIG_BYTES = 256 * 1024
INSPECT_TIMEOUT = 15


class ConfigInspectError(ValueError):
    """A named target is unknown or its content cannot be safely projected."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ConfigSpec:
    config_id: str
    path: str
    format: str


_CONFIGS: dict[str, ConfigSpec] = {
    "netdata-main": ConfigSpec(
        config_id="netdata-main",
        path="/etc/netdata/netdata.conf",
        format="netdata-ini",
    ),
}


def resolve_config(config_id: str) -> ConfigSpec:
    cid = str(config_id or "").strip()
    spec = _CONFIGS.get(cid)
    if spec is None:
        raise ConfigInspectError("unknown_config")
    return spec


def read_remote_command(spec: ConfigSpec) -> list[str]:
    """Fixed bounded read-only argv for a server-owned absolute path.

    Read at most one byte beyond the accepted size so the local SSH process never
    buffers an unbounded remote file before ``inspect_bytes`` rejects it.
    """
    return ["/usr/bin/head", "-c", str(MAX_CONFIG_BYTES + 1), "--", spec.path]


def inspect_bytes(spec: ConfigSpec, raw: bytes) -> dict[str, Any]:
    """Parse and project a config without exposing its raw text or unknown values.

    V1 admits one typed setting: ``[global] update every`` as an integer in [1, 60].
    All other keys and values are counted but discarded. This keeps future credentials
    out of evidence by construction, not by name-based redaction.
    """
    if not isinstance(raw, bytes):
        raise ConfigInspectError("invalid_content")
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigInspectError("config_too_large")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ConfigInspectError("config_not_utf8") from exc

    parser = configparser.ConfigParser(
        interpolation=None,
        strict=False,
        empty_lines_in_values=False,
    )
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ConfigInspectError("config_parse_failed") from exc

    safe_settings: dict[str, Any] = {}
    if parser.has_option("global", "update every"):
        value = parser.get("global", "update every", raw=True).strip()
        try:
            update_every = int(value)
        except ValueError as exc:
            raise ConfigInspectError("invalid_update_every") from exc
        if not 1 <= update_every <= 60:
            raise ConfigInspectError("invalid_update_every")
        safe_settings["global.update_every"] = update_every

    section_count = len(parser.sections())
    setting_count = sum(len(parser.items(section, raw=True)) for section in parser.sections())
    return {
        "config_id": spec.config_id,
        "path": spec.path,
        "format": spec.format,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "comment_only": setting_count == 0,
        "section_count": section_count,
        "setting_count": setting_count,
        "safe_settings": safe_settings,
    }
