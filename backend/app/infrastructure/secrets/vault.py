"""Portable application-owned encrypted secret vault.

The vault is a single versioned JSON envelope under ``ELIRA_DATA_DIR``. Secret
values and the random data key are encrypted with AES-256-GCM. The data key is
wrapped independently by a scrypt-derived passphrase key and by an optional
recovery key, so either credential can unlock the same stable ``secret_ref``
records. Windows Credential Manager is not used by the active backend.

Only this module owns the decrypted data key. Callers receive secret values only
from ``resolve`` inside runtime dispatch; lifecycle/status APIs never expose
plaintext. ``wincred`` is imported lazily by the explicit legacy migration path.
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.core.data_files import data_file
from app.domain import it_ops as _dom
from app.infrastructure.it_ops import store as _store


_FORMAT_NAME = "elira-portable-vault"
_FORMAT_VERSION = 1
_VAULT_FILENAME = "portable_vault.json"
_DATA_KEY_BYTES = 32
_NONCE_BYTES = 12
_SALT_BYTES = 16
_KDF_N = 1 << 15
_KDF_R = 8
_KDF_P = 1
_MAX_KDF_N = 1 << 20
_MAX_KDF_R = 32
_MAX_KDF_P = 16
_MAX_FILE_BYTES = 128 * 1024 * 1024
_RECOVERY_PREFIX = "elira-recovery-v1-"
_lock = threading.RLock()
_data_key: bytearray | None = None
_unlocked_vault_id = ""
_last_used_at: float | None = None
_idle_timer: threading.Timer | None = None
_idle_generation = 0
_VAULT_PATH_OVERRIDE: str | None = None


def _idle_timeout_seconds() -> float:
    """Server-owned vault idle timeout; not a Workflow run deadline."""
    try:
        value = float(os.getenv("ELIRA_VAULT_IDLE_TIMEOUT_SECONDS", "900"))
    except (TypeError, ValueError):
        value = 900.0
    return max(1.0, value)


class VaultError(RuntimeError):
    """Base class for portable vault failures."""


class VaultNotInitialized(VaultError):
    """The portable vault has not been created or restored yet."""


class VaultLocked(VaultError):
    """A secret operation requires an unlocked vault."""


class VaultAuthenticationError(VaultError):
    """The passphrase/recovery key did not unlock the vault."""


class VaultFormatError(VaultError):
    """The vault envelope is malformed, modified, or from an unknown version."""


class SecretUnavailable(VaultError):
    """The secret value is absent, revoked, incomplete, or unavailable."""


class VaultProvisioningError(VaultError):
    """A secret could not be fully provisioned; carries only its opaque ref."""

    def __init__(self, secret_ref: str, message: str):
        self.secret_ref = secret_ref
        super().__init__(f"{message} (secret_ref={secret_ref})")


def _vault_path() -> Path:
    if _VAULT_PATH_OVERRIDE:
        return Path(_VAULT_PATH_OVERRIDE).resolve()
    return data_file(_VAULT_FILENAME)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise VaultFormatError(f"vault field {field!r} must be non-empty base64")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise VaultFormatError(f"vault field {field!r} is not valid base64") from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _wrapper_aad(vault_id: str, wrapper: str) -> bytes:
    return _canonical({
        "format": _FORMAT_NAME,
        "version": _FORMAT_VERSION,
        "vault_id": vault_id,
        "wrapper": wrapper,
    })


def _record_aad(vault_id: str, secret_ref: str, kind: str) -> bytes:
    return _canonical({
        "format": _FORMAT_NAME,
        "version": _FORMAT_VERSION,
        "vault_id": vault_id,
        "secret_ref": secret_ref,
        "kind": kind,
    })


def _backup_component_aad(
    vault_id: str,
    component: str,
    *,
    version: int = 3,
) -> bytes:
    return _canonical({
        "format": "elira-portable-vault-backup",
        "version": version,
        "vault_id": vault_id,
        "component": component,
    })


def _portable_components() -> dict[str, tuple[str, Path]]:
    """Fixed restore allowlist; backup content can never choose a filesystem path."""
    return {
        "workflow_engine.db": ("sqlite", data_file("workflow_engine.db")),
        "integrations.db": ("sqlite", data_file("integrations.db")),
        "agent_registry.db": ("sqlite", data_file("agent_registry.db")),
        "tool_registry.db": ("sqlite", data_file("tool_registry.db")),
        # Curated facts and semantic/Project Corpus memory are encrypted in the
        # same user-triggered portable bundle. The run-scoped web Corpus remains
        # an expiring cache and is intentionally excluded.
        "smart_memory.db": ("sqlite", data_file("smart_memory.db")),
        "rag_memory.db": ("sqlite", data_file("rag_memory.db")),
        "mcp_servers.json": ("json", data_file("mcp_servers.json")),
        "lsp_servers.json": ("json", data_file("lsp_servers.json")),
        "ssh_acl.json": ("json", data_file("ssh_acl.json")),
    }


def _export_sqlite(path: Path) -> bytes:
    """Return a transactionally consistent SQLite image including WAL state."""
    with tempfile.TemporaryDirectory(prefix="elira-vault-backup-") as tmp_dir:
        snapshot = Path(tmp_dir) / "snapshot.sqlite3"
        source = sqlite3.connect(str(path))
        target = sqlite3.connect(str(snapshot))
        try:
            source.backup(target)
            target.commit()
        except sqlite3.Error as exc:
            raise VaultError(f"could not snapshot {path.name}: {exc}") from exc
        finally:
            target.close()
            source.close()
        return snapshot.read_bytes()


def _component_bytes(kind: str, path: Path) -> bytes:
    if kind == "sqlite":
        return _export_sqlite(path)
    raw = path.read_bytes()
    if kind == "json":
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise VaultFormatError(f"portable component {path.name} is invalid JSON") from exc
        return raw
    raise VaultFormatError(f"unsupported portable component kind: {kind}")


def _install_component(kind: str, path: Path, raw: bytes) -> None:
    """Validate and atomically install one fixed-path portable component."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.restore")
    previous = path.with_suffix(path.suffix + ".prev")
    try:
        with staging.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if kind == "json":
            json.loads(staging.read_text(encoding="utf-8"))
        elif kind == "sqlite":
            connection = sqlite3.connect(f"file:{staging.as_posix()}?mode=ro", uri=True)
            try:
                check = connection.execute("PRAGMA quick_check").fetchone()
                if not check or str(check[0]).lower() != "ok":
                    raise VaultFormatError(f"portable database {path.name} failed quick_check")
            finally:
                connection.close()
        else:
            raise VaultFormatError(f"unsupported portable component kind: {kind}")
        if path.exists():
            shutil.copyfile(path, previous)
        if kind == "sqlite":
            for suffix in ("-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)
        os.replace(staging, path)
        _fsync_directory(path.parent)
    except (OSError, UnicodeError, json.JSONDecodeError, sqlite3.Error) as exc:
        raise VaultError(f"could not restore {path.name}: {exc}") from exc
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_passphrase(passphrase: str) -> None:
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("vault passphrase must be a non-empty string")


def _kdf_config() -> dict[str, Any]:
    return {
        "name": "scrypt",
        "salt": _b64(secrets.token_bytes(_SALT_BYTES)),
        "length": _DATA_KEY_BYTES,
        "n": _KDF_N,
        "r": _KDF_R,
        "p": _KDF_P,
    }


def _derive_key(passphrase: str, config: dict[str, Any]) -> bytes:
    try:
        if config.get("name") != "scrypt" or int(config.get("length")) != _DATA_KEY_BYTES:
            raise VaultFormatError("unsupported vault KDF")
        n = int(config["n"])
        r = int(config["r"])
        p = int(config["p"])
        if n < 2 or n > _MAX_KDF_N or n & (n - 1):
            raise VaultFormatError("vault scrypt n is outside supported limits")
        if not 1 <= r <= _MAX_KDF_R or not 1 <= p <= _MAX_KDF_P:
            raise VaultFormatError("vault scrypt parameters are outside supported limits")
        salt = _unb64(config.get("salt"), "kdf.salt")
        if len(salt) != _SALT_BYTES:
            raise VaultFormatError("vault scrypt salt has invalid length")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, VaultFormatError):
            raise
        raise VaultFormatError("invalid vault KDF parameters") from exc
    return Scrypt(salt=salt, length=_DATA_KEY_BYTES, n=n, r=r, p=p).derive(
        passphrase.encode("utf-8")
    )


def _encrypt(key: bytes, plaintext: bytes, aad: bytes) -> dict[str, str]:
    nonce = secrets.token_bytes(_NONCE_BYTES)
    return {
        "nonce": _b64(nonce),
        "ciphertext": _b64(AESGCM(key).encrypt(nonce, plaintext, aad)),
    }


def _decrypt(key: bytes, payload: Any, aad: bytes, field: str) -> bytes:
    if not isinstance(payload, dict):
        raise VaultFormatError(f"vault field {field!r} must be an object")
    nonce = _unb64(payload.get("nonce"), f"{field}.nonce")
    ciphertext = _unb64(payload.get("ciphertext"), f"{field}.ciphertext")
    if len(nonce) != _NONCE_BYTES:
        raise VaultFormatError(f"vault field {field!r} has invalid nonce length")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise VaultAuthenticationError("vault credential is invalid or data was modified") from exc


def _validate_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise VaultFormatError("vault root must be an object")
    if document.get("format") != _FORMAT_NAME:
        raise VaultFormatError("unsupported vault format")
    if document.get("version") != _FORMAT_VERSION:
        raise VaultFormatError(f"unsupported vault version: {document.get('version')!r}")
    vault_id = document.get("vault_id")
    if not isinstance(vault_id, str) or not vault_id:
        raise VaultFormatError("vault_id is missing")
    if not isinstance(document.get("kdf"), dict):
        raise VaultFormatError("vault KDF metadata is missing")
    wrappers = document.get("wrappers")
    if not isinstance(wrappers, dict) or not isinstance(wrappers.get("passphrase"), dict):
        raise VaultFormatError("vault passphrase wrapper is missing")
    records = document.get("records")
    if not isinstance(records, dict):
        raise VaultFormatError("vault records must be an object")
    return document


def _read_document() -> dict[str, Any]:
    path = _vault_path()
    if not path.exists():
        raise VaultNotInitialized("portable vault is not initialized")
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise VaultFormatError("vault file exceeds supported size")
        return _validate_document(json.loads(path.read_text(encoding="utf-8")))
    except VaultError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VaultFormatError("portable vault file cannot be read") from exc


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _atomic_write(document: dict[str, Any]) -> None:
    path = _vault_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    previous_temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.prev.tmp")
    previous = path.with_suffix(path.suffix + ".prev")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        if path.exists():
            shutil.copyfile(path, previous_temp)
            # Windows rejects fsync on a read-only CRT descriptor with EBADF.
            # The copy is ours, so open it read/write solely to flush the bytes
            # before the atomic rename to ``.prev``.
            with previous_temp.open("rb+") as handle:
                os.fsync(handle.fileno())
            os.replace(previous_temp, previous)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise VaultError(f"portable vault write failed: {exc}") from exc
    finally:
        for candidate in (temp, previous_temp):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def _clear_data_key() -> None:
    global _data_key, _unlocked_vault_id, _last_used_at
    global _idle_timer, _idle_generation
    _idle_generation += 1
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None
    if _data_key is not None:
        for index in range(len(_data_key)):
            _data_key[index] = 0
    _data_key = None
    _unlocked_vault_id = ""
    _last_used_at = None


def _expire_idle_key(generation: int, vault_id: str) -> None:
    with _lock:
        if generation != _idle_generation or vault_id != _unlocked_vault_id:
            return
        _clear_data_key()


def _touch_data_key() -> None:
    global _last_used_at, _idle_timer, _idle_generation
    if _data_key is None or not _unlocked_vault_id:
        return
    _last_used_at = time.time()
    _idle_generation += 1
    generation = _idle_generation
    if _idle_timer is not None:
        _idle_timer.cancel()
    timer = threading.Timer(
        _idle_timeout_seconds(),
        _expire_idle_key,
        args=(generation, _unlocked_vault_id),
    )
    timer.daemon = True
    _idle_timer = timer
    timer.start()


def _set_data_key(key: bytes, vault_id: str) -> None:
    global _data_key, _unlocked_vault_id
    if len(key) != _DATA_KEY_BYTES:
        raise VaultFormatError("unwrapped vault data key has invalid length")
    _clear_data_key()
    _data_key = bytearray(key)
    _unlocked_vault_id = vault_id
    _touch_data_key()


def _require_key(document: dict[str, Any]) -> bytes:
    if _data_key is None or _unlocked_vault_id != document["vault_id"]:
        raise VaultLocked("portable vault is locked")
    _touch_data_key()
    return bytes(_data_key)


def _new_ref() -> str:
    return "sref_" + uuid.uuid4().hex


def _new_recovery_key() -> tuple[str, bytes]:
    raw = secrets.token_bytes(_DATA_KEY_BYTES)
    return _RECOVERY_PREFIX + _b64(raw), raw


def _parse_recovery_key(value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith(_RECOVERY_PREFIX):
        raise VaultAuthenticationError("invalid recovery key")
    raw = _unb64(value[len(_RECOVERY_PREFIX):], "recovery_key")
    if len(raw) != _DATA_KEY_BYTES:
        raise VaultAuthenticationError("invalid recovery key")
    return raw


def create(passphrase: str) -> dict[str, Any]:
    """Create and unlock a new vault; return its one-time recovery key."""
    _validate_passphrase(passphrase)
    with _lock:
        if _vault_path().exists():
            raise VaultError("portable vault already exists")
        vault_id = "vault_" + uuid.uuid4().hex
        data_key = secrets.token_bytes(_DATA_KEY_BYTES)
        recovery_text, recovery_raw = _new_recovery_key()
        kdf = _kdf_config()
        passphrase_key = _derive_key(passphrase, kdf)
        now = time.time()
        document = {
            "format": _FORMAT_NAME,
            "version": _FORMAT_VERSION,
            "vault_id": vault_id,
            "created_at": now,
            "updated_at": now,
            "kdf": kdf,
            "wrappers": {
                "passphrase": _encrypt(
                    passphrase_key,
                    data_key,
                    _wrapper_aad(vault_id, "passphrase"),
                ),
                "recovery": _encrypt(
                    recovery_raw,
                    data_key,
                    _wrapper_aad(vault_id, "recovery"),
                ),
            },
            "records": {},
        }
        _atomic_write(document)
        _set_data_key(data_key, vault_id)
        return {**status(), "recovery_key": recovery_text}


def unlock(*, passphrase: str | None = None, recovery_key: str | None = None) -> dict[str, Any]:
    """Unlock using exactly one credential. Nothing is persisted in plaintext."""
    if (passphrase is None) == (recovery_key is None):
        raise ValueError("provide exactly one of passphrase or recovery_key")
    with _lock:
        document = _read_document()
        vault_id = document["vault_id"]
        if passphrase is not None:
            _validate_passphrase(passphrase)
            wrapper_key = _derive_key(passphrase, document["kdf"])
            wrapper_name = "passphrase"
        else:
            wrapper_key = _parse_recovery_key(str(recovery_key))
            wrapper_name = "recovery"
        wrapper = document["wrappers"].get(wrapper_name)
        if not isinstance(wrapper, dict):
            raise VaultFormatError(f"vault {wrapper_name} wrapper is missing")
        data_key = _decrypt(
            wrapper_key,
            wrapper,
            _wrapper_aad(vault_id, wrapper_name),
            f"wrappers.{wrapper_name}",
        )
        _apply_pending_restore(document, data_key)
        _set_data_key(data_key, vault_id)
        return status()


def lock() -> dict[str, Any]:
    with _lock:
        _clear_data_key()
        return status()


def status() -> dict[str, Any]:
    """Return lifecycle state and record counts; never key/value material."""
    with _lock:
        path = _vault_path()
        if not path.exists():
            return {
                "initialized": False,
                "locked": True,
                "format_version": _FORMAT_VERSION,
                "record_count": 0,
                "last_used_at": None,
                "idle_timeout_seconds": _idle_timeout_seconds(),
            }
        document = _read_document()
        unlocked = _data_key is not None and _unlocked_vault_id == document["vault_id"]
        return {
            "initialized": True,
            "locked": not unlocked,
            "format_version": document["version"],
            "vault_id": document["vault_id"],
            "record_count": len(document["records"]),
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
            "last_used_at": _last_used_at if unlocked else None,
            "idle_timeout_seconds": _idle_timeout_seconds(),
            "recovery_enabled": isinstance(document["wrappers"].get("recovery"), dict),
            "pending_state_restore": isinstance(document.get("pending_restore"), dict),
            "pending_metadata_restore": isinstance(document.get("pending_restore"), dict),
        }


def _best_effort_mark(secret_ref: str, lifecycle: str) -> None:
    try:
        _store.set_secret_lifecycle(secret_ref, lifecycle)
    except _store.StoreUnavailable:
        pass


def _delete_record(secret_ref: str) -> bool:
    document = _read_document()
    _require_key(document)
    if secret_ref not in document["records"]:
        return False
    document["records"].pop(secret_ref, None)
    document["updated_at"] = time.time()
    _atomic_write(document)
    return True


def put_secret(
    *,
    kind: str,
    value: str,
    asset_id: str | None = None,
    lifecycle: str = "temporary",
) -> str:
    """Encrypt a value and return a stable opaque ``secret_ref``."""
    if not isinstance(value, str) or value == "":
        raise ValueError("secret value must be a non-empty string")
    if kind not in _dom.SECRET_KINDS:
        raise ValueError(f"secret kind must be one of {list(_dom.SECRET_KINDS)}")
    if lifecycle not in _dom.SECRET_REQUESTABLE_LIFECYCLE:
        raise ValueError(
            f"requested lifecycle must be one of {list(_dom.SECRET_REQUESTABLE_LIFECYCLE)}"
        )
    with _lock:
        _store.init_db()
        document = _read_document()
        key = _require_key(document)
        secret_ref = _new_ref()
        _store.put_secret_ref(
            secret_ref=secret_ref,
            kind=kind,
            backend="portable_v1",
            asset_id=asset_id,
            lifecycle="provisioning",
        )
        try:
            document["records"][secret_ref] = {
                "kind": kind,
                "asset_id": asset_id,
                "lifecycle": lifecycle,
                "created_at": time.time(),
                **_encrypt(
                    key,
                    value.encode("utf-8"),
                    _record_aad(document["vault_id"], secret_ref, kind),
                ),
            }
            document["updated_at"] = time.time()
            _atomic_write(document)
        except Exception as exc:
            _best_effort_mark(secret_ref, "cleanup_pending")
            raise VaultProvisioningError(secret_ref, "portable vault write failed") from exc
        try:
            finalized = _store.finalize_provisioning(secret_ref, lifecycle)
        except _store.StoreUnavailable as exc:
            raise VaultProvisioningError(
                secret_ref,
                "provisioning did not complete (metadata unavailable; encrypted record tracked)",
            ) from exc
        if not finalized:
            raise VaultProvisioningError(secret_ref, "provisioning state conflict")
        return secret_ref


def resolve(secret_ref: str) -> str:
    """Decrypt one complete live secret for runtime dispatch only."""
    with _lock:
        _store.init_db()
        metadata = _store.secret_ref_state(secret_ref)
        if not metadata:
            raise SecretUnavailable(f"unknown secret_ref: {secret_ref}")
        if metadata.get("backend") != "portable_v1":
            raise SecretUnavailable(f"secret_ref requires explicit legacy migration: {secret_ref}")
        if metadata.get("lifecycle") not in _dom.SECRET_RESOLVABLE_LIFECYCLE:
            raise SecretUnavailable(
                f"secret_ref not resolvable (lifecycle={metadata.get('lifecycle')!r}): {secret_ref}"
            )
        document = _read_document()
        key = _require_key(document)
        record = document["records"].get(secret_ref)
        if not isinstance(record, dict):
            raise SecretUnavailable(f"secret_ref not in portable vault: {secret_ref}")
        kind = str(record.get("kind", ""))
        if kind != metadata.get("kind"):
            raise VaultFormatError(f"secret metadata mismatch: {secret_ref}")
        plaintext = _decrypt(
            key,
            record,
            _record_aad(document["vault_id"], secret_ref, kind),
            f"records.{secret_ref}",
        )
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VaultFormatError(f"secret payload is not UTF-8: {secret_ref}") from exc


def state(secret_ref: str) -> dict[str, Any] | None:
    _store.init_db()
    return _store.secret_ref_state(secret_ref)


def revoke(secret_ref: str) -> None:
    """Remove ciphertext before marking the durable reference revoked."""
    with _lock:
        _store.init_db()
        metadata = _store.secret_ref_state(secret_ref)
        if not metadata:
            raise SecretUnavailable(f"unknown secret_ref: {secret_ref}")
        if metadata.get("backend") != "portable_v1":
            raise SecretUnavailable(f"secret_ref requires explicit legacy migration: {secret_ref}")
        _delete_record(secret_ref)
        _store.mark_secret_revoked(secret_ref)


def rotate_passphrase(new_passphrase: str) -> dict[str, Any]:
    """Re-wrap the unchanged data key; secret refs and ciphertext stay stable."""
    _validate_passphrase(new_passphrase)
    with _lock:
        document = _read_document()
        key = _require_key(document)
        kdf = _kdf_config()
        wrapper_key = _derive_key(new_passphrase, kdf)
        document["kdf"] = kdf
        document["wrappers"]["passphrase"] = _encrypt(
            wrapper_key,
            key,
            _wrapper_aad(document["vault_id"], "passphrase"),
        )
        document["updated_at"] = time.time()
        _atomic_write(document)
        return status()


def rotate_recovery_key() -> dict[str, Any]:
    """Replace recovery wrapping and return the new key exactly once."""
    with _lock:
        document = _read_document()
        key = _require_key(document)
        recovery_text, recovery_raw = _new_recovery_key()
        document["wrappers"]["recovery"] = _encrypt(
            recovery_raw,
            key,
            _wrapper_aad(document["vault_id"], "recovery"),
        )
        document["updated_at"] = time.time()
        _atomic_write(document)
        return {**status(), "recovery_key": recovery_text}


def backup(destination: str | Path) -> dict[str, Any]:
    """Bundle encrypted vault, Workflow/integration state and durable memory."""
    with _lock:
        source = _vault_path()
        document = _read_document()
        key = _require_key(document)
        raw = source.read_bytes()
        components: dict[str, dict[str, str]] = {
            "it_ops.sqlite3": {
                "kind": "sqlite",
                **_encrypt(
                    key,
                    _store.export_database_bytes(),
                    _backup_component_aad(document["vault_id"], "it_ops.sqlite3"),
                ),
            },
        }
        for name, (kind, path) in _portable_components().items():
            if not path.exists():
                continue
            components[name] = {
                "kind": kind,
                **_encrypt(
                    key,
                    _component_bytes(kind, path),
                    _backup_component_aad(document["vault_id"], name),
                ),
            }
        bundle = {
            "format": "elira-portable-vault-backup",
            "version": 3,
            "created_at": time.time(),
            "vault_id": document["vault_id"],
            "vault_sha256": sha256(raw).hexdigest(),
            "vault": _b64(raw),
            "components": components,
        }
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(bundle, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except OSError as exc:
            raise VaultError(f"vault backup failed: {exc}") from exc
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "ok": True,
            "path": str(target),
            "vault_id": document["vault_id"],
            "sha256": bundle["vault_sha256"],
            "components": sorted(components),
        }


def restore(source: str | Path) -> dict[str, Any]:
    """Stage an encrypted bundle; state activates after successful unlock."""
    with _lock:
        backup_path = Path(source).expanduser().resolve()
        try:
            bundle = json.loads(backup_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise VaultFormatError("vault backup cannot be read") from exc
        version = bundle.get("version") if isinstance(bundle, dict) else None
        if (
            not isinstance(bundle, dict)
            or bundle.get("format") != "elira-portable-vault-backup"
            or version not in {2, 3}
        ):
            raise VaultFormatError("unsupported vault backup format")
        raw = _unb64(bundle.get("vault"), "backup.vault")
        if sha256(raw).hexdigest() != bundle.get("vault_sha256"):
            raise VaultFormatError("vault backup checksum mismatch")
        try:
            restored = _validate_document(json.loads(raw.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise VaultFormatError("vault backup payload is invalid") from exc
        if restored["vault_id"] != bundle.get("vault_id"):
            raise VaultFormatError("vault backup identity mismatch")
        if version == 2:
            it_ops = bundle.get("it_ops")
            components: dict[str, Any] = {
                "it_ops.sqlite3": {"kind": "sqlite", **(it_ops if isinstance(it_ops, dict) else {})},
            }
        else:
            raw_components = bundle.get("components")
            if not isinstance(raw_components, dict):
                raise VaultFormatError("vault backup components are missing")
            components = dict(raw_components)
        allowed = {"it_ops.sqlite3": ("sqlite", None), **_portable_components()}
        if "it_ops.sqlite3" not in components:
            raise VaultFormatError("vault backup secret metadata is missing")
        for name, encrypted in components.items():
            if name not in allowed or not isinstance(encrypted, dict):
                raise VaultFormatError(f"unsupported vault backup component: {name}")
            expected_kind = allowed[name][0]
            if encrypted.get("kind") != expected_kind:
                raise VaultFormatError(f"vault backup component kind mismatch: {name}")
            nonce = _unb64(encrypted.get("nonce"), f"backup.{name}.nonce")
            _unb64(encrypted.get("ciphertext"), f"backup.{name}.ciphertext")
            if len(nonce) != _NONCE_BYTES:
                raise VaultFormatError(f"vault backup component nonce is invalid: {name}")
        restored["pending_restore"] = {
            "backup_version": int(version),
            "components": components,
        }
        _clear_data_key()
        _atomic_write(restored)
        return status()


def _apply_pending_restore(document: dict[str, Any], key: bytes) -> None:
    pending = document.get("pending_restore")
    if not isinstance(pending, dict):
        return
    version = int(pending.get("backup_version") or 2)
    components = pending.get("components")
    if not isinstance(components, dict):
        # Compatibility with the first v2 staging shape.
        components = {"it_ops.sqlite3": {"kind": "sqlite", **dict(pending.get("it_ops") or {})}}
    portable = _portable_components()
    for name, encrypted in components.items():
        raw = _decrypt(
            key,
            encrypted,
            _backup_component_aad(document["vault_id"], name, version=version),
            f"pending_restore.{name}",
        )
        if name == "it_ops.sqlite3":
            _store.restore_database_bytes(raw)
            continue
        expected = portable.get(name)
        if expected is None:
            raise VaultFormatError(f"unsupported pending restore component: {name}")
        kind, path = expected
        if encrypted.get("kind") != kind:
            raise VaultFormatError(f"pending restore component kind mismatch: {name}")
        _install_component(kind, path, raw)
    document.pop("pending_restore", None)
    document["updated_at"] = time.time()
    _atomic_write(document)


def migrate_legacy_secret(secret_ref: str) -> dict[str, Any]:
    """Explicitly move one WinCred value into the portable vault in-place."""
    with _lock:
        _store.init_db()
        metadata = _store.secret_ref_state(secret_ref)
        if not metadata:
            raise SecretUnavailable(f"unknown secret_ref: {secret_ref}")
        if metadata.get("backend") == "portable_v1":
            from app.infrastructure.secrets import wincred

            legacy_deleted = wincred.delete_secret(secret_ref) if wincred.available() else False
            return {
                "ok": True,
                "secret_ref": secret_ref,
                "already_migrated": True,
                "legacy_deleted": legacy_deleted,
            }
        if metadata.get("backend") != "wincred":
            raise SecretUnavailable(f"unsupported legacy backend: {metadata.get('backend')!r}")
        from app.infrastructure.secrets import wincred

        value = wincred.read_secret(secret_ref)
        if value is None:
            raise SecretUnavailable(f"legacy secret_ref not found in WinCred: {secret_ref}")
        document = _read_document()
        key = _require_key(document)
        kind = str(metadata.get("kind", ""))
        record = {
            "kind": kind,
            "asset_id": metadata.get("asset_id"),
            "lifecycle": metadata.get("lifecycle"),
            "created_at": metadata.get("created_at") or time.time(),
            **_encrypt(
                key,
                value.encode("utf-8"),
                _record_aad(document["vault_id"], secret_ref, kind),
            ),
        }
        document["records"][secret_ref] = record
        document["updated_at"] = time.time()
        _atomic_write(document)
        round_trip = _decrypt(
            key,
            record,
            _record_aad(document["vault_id"], secret_ref, kind),
            f"records.{secret_ref}",
        ).decode("utf-8")
        if not secrets.compare_digest(round_trip, value):
            _delete_record(secret_ref)
            raise VaultError(f"legacy migration round-trip failed: {secret_ref}")
        _store.set_secret_backend(secret_ref, "portable_v1")
        wincred.delete_secret(secret_ref)
        return {
            "ok": True,
            "secret_ref": secret_ref,
            "already_migrated": False,
            "legacy_deleted": True,
        }


def recover_incomplete_secrets() -> dict[str, Any]:
    """Manual recovery for tracked incomplete portable records."""
    with _lock:
        _store.init_db()
        document = _read_document()
        _require_key(document)
        stale_before = _store._now() - _store.LEASE_TTL_SECONDS
        refs = _store.list_recoverable(
            stale_before,
            _store.MAX_RECOVERY_PER_START + 1,
        )
        capped = len(refs) > _store.MAX_RECOVERY_PER_START
        cleaned: list[str] = []
        failed: list[dict[str, str]] = []
        skipped: list[str] = []
        exhausted: list[str] = []
        for record in refs[:_store.MAX_RECOVERY_PER_START]:
            secret_ref = str(record["secret_ref"])
            if record.get("backend") != "portable_v1":
                skipped.append(secret_ref)
                continue
            if int(record.get("recovery_attempts") or 0) >= _store.MAX_RECOVERY_ATTEMPTS:
                if _store.mark_recovery_failed(secret_ref, stale_before):
                    exhausted.append(secret_ref)
                continue
            try:
                if not _store.claim_for_recovery(secret_ref, stale_before):
                    skipped.append(secret_ref)
                    continue
                _delete_record(secret_ref)
                if _store.delete_claimed_recovery(secret_ref):
                    cleaned.append(secret_ref)
                else:
                    failed.append({
                        "secret_ref": secret_ref,
                        "error": "state conflict on record delete",
                    })
            except (VaultError, _store.StoreUnavailable) as exc:
                _best_effort_mark(secret_ref, "cleanup_pending")
                failed.append({"secret_ref": secret_ref, "error": str(exc)})
        return {
            "cleaned": cleaned,
            "failed": failed,
            "skipped": skipped,
            "exhausted": exhausted,
            "capped": capped,
        }


atexit.register(lock)
