"""Read/delete adapter for explicit legacy WinCred migration.

The active vault never calls Credential Manager or DPAPI. This module can read an
old ``Elira:itops:`` value immediately before encrypting it into the portable
vault, and delete the legacy copy only after a verified round-trip. It has no
write API and is not imported by ordinary vault lifecycle or secret resolution.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_PREFIX = "Elira:itops:"          # Credential Manager target namespace
CRED_TYPE_GENERIC = 0x1
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
