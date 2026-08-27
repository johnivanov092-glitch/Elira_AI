"""Durable SSH-only MikroTik registry over the existing IT Ops store.

MikroTik is intentionally not an MCP integration. RouterOS 6 and 7 use the
same non-interactive OpenSSH provider, with a configured key/ssh-agent. Legacy
MikroMCP launch artifacts are removed during every registry synchronization.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from app.application.tool_providers import mcp_runtime
from app.core.data_files import data_subdir
from app.infrastructure.it_ops import store


LEGACY_MCP_SERVER_ID = "mikrotik"
_ROUTER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_ROS_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
_ROS_OUTPUT_VERSION_RE = re.compile(r"(?im)^\s*version:\s*(\d+\.\d+(?:\.\d+)?)")
_VENDOR = "mikrotik"


def default_registry_path() -> Path:
    """Legacy MikroMCP YAML path retained only so cleanup is deterministic."""
    return data_subdir("mikromcp") / "routers.yaml"


def _normalize_host(value: str) -> tuple[str, int | None]:
    raw = str(value or "").strip()
    if not raw or any(char.isspace() for char in raw):
        raise ValueError("MikroTik host is required and must not contain whitespace")
    host = raw
    port: int | None = None
    if "://" in raw:
        parsed = urlsplit(raw)
        if parsed.scheme != "ssh" or parsed.username or parsed.password:
            raise ValueError("MikroTik endpoint must be an ssh:// host without credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("MikroTik endpoint must contain only a host and optional port")
        host = parsed.hostname or ""
        port = parsed.port
    elif raw.count(":") == 1:
        candidate_host, candidate_port = raw.rsplit(":", 1)
        if candidate_port.isdigit():
            host, port = candidate_host, int(candidate_port)
    host = host.strip().rstrip(".").lower()
    if not host or any(char in host for char in "/\\@?#"):
        raise ValueError("MikroTik host is invalid")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or any(
            not _HOST_LABEL_RE.fullmatch(label) for label in host.split(".")
        ):
            raise ValueError("MikroTik host must be an IP address or DNS name")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("MikroTik SSH port must be between 1 and 65535")
    return host, port


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")[:96]


def _default_router_id(host: str, label: str) -> str:
    candidate = _slug(label) or _slug(host)
    return candidate or f"router-{hashlib.sha256(host.encode('utf-8')).hexdigest()[:12]}"


def _validate_router_id(value: str) -> str:
    router_id = str(value or "").strip()
    if not _ROUTER_ID_RE.fullmatch(router_id):
        raise ValueError("router_id contains unsupported characters")
    return router_id


def _validate_asset_id(value: str) -> str:
    asset_id = str(value or "").strip()
    if not _ASSET_ID_RE.fullmatch(asset_id):
        raise ValueError("asset_id contains unsupported characters")
    return asset_id


def _internal_routers(*, include_legacy_mcp: bool = True) -> list[dict[str, Any]]:
    store.init_db()
    assets = {item["asset_id"]: item for item in store.list_assets()}
    routers: list[dict[str, Any]] = []
    for profile in store.list_connection_profiles():
        meta = profile.get("os_platform_meta")
        if not isinstance(meta, dict) or str(meta.get("vendor", "")).lower() != _VENDOR:
            continue
        transport = str(profile.get("transport") or "")
        if transport != "ssh" and not (include_legacy_mcp and transport == "mcp"):
            continue
        asset = assets.get(profile.get("asset_id"))
        if not asset or asset.get("kind") != "network_device":
            continue
        routers.append({
            "asset_id": asset["asset_id"],
            "profile_id": profile["profile_id"],
            "label": asset.get("label") or meta.get("router_id") or asset["asset_id"],
            "host": str(meta.get("host") or ""),
            "router_id": str(meta.get("router_id") or ""),
            "port": int(meta.get("port") or 22),
            "ros_version": str(meta.get("ros_version") or ""),
            "runtime": "typed_ssh",
            "transport": transport,
            "user": str(profile.get("user") or ""),
            "ssh_alias": str(profile.get("ssh_alias") or ""),
            "identity_file": str(meta.get("identity_file") or ""),
            "auth_ref": str(profile.get("auth_ref") or ""),
            "tags": list(asset.get("tags") or []),
            "lifecycle_state": asset.get("lifecycle_state") or "draft",
            "last_health": profile.get("last_health") or {},
        })
    return sorted(routers, key=lambda item: (item["router_id"], item["asset_id"]))


def _public_router(router: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in router.items() if key not in {"auth_ref", "identity_file"}
    } | {
        "has_legacy_secret_ref": bool(router.get("auth_ref")),
        "has_identity_file": bool(router.get("identity_file")),
    }


def list_routers() -> list[dict[str, Any]]:
    return [_public_router(item) for item in _internal_routers()]


def get_router(router_id: str) -> dict[str, Any] | None:
    router = _find_router(router_id=str(router_id or "").strip())
    return dict(router) if router is not None else None


def _find_router(*, host: str = "", router_id: str = "", asset_id: str = "") -> dict[str, Any] | None:
    normalized_host = _normalize_host(host)[0] if host else ""
    for router in _internal_routers():
        if asset_id and router["asset_id"] == asset_id:
            return router
        if router_id and router["router_id"] == router_id:
            return router
        if normalized_host and router["host"] == normalized_host:
            return router
    return None


def prepare_identity(*, host: str, label: str = "", asset_id: str = "", router_id: str = "") -> dict[str, str]:
    normalized_host, _ = _normalize_host(host)
    existing = _find_router(host=normalized_host, router_id=router_id, asset_id=asset_id)
    resolved_router_id = _validate_router_id(
        router_id or str((existing or {}).get("router_id") or "") or _default_router_id(normalized_host, label)
    )
    resolved_asset_id = _validate_asset_id(
        asset_id or str((existing or {}).get("asset_id") or "") or f"mikrotik:{resolved_router_id}"
    )
    return {
        "host": normalized_host,
        "router_id": resolved_router_id,
        "asset_id": resolved_asset_id,
        "profile_id": f"{resolved_asset_id}:ssh",
    }


def existing_auth_ref(*, host: str, router_id: str = "", asset_id: str = "") -> str:
    existing = _find_router(host=host, router_id=router_id, asset_id=asset_id)
    return str((existing or {}).get("auth_ref") or "")


def ssh_target(router: dict[str, Any]) -> str:
    alias = str(router.get("ssh_alias") or "").strip()
    if alias:
        return alias
    user = str(router.get("user") or "").strip()
    host = str(router.get("host") or "").strip()
    return f"{user}@{host}" if user else host


def ssh_compatibility_args(target: str) -> list[str]:
    """Fixed per-router OpenSSH identity and RouterOS 6 compatibility args."""
    token = str(target or "").strip().lower()
    target_host = token.rsplit("@", 1)[-1].strip("[]")
    for router in _internal_routers(include_legacy_mcp=False):
        alias = str(router.get("ssh_alias") or "").strip().lower()
        host = str(router.get("host") or "").strip().lower()
        version = str(router.get("ros_version") or "").strip()
        if target_host == host or (alias and token == alias):
            args: list[str] = []
            identity_file = str(router.get("identity_file") or "").strip()
            if identity_file:
                args.extend(["-o", "IdentitiesOnly=yes", "-i", identity_file])
            if version.startswith("7."):
                return args
            args.extend([
                "-o", "MACs=+hmac-sha1",
                "-o", "HostKeyAlgorithms=+ssh-rsa",
                "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
            ])
            return args
    return []


def discover_routeros_version(
    *,
    host: str,
    user: str,
    ssh_alias: str = "",
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Best-effort version discovery through the canonical typed SSH provider."""
    if runner is None:
        from app.application.tool_providers.ssh_provider import tool_ssh_run

        runner = tool_ssh_run
    normalized_host, _ = _normalize_host(host)
    target = str(ssh_alias or "").strip() or f"{str(user).strip()}@{normalized_host}"
    result = runner(
        host=target,
        command="/system resource print without-paging",
        timeout=10,
    )
    if not result.get("ok"):
        return {"ok": False, "error": "ssh_probe_failed"}
    match = _ROS_OUTPUT_VERSION_RE.search(str(result.get("text") or ""))
    if not match:
        return {"ok": False, "error": "routeros_version_not_found"}
    return {"ok": True, "ros_version": match.group(1)}


def sync_runtime(*, registry_path: Path | None = None) -> dict[str, Any]:
    """Remove the retired MikroMCP server and its generated YAML artifact."""
    mcp_runtime.stop_server(LEGACY_MCP_SERVER_ID)
    current = [
        item for item in mcp_runtime.list_servers()
        if str(item.get("id") or "") != LEGACY_MCP_SERVER_ID
    ]
    saved = mcp_runtime.save_servers(current)
    legacy_path = (registry_path or default_registry_path()).resolve()
    removed_registry = legacy_path.is_file()
    if removed_registry:
        legacy_path.unlink()
    return {
        "ok": True,
        "transport": "ssh",
        "legacy_mcp_removed": not any(
            str(item.get("id") or "") == LEGACY_MCP_SERVER_ID for item in saved
        ),
        "legacy_registry_removed": removed_registry,
    }


def upsert_router(
    *,
    host: str,
    user: str,
    label: str = "",
    asset_id: str = "",
    router_id: str = "",
    ssh_alias: str = "",
    identity_file: str = "",
    port: int | None = None,
    ros_version: str = "",
    auth_ref: str = "",
    tags: list[str] | None = None,
    registry_path: Path | None = None,
    verify_connection: bool = True,
    **_legacy_mcp: Any,
) -> dict[str, Any]:
    normalized_host, endpoint_port = _normalize_host(host)
    identity = prepare_identity(
        host=normalized_host,
        label=label,
        asset_id=asset_id,
        router_id=router_id,
    )
    existing = _find_router(asset_id=identity["asset_id"])
    normalized_user = str(user or "").strip()
    if not normalized_user:
        raise ValueError("MikroTik username is required")
    normalized_identity_file = str(identity_file or "").strip()
    if normalized_identity_file:
        identity_path = Path(normalized_identity_file).expanduser().resolve()
        if not identity_path.is_file():
            raise ValueError("MikroTik identity_file must reference an existing file")
        normalized_identity_file = str(identity_path)
    resolved_port = int(port or endpoint_port or 22)
    if not 1 <= resolved_port <= 65535:
        raise ValueError("MikroTik SSH port must be between 1 and 65535")
    normalized_version = str(ros_version or "").strip()
    discovery: dict[str, Any] = {"ok": False, "error": "not_probed"}
    if normalized_version and not _ROS_VERSION_RE.fullmatch(normalized_version):
        raise ValueError("MikroTik ros_version must look like 6.49.19 or 7.16.2")
    normalized_tags = list(dict.fromkeys([
        "mikrotik", "routeros", "ssh",
        *[str(item).strip() for item in (tags or []) if str(item).strip()],
    ]))
    meta = {
        "vendor": _VENDOR,
        "runtime": "typed_ssh",
        "router_id": identity["router_id"],
        "host": normalized_host,
        "port": resolved_port,
        "ros_version": normalized_version,
        "auth_mode": "openssh_key_or_agent",
        "identity_file": normalized_identity_file,
    }
    store.init_db()
    store.upsert_asset(
        asset_id=identity["asset_id"],
        label=str(label or (existing or {}).get("label") or identity["router_id"]),
        kind="network_device",
        endpoint=f"ssh://{normalized_host}:{resolved_port}",
        tags=normalized_tags,
        owner_scope="global",
        lifecycle_state="enabled",
    )
    store.put_connection_profile(
        profile_id=identity["profile_id"],
        asset_id=identity["asset_id"],
        transport="ssh",
        user=normalized_user,
        auth_ref=str(auth_ref or (existing or {}).get("auth_ref") or "").strip() or None,
        ssh_alias=str(ssh_alias or "").strip(),
        os_platform_meta=meta,
        last_health={
            "ok": bool(discovery.get("ok")),
            "error": str(discovery.get("error") or ""),
        },
    )
    legacy_profile_id = str((existing or {}).get("profile_id") or "")
    if legacy_profile_id and legacy_profile_id != identity["profile_id"]:
        store.delete_connection_profile(legacy_profile_id)
    sync = sync_runtime(registry_path=registry_path)
    if verify_connection:
        discovery = discover_routeros_version(
            host=normalized_host,
            user=normalized_user,
            ssh_alias=ssh_alias,
        )
        if discovery.get("ok"):
            normalized_version = str(discovery["ros_version"])
            meta["ros_version"] = normalized_version
        store.put_connection_profile(
            profile_id=identity["profile_id"],
            asset_id=identity["asset_id"],
            transport="ssh",
            user=normalized_user,
            auth_ref=str(auth_ref or (existing or {}).get("auth_ref") or "").strip() or None,
            ssh_alias=str(ssh_alias or "").strip(),
            os_platform_meta=meta,
            last_health={
                "ok": bool(discovery.get("ok")),
                "error": str(discovery.get("error") or ""),
            },
        )
    saved = _find_router(asset_id=identity["asset_id"])
    if saved is None:
        raise RuntimeError("MikroTik SSH asset was written but could not be read back")
    return {
        "ok": True,
        "router": _public_router(saved),
        "connection_verified": bool(discovery.get("ok")),
        "connection_error": str(discovery.get("error") or ""),
        "sync": sync,
    }


def remove_router(
    *,
    asset_id: str = "",
    router_id: str = "",
    host: str = "",
    registry_path: Path | None = None,
) -> dict[str, Any]:
    if not any((asset_id, router_id, host)):
        raise ValueError("asset_id, router_id, or host is required")
    existing = _find_router(asset_id=asset_id, router_id=router_id, host=host)
    if existing is None:
        return {"ok": True, "deleted": False, "sync": sync_runtime(registry_path=registry_path)}
    deleted = store.delete_asset(existing["asset_id"])
    return {
        "ok": True,
        "deleted": deleted,
        "asset_id": existing["asset_id"],
        "router_id": existing["router_id"],
        "sync": sync_runtime(registry_path=registry_path),
    }
