"""Windows Credential Manager wrapper (ctypes / advapi32).

CredWrite / CredRead / CredDelete only — the Phase-0 vault backend. Credential
Manager protects the value with DPAPI under the hood (OS-internal); this module
never invokes DPAPI directly and never stores a ciphertext blob in the app.

Import-guarded: on a non-Windows platform (or if advapi32 is unavailable) the
module raises ``WinCredUnavailable`` on first use, so the IT-Ops vault fails
closed off-Windows rather than silently persisting secrets elsewhere.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_PREFIX = "Elira:itops:"          # Credential Manager target namespace
CRED_TYPE_GENERIC = 0x1
CRED_PERSIST_LOCAL_MACHINE = 0x2  # per-user, this machine, survives logoff
ERROR_NOT_FOUND = 1168            # winerror.h — the ref simply does not exist


class WinCredUnavailable(RuntimeError):
    """Credential Manager is not available (non-Windows or advapi32 missing)."""


def available() -> bool:
    return sys.platform == "win32"


def _advapi32():
    if sys.platform != "win32":
        raise WinCredUnavailable("Windows Credential Manager is Windows-only")
    try:
        return ctypes.WinDLL("advapi32", use_last_error=True)
    except OSError as exc:  # pragma: no cover - defensive
        raise WinCredUnavailable(f"advapi32 unavailable: {exc}") from exc


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _target(secret_ref: str) -> str:
    return f"{_PREFIX}{secret_ref}"


def write_secret(secret_ref: str, value: str) -> None:
    """Store *value* under the opaque secret_ref (CRED_TYPE_GENERIC, per-user)."""
    lib = _advapi32()
    blob = value.encode("utf-8")
    cred = _CREDENTIAL()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = _target(secret_ref)
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                      ctypes.POINTER(ctypes.c_char))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = _target(secret_ref)
    lib.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    lib.CredWriteW.restype = wintypes.BOOL
    if not lib.CredWriteW(ctypes.byref(cred), 0):
        raise WinCredUnavailable(f"CredWrite failed (err={ctypes.get_last_error()})")


def read_secret(secret_ref: str) -> str | None:
    """Return the value, or None if the ref does NOT EXIST. An infrastructure /
    access error (anything other than ERROR_NOT_FOUND) is raised — a missing secret
    and a broken vault must never look the same."""
    lib = _advapi32()
    lib.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    lib.CredReadW.restype = wintypes.BOOL
    lib.CredFree.argtypes = [ctypes.c_void_p]
    ptr = ctypes.POINTER(_CREDENTIAL)()
    if not lib.CredReadW(_target(secret_ref), CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None
        raise WinCredUnavailable(f"CredRead failed (err={err})")
    try:
        cred = ptr.contents
        size = int(cred.CredentialBlobSize)
        if size <= 0:
            return ""
        raw = ctypes.string_at(cred.CredentialBlob, size)
        return raw.decode("utf-8", errors="replace")
    finally:
        lib.CredFree(ptr)


def delete_secret(secret_ref: str) -> bool:
    """Delete the ref from Credential Manager. Returns True if it existed and was
    deleted, False if it did NOT exist (ERROR_NOT_FOUND). Any other failure
    (infrastructure / access error) is RAISED — never silently swallowed as False."""
    lib = _advapi32()
    lib.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    lib.CredDeleteW.restype = wintypes.BOOL
    if lib.CredDeleteW(_target(secret_ref), CRED_TYPE_GENERIC, 0):
        return True
    err = ctypes.get_last_error()
    if err == ERROR_NOT_FOUND:
        return False
    raise WinCredUnavailable(f"CredDelete failed (err={err})")
