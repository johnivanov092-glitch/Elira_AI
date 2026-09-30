"""Windows primitives for the protected Foundation lifecycle, not agent tools.

Candidate commands always use an authenticated, unelevated interactive token.
No error path falls back to the identity of this process. Importing this module
does not install a service, change ACLs, or start a process.
"""
from __future__ import annotations

import contextlib
import base64
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Callable


MAX_FRAME = 1024 * 1024
_IS_WINDOWS = os.name == "nt"
if _IS_WINDOWS:
    import msvcrt
_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_INVALID = ctypes.c_void_p(-1).value
_TOKEN_QUERY, _TOKEN_DUPLICATE, _TOKEN_ASSIGN_PRIMARY = 8, 2, 1
_TOKEN_IMPERSONATE = 4
_TOKEN_ADJUST_PRIVILEGES = 32
_WAIT_OBJECT_0, _WAIT_TIMEOUT = 0, 258
_ERROR_IO_PENDING, _ERROR_PIPE_CONNECTED = 997, 535
_ERROR_BROKEN_PIPE, _ERROR_NO_DATA = 109, 232
_PIPE_RIGHTS = 0x0012019B  # generic read/write minus APPEND/CREATE_PIPE_INSTANCE


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]


class _LUID_ATTR(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", w.DWORD)]


class _TOKEN_PRIVILEGES_ONE(ctypes.Structure):
    _fields_ = [("PrivilegeCount", w.DWORD), ("Privileges", _LUID_ATTR * 1)]


class _SID_ATTR(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", w.DWORD)]


class _TOKEN_GROUPS_HEAD(ctypes.Structure):
    _fields_ = [("GroupCount", w.DWORD), ("Groups", _SID_ATTR * 1)]


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD),
                ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD),
                ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", w.BOOL)]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


class _JOB_BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
               ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOB_EXTENDED(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOB_BASIC), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _SERVICE_STATUS(ctypes.Structure):
    _fields_ = [(name, w.DWORD) for name in
               ("dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode",
                "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint")]


class _SERVICE_STATUS_PROCESS(ctypes.Structure):
    _fields_ = _SERVICE_STATUS._fields_ + [("dwProcessId", w.DWORD), ("dwServiceFlags", w.DWORD)]


class _FILE_INFORMATION(ctypes.Structure):
    _fields_ = [("attributes", w.DWORD), ("creation", w.FILETIME), ("access", w.FILETIME),
                ("write", w.FILETIME), ("volume", w.DWORD), ("size_high", w.DWORD),
                ("size_low", w.DWORD), ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]


_SERVICE_MAIN = _CALLBACK(None, w.DWORD, ctypes.POINTER(w.LPWSTR))
_SERVICE_HANDLER = _CALLBACK(w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p, ctypes.c_void_p)


class _SERVICE_TABLE_ENTRY(ctypes.Structure):
    _fields_ = [("lpServiceName", w.LPWSTR), ("lpServiceProc", _SERVICE_MAIN)]


def _windows() -> None:
    if not _IS_WINDOWS:
        raise OSError("Foundation Windows primitives require Windows")


def _bind(dll: Any, name: str, restype: Any, *argtypes: Any) -> Any:
    function = getattr(dll, name)
    function.restype, function.argtypes = restype, list(argtypes)
    return function


if _IS_WINDOWS:
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _a = ctypes.WinDLL("advapi32", use_last_error=True)
    _u = ctypes.WinDLL("userenv", use_last_error=True)
    _bind(_k, "CloseHandle", w.BOOL, w.HANDLE)
    _bind(_k, "GetCurrentProcess", w.HANDLE)
    _bind(_k, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)
    _bind(_k, "CreateEventW", w.HANDLE, ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR)
    _bind(_k, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
    _bind(_k, "GetExitCodeProcess", w.BOOL, w.HANDLE, ctypes.POINTER(w.DWORD))
    _bind(_k, "TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
    _bind(_k, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    _bind(_k, "GetProcessTimes", w.BOOL, w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4))
    _bind(_k, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
    _bind(_k, "GetFileInformationByHandle", w.BOOL, w.HANDLE, ctypes.POINTER(_FILE_INFORMATION))
    _bind(_k, "GetFileType", w.DWORD, w.HANDLE)
    _bind(_k, "GetFinalPathNameByHandleW", w.DWORD, w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
    _bind(_k, "ResumeThread", w.DWORD, w.HANDLE)
    _bind(_k, "CreateJobObjectW", w.HANDLE, ctypes.c_void_p, w.LPCWSTR)
    _bind(_k, "SetInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
    _bind(_k, "AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)
    _bind(_k, "TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)
    _bind(_k, "CreateNamedPipeW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
          w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(_SECURITY_ATTRIBUTES))
    _bind(_k, "ConnectNamedPipe", w.BOOL, w.HANDLE, ctypes.POINTER(_OVERLAPPED))
    _bind(_k, "DisconnectNamedPipe", w.BOOL, w.HANDLE)
    _bind(_k, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
          w.DWORD, w.DWORD, w.HANDLE)
    _bind(_k, "ReadFile", w.BOOL, w.HANDLE, ctypes.c_void_p, w.DWORD,
          ctypes.POINTER(w.DWORD), ctypes.POINTER(_OVERLAPPED))
    _bind(_k, "WriteFile", w.BOOL, w.HANDLE, ctypes.c_void_p, w.DWORD,
          ctypes.POINTER(w.DWORD), ctypes.POINTER(_OVERLAPPED))
    _bind(_k, "GetOverlappedResult", w.BOOL, w.HANDLE, ctypes.POINTER(_OVERLAPPED),
          ctypes.POINTER(w.DWORD), w.BOOL)
    _bind(_k, "CancelIoEx", w.BOOL, w.HANDLE, ctypes.POINTER(_OVERLAPPED))
    _bind(_k, "GetNamedPipeServerProcessId", w.BOOL, w.HANDLE, ctypes.POINTER(w.ULONG))
    _bind(_k, "GetNamedPipeClientProcessId", w.BOOL, w.HANDLE, ctypes.POINTER(w.ULONG))
    _bind(_a, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
    _bind(_a, "OpenThreadToken", w.BOOL, w.HANDLE, w.DWORD, w.BOOL, ctypes.POINTER(w.HANDLE))
    _bind(_k, "GetCurrentThread", w.HANDLE)
    _bind(_a, "GetTokenInformation", w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p,
          w.DWORD, ctypes.POINTER(w.DWORD))
    _bind(_a, "DuplicateTokenEx", w.BOOL, w.HANDLE, w.DWORD, ctypes.c_void_p,
          ctypes.c_int, ctypes.c_int, ctypes.POINTER(w.HANDLE))
    _bind(_a, "CreateRestrictedToken", w.BOOL, w.HANDLE, w.DWORD, w.DWORD,
          ctypes.POINTER(_SID_ATTR), w.DWORD, ctypes.c_void_p, w.DWORD,
          ctypes.c_void_p, ctypes.POINTER(w.HANDLE))
    _bind(_a, "SetTokenInformation", w.BOOL, w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
    _bind(_a, "InitializeSecurityDescriptor", w.BOOL, ctypes.c_void_p, w.DWORD)
    _bind(_a, "SetSecurityDescriptorDacl", w.BOOL, ctypes.c_void_p, w.BOOL, ctypes.c_void_p, w.BOOL)
    _bind(_a, "GetSecurityDescriptorDacl", w.BOOL, ctypes.c_void_p, ctypes.POINTER(w.BOOL),
          ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.BOOL))
    _bind(_a, "ConvertStringSidToSidW", w.BOOL, w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    _bind(_a, "GetLengthSid", w.DWORD, ctypes.c_void_p)
    _bind(_a, "LookupPrivilegeNameW", w.BOOL, w.LPCWSTR, ctypes.POINTER(_LUID),
          w.LPWSTR, ctypes.POINTER(w.DWORD))
    _bind(_a, "ImpersonateLoggedOnUser", w.BOOL, w.HANDLE)
    _bind(_a, "ImpersonateNamedPipeClient", w.BOOL, w.HANDLE)
    _bind(_a, "RevertToSelf", w.BOOL)
    _bind(_a, "ConvertSidToStringSidW", w.BOOL, ctypes.c_void_p, ctypes.POINTER(w.LPWSTR))
    _bind(_a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
          w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.ULONG))
    _bind(_a, "GetSecurityInfo", w.DWORD, w.HANDLE, ctypes.c_int, w.DWORD,
          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
          ctypes.POINTER(ctypes.c_void_p))
    _bind(_a, "ConvertSecurityDescriptorToStringSecurityDescriptorW", w.BOOL,
          ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.POINTER(w.ULONG))
    _bind(_a, "LookupAccountNameW", w.BOOL, w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p,
          ctypes.POINTER(w.DWORD), w.LPWSTR, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD))
    _bind(_a, "LookupPrivilegeValueW", w.BOOL, w.LPCWSTR, w.LPCWSTR, ctypes.POINTER(_LUID))
    _bind(_a, "AdjustTokenPrivileges", w.BOOL, w.HANDLE, w.BOOL,
          ctypes.POINTER(_TOKEN_PRIVILEGES_ONE), w.DWORD, ctypes.c_void_p, ctypes.c_void_p)
    _bind(_a, "CreateProcessAsUserW", w.BOOL, w.HANDLE, w.LPCWSTR, w.LPWSTR,
          ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p,
          w.LPCWSTR, ctypes.POINTER(_STARTUPINFO), ctypes.POINTER(_PROCESS_INFORMATION))
    _bind(_u, "CreateEnvironmentBlock", w.BOOL, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.BOOL)
    _bind(_u, "DestroyEnvironmentBlock", w.BOOL, ctypes.c_void_p)
    _bind(_a, "OpenSCManagerW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.DWORD)
    _bind(_a, "OpenServiceW", w.HANDLE, w.HANDLE, w.LPCWSTR, w.DWORD)
    _bind(_a, "CloseServiceHandle", w.BOOL, w.HANDLE)
    _bind(_a, "QueryServiceStatusEx", w.BOOL, w.HANDLE, ctypes.c_int,
          ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD))
    _bind(_a, "StartServiceCtrlDispatcherW", w.BOOL, ctypes.POINTER(_SERVICE_TABLE_ENTRY))
    _bind(_a, "RegisterServiceCtrlHandlerExW", w.HANDLE, w.LPCWSTR, _SERVICE_HANDLER, ctypes.c_void_p)
    _bind(_a, "SetServiceStatus", w.BOOL, w.HANDLE, ctypes.POINTER(_SERVICE_STATUS))


def _check(ok: Any) -> Any:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return ok


class _Handle:
    def __init__(self, value: int):
        if value is None or value == _INVALID or value == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        self.value = value

    def close(self) -> None:
        if self.value is not None:
            _k.CloseHandle(self.value)
            self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _token_buffer(handle: int, information: int):
    length = w.DWORD()
    _a.GetTokenInformation(handle, information, None, 0, ctypes.byref(length))
    if not length.value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = ctypes.create_string_buffer(length.value)
    _check(_a.GetTokenInformation(handle, information, result, length, ctypes.byref(length)))
    return result


def _sid_text(sid: int) -> str:
    value = w.LPWSTR()
    _check(_a.ConvertSidToStringSidW(sid, ctypes.byref(value)))
    try:
        return value.value
    finally:
        _k.LocalFree(ctypes.cast(value, ctypes.c_void_p))


def account_sid(name: str) -> str:
    _windows()
    size, domain_size, kind = w.DWORD(), w.DWORD(), w.DWORD()
    _a.LookupAccountNameW(None, name, None, ctypes.byref(size), None, ctypes.byref(domain_size), ctypes.byref(kind))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    sid, domain = ctypes.create_string_buffer(size.value), ctypes.create_unicode_buffer(domain_size.value)
    _check(_a.LookupAccountNameW(None, name, sid, ctypes.byref(size), domain,
                                ctypes.byref(domain_size), ctypes.byref(kind)))
    return _sid_text(ctypes.addressof(sid))


def token_info(handle: int) -> dict[str, Any]:
    _windows()
    user = _token_buffer(handle, 1)
    groups_buffer = _token_buffer(handle, 2)
    head = ctypes.cast(groups_buffer, ctypes.POINTER(_TOKEN_GROUPS_HEAD)).contents
    start = ctypes.addressof(groups_buffer) + _TOKEN_GROUPS_HEAD.Groups.offset
    groups = []
    for index in range(head.GroupCount):
        group = _SID_ATTR.from_address(start + index * ctypes.sizeof(_SID_ATTR))
        groups.append({"sid": _sid_text(group.Sid), "attributes": group.Attributes})
    integrity = _token_buffer(handle, 25)
    integrity_sid = _sid_text(ctypes.cast(integrity, ctypes.POINTER(_SID_ATTR)).contents.Sid)
    privileges_buffer = _token_buffer(handle, 3)
    privilege_count = ctypes.cast(privileges_buffer, ctypes.POINTER(w.DWORD)).contents.value
    privilege_start = ctypes.addressof(privileges_buffer) + _TOKEN_PRIVILEGES_ONE.Privileges.offset
    privileges = []
    for index in range(privilege_count):
        privilege = _LUID_ATTR.from_address(privilege_start + index * ctypes.sizeof(_LUID_ATTR))
        length = w.DWORD(256)
        name = ctypes.create_unicode_buffer(length.value)
        _check(_a.LookupPrivilegeNameW(None, ctypes.byref(privilege.Luid), name, ctypes.byref(length)))
        privileges.append({"name": name.value, "attributes": privilege.Attributes})
    integer = lambda key: ctypes.cast(_token_buffer(handle, key), ctypes.POINTER(w.DWORD)).contents.value
    return {"sid": _sid_text(ctypes.cast(user, ctypes.POINTER(_SID_ATTR)).contents.Sid),
            "session_id": integer(12), "elevated": bool(integer(20)),
            "elevation_type": integer(18), "integrity_rid": int(integrity_sid.rsplit("-", 1)[1]),
            "ui_access": bool(integer(26)), "groups": groups, "privileges": privileges,
            "administrator_enabled": any(g["sid"] == "S-1-5-32-544" and g["attributes"] & 4 for g in groups)}


def _validate_user(info: dict[str, Any], *, allowed_sid: str | None = None,
                   token_mode: str = "limited") -> None:
    if token_mode not in {"limited", "administrator"}:
        raise ValueError("Unknown application token mode")
    if (info["session_id"] == 0 or info["ui_access"]
            or info["sid"] in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}
            or (allowed_sid is not None and info["sid"] != allowed_sid)):
        raise PermissionError("An authenticated interactive user token is required")
    if token_mode == "administrator":
        if not info["elevated"] or not info["administrator_enabled"] or info["integrity_rid"] != 12288:
            raise PermissionError("Administrator mode requires the current elevated interactive user; no automatic elevation")
        return
    dangerous = {"SeDebugPrivilege", "SeTcbPrivilege", "SeImpersonatePrivilege",
                 "SeAssignPrimaryTokenPrivilege", "SeBackupPrivilege", "SeRestorePrivilege",
                 "SeTakeOwnershipPrivilege", "SeLoadDriverPrivilege", "SeCreateTokenPrivilege"}
    if (info["elevated"] or info["elevation_type"] == 2
            or info["integrity_rid"] > 8192 or info["administrator_enabled"]
            or any(p["name"] in dangerous for p in info["privileges"])):
        raise PermissionError("An authenticated unelevated interactive user token is required")


class UserToken(_Handle):
    def __init__(self, value: int, *, allowed_sid: str | None = None, token_mode: str = "limited"):
        super().__init__(value)
        try:
            self.info = token_info(self.value)
            _validate_user(self.info, allowed_sid=allowed_sid, token_mode=token_mode)
            self.token_mode = token_mode
        except BaseException:
            self.close()
            raise


def _primary_token(handle: int, *, allowed_sid: str | None = None, token_mode: str = "limited") -> UserToken:
    duplicate = w.HANDLE()
    _check(_a.DuplicateTokenEx(handle, _TOKEN_QUERY | _TOKEN_DUPLICATE | _TOKEN_ASSIGN_PRIMARY | _TOKEN_IMPERSONATE,
                              None, 2, 1, ctypes.byref(duplicate)))
    return UserToken(duplicate.value, allowed_sid=allowed_sid, token_mode=token_mode)


def _restricted_user_token(handle: int, allowed_sid: str) -> UserToken:
    """Explicit per-process rights reduction for an admin without a linked token."""
    duplicate = w.HANDLE()
    rights = _TOKEN_QUERY | _TOKEN_DUPLICATE | _TOKEN_ASSIGN_PRIMARY | _TOKEN_IMPERSONATE | 0x80
    _check(_a.DuplicateTokenEx(handle, rights, None, 2, 1, ctypes.byref(duplicate)))
    with _Handle(duplicate.value) as original:
        group_buffer = _token_buffer(original.value, 2)
        count = ctypes.cast(group_buffer, ctypes.POINTER(w.DWORD)).contents.value
        start = ctypes.addressof(group_buffer) + _TOKEN_GROUPS_HEAD.Groups.offset
        ordinary = {"S-1-1-0", "S-1-2-0", "S-1-2-1", "S-1-5-4", "S-1-5-11",
                    "S-1-5-15", "S-1-5-32-545", "S-1-5-64-10", "S-1-5-113"}
        disabled = []
        for index in range(count):
            group = _SID_ATTR.from_address(start + index * ctypes.sizeof(_SID_ATTR))
            sid = _sid_text(group.Sid)
            if (sid not in ordinary and not sid.startswith("S-1-5-5-")
                    and not group.Attributes & 0x20):  # do not disable integrity SID
                disabled.append(_SID_ATTR(group.Sid, 0))
        array = (_SID_ATTR * len(disabled))(*disabled)
        restricted = w.HANDLE()
        _check(_a.CreateRestrictedToken(original.value, 1 | 4, len(array), array,
                                        0, None, 0, None, ctypes.byref(restricted)))
        with _Handle(restricted.value) as reduced:
            medium = ctypes.c_void_p()
            _check(_a.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(medium)))
            try:
                label = _SID_ATTR(medium, 0x20)
                _check(_a.SetTokenInformation(reduced.value, 25, ctypes.byref(label),
                                              ctypes.sizeof(label) + _a.GetLengthSid(medium)))
            finally:
                _k.LocalFree(medium)
            _user_default_dacl(reduced.value, allowed_sid)
            result = _primary_token(reduced.value, allowed_sid=allowed_sid)
            result.origin = "restricted_current_process"
            return result


def _user_default_dacl(token: int, sid: str) -> None:
    """Preserve the new restricted token's ACEs and grant its user self access.

    A built-in Administrator default DACL can name only Administrators/System
    plus read-only logon access. Once Administrators becomes deny-only, newly
    created process/thread objects would deny their own user initialization.
    This changes only the new token, never a service or filesystem object ACL.
    """
    original = _default_dacl_diagnostic(token)
    if not original.startswith("D:") or "NO_ACCESS_CONTROL" in original:
        raise PermissionError("A concrete user token default DACL is required")
    descriptor = ctypes.c_void_p()
    _check(_a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        original + f"(A;;GA;;;{sid})", 1, ctypes.byref(descriptor), None))
    try:
        present, defaulted, dacl = w.BOOL(), w.BOOL(), ctypes.c_void_p()
        _check(_a.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                           ctypes.byref(dacl), ctypes.byref(defaulted)))
        if not present.value or not dacl.value:
            raise PermissionError("The user token default DACL is missing")
        _check(_a.SetTokenInformation(token, 6, ctypes.byref(dacl), ctypes.sizeof(dacl)))
    finally:
        _k.LocalFree(descriptor)


def current_user_token(*, limited: bool = True, token_mode: str = "limited") -> UserToken:
    _windows()
    original = w.HANDLE()
    _check(_a.OpenProcessToken(_k.GetCurrentProcess(), _TOKEN_QUERY | _TOKEN_DUPLICATE, ctypes.byref(original)))
    with _Handle(original.value) as owned:
        info = token_info(owned.value)
        if token_mode not in {"limited", "administrator"}:
            raise ValueError("Unknown application token mode")
        if token_mode == "limited" and limited and info["elevated"]:
            try:
                linked = ctypes.cast(_token_buffer(owned.value, 19), ctypes.POINTER(w.HANDLE)).contents.value
            except OSError as exc:
                if info["elevation_type"] != 1 or exc.winerror != 1312:
                    raise
                return _restricted_user_token(owned.value, info["sid"])
            with _Handle(linked) as linked_handle:
                result = _primary_token(linked_handle.value, allowed_sid=info["sid"])
                result.origin = "linked_limited"
                return result
        result = _primary_token(owned.value, token_mode=token_mode)
        result.origin = "current_process"
        return result


@contextlib.contextmanager
def _restore_thread_identity():
    previous = w.HANDLE()
    opened = _a.OpenThreadToken(_k.GetCurrentThread(), _TOKEN_QUERY | _TOKEN_IMPERSONATE,
                                True, ctypes.byref(previous))
    if not opened and ctypes.get_last_error() == 5:
        opened = _a.OpenThreadToken(_k.GetCurrentThread(), _TOKEN_QUERY | _TOKEN_IMPERSONATE,
                                    False, ctypes.byref(previous))
    if not opened and ctypes.get_last_error() != 1008:  # ERROR_NO_TOKEN
        raise ctypes.WinError(ctypes.get_last_error())
    prior = _Handle(previous.value) if opened else None
    try:
        yield
    finally:
        restored = _a.ImpersonateLoggedOnUser(prior.value) if prior is not None else _a.RevertToSelf()
        if prior is not None:
            prior.close()
        if not restored:
            # Continuing a service with the wrong thread identity is unsafe.
            os._exit(70)


@contextlib.contextmanager
def _impersonate(token: UserToken):
    with _restore_thread_identity():
        _check(_a.ImpersonateLoggedOnUser(token.value))
        yield


def _capture_pipe_token(pipe: int, allowed_sid: str, *, token_mode: str = "limited") -> UserToken:
    with _restore_thread_identity():
        _check(_a.ImpersonateNamedPipeClient(pipe))
        thread_token = w.HANDLE()
        _check(_a.OpenThreadToken(_k.GetCurrentThread(), _TOKEN_QUERY | _TOKEN_DUPLICATE,
                                  True, ctypes.byref(thread_token)))
        with _Handle(thread_token.value) as owned:
            return _primary_token(owned.value, allowed_sid=allowed_sid, token_mode=token_mode)


def _enable_launch_privileges() -> None:
    process_token = w.HANDLE()
    _check(_a.OpenProcessToken(_k.GetCurrentProcess(), _TOKEN_QUERY | _TOKEN_ADJUST_PRIVILEGES,
                               ctypes.byref(process_token)))
    with _Handle(process_token.value) as owned:
        # Medium clients launching themselves do not have/need assignment privilege
        # when using a restricted token of their own primary token.
        service = token_info(owned.value)["sid"] == "S-1-5-19"
        if not service:
            return
        for name in ("SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege"):
            state = _TOKEN_PRIVILEGES_ONE()
            state.PrivilegeCount = 1
            _check(_a.LookupPrivilegeValueW(None, name, ctypes.byref(state.Privileges[0].Luid)))
            state.Privileges[0].Attributes = 2
            ctypes.set_last_error(0)
            _check(_a.AdjustTokenPrivileges(owned.value, False, ctypes.byref(state), 0, None, None))
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)


class WindowsProcess:
    """Owned process + job; closing a live handle terminates its owned job."""
    def __init__(self, process: int, job: int, pid: int, args: list[str], identity: dict):
        self._process, self._job = _Handle(process), _Handle(job)
        self.pid, self.args, self.identity = pid, args, identity
        self.creation_identity = _creation_identity(process)
        image_buffer, image_length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        _check(_k.QueryFullProcessImageNameW(process, 0, image_buffer, ctypes.byref(image_length)))
        self.image_path = image_buffer.value
        self.returncode: int | None = None
        self._relay: _LogRelay | None = None
        self._terminated = False

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if self._process.value is None:
            raise ValueError("Process handle is closed")
        result = _k.WaitForSingleObject(self._process.value, 0)
        if result == _WAIT_TIMEOUT:
            return None
        if result != _WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = w.DWORD()
        _check(_k.GetExitCodeProcess(self._process.value, ctypes.byref(code)))
        if self._relay is not None and not self._relay.done.is_set():
            return None
        self.returncode = int(code.value)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            if _k.WaitForSingleObject(self._process.value, 100) == _WAIT_OBJECT_0 and self._relay is not None:
                self._relay.done.wait(0.1)
        if self._relay is not None and not self._terminated:
            self._relay.check(self.returncode)
        return self.returncode

    def terminate(self) -> None:
        if self.poll() is None:
            _check(_k.TerminateJobObject(self._job.value, 1))
            # Terminating the owned job also kills its runner. It cannot send
            # an exit receipt; require receipts only for normal command exits.
            self._terminated = True

    kill = terminate

    def close(self) -> None:
        self._job.close()
        if self._relay is not None:
            self._relay.close()
        self._process.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _creation_identity(handle: int) -> str:
    times = [w.FILETIME() for _ in range(4)]
    _check(_k.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)))
    return f"win:{(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}"


def _final_path(handle: int) -> str:
    required = _k.GetFinalPathNameByHandleW(handle, None, 0, 0)
    _check(required)
    buffer = ctypes.create_unicode_buffer(required + 1)
    actual = _k.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    _check(actual)
    if actual >= len(buffer):
        raise OSError("Final source path changed while reading its identity")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        raise ValueError("Foundation imports only local source files")
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _open_source_handles(path: str | Path, root: str | Path):
    """Called while impersonating user. Returns held, non-inheritable handles.

    Every ancestor is held without delete sharing; the source also denies write
    sharing. This deliberately refuses junctions and multiple hard links.
    """
    source, boundary = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    if str(source).startswith("\\\\") or str(boundary).startswith("\\\\"):
        raise ValueError("Source and boundary must be local absolute paths")
    try:
        relative = source.relative_to(boundary)
    except ValueError as exc:
        raise ValueError("Source is outside its approved root") from exc
    if not relative.parts or any(":" in part or part in {".", ".."} for part in relative.parts):
        raise ValueError("Invalid source relative path")
    held: list[_Handle] = []
    file_handle = None
    try:
        directories = list(reversed(source.parent.parents)) + [source.parent]
        for directory in directories:
            handle = _Handle(_k.CreateFileW(str(directory), 0x80, 1 | 2, None, 3,
                                            0x02000000 | 0x00200000, None))
            held.append(handle)
            info = _FILE_INFORMATION()
            _check(_k.GetFileInformationByHandle(handle.value, ctypes.byref(info)))
            if info.attributes & 0x400 or not info.attributes & 0x10:
                raise ValueError("Source ancestors must be ordinary directories, not reparse points")
            if _final_path(handle.value) != os.path.normcase(os.path.normpath(str(directory))):
                raise ValueError("Source ancestor final path differs from its approved path")
        file_handle = _Handle(_k.CreateFileW(str(source), 0x80000000, 1, None, 3, 0x00200000, None))
        info = _FILE_INFORMATION()
        _check(_k.GetFileInformationByHandle(file_handle.value, ctypes.byref(info)))
        if _k.GetFileType(file_handle.value) != 1 or info.attributes & (0x400 | 0x10) or info.links != 1:
            raise ValueError(f"Source must be one regular non-reparse file without hard-link aliases: {source}")
        if _final_path(file_handle.value) != os.path.normcase(os.path.normpath(str(source))):
            raise ValueError("Source handle final path differs from its approved path")
        descriptor = msvcrt.open_osfhandle(file_handle.value, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        file_handle.value = None  # ownership transferred to descriptor
        try:
            stream = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        return stream, held
    except BaseException:
        if file_handle is not None:
            file_handle.close()
        for handle in reversed(held):
            handle.close()
        raise


@contextlib.contextmanager
def safe_read_file(path: str | Path, root: str | Path):
    """Open under caller's current impersonation; close safely after reverting."""
    _windows()
    stream, held = _open_source_handles(path, root)
    try:
        yield stream
    finally:
        stream.close()
        for handle in reversed(held):
            handle.close()


class WindowsProcessHost:
    def __init__(self, token: UserToken, *, runner_python: str | None = None,
                 runner_script: str | None = None):
        _windows()
        _validate_user(token.info, token_mode=token.token_mode)
        self.token = _primary_token(token.value, allowed_sid=token.info["sid"], token_mode=token.token_mode)
        self.runner_python = str(Path(runner_python or sys.executable).resolve())
        self.runner_script = str(Path(runner_script or __file__).resolve())

    def clone(self) -> WindowsProcessHost:
        return WindowsProcessHost(self.token, runner_python=self.runner_python, runner_script=self.runner_script)

    def close(self) -> None:
        self.token.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def impersonate(self):
        return _impersonate(self.token)

    @contextlib.contextmanager
    def open_source(self, path: str | Path, root: str | Path):
        # Privileged destinations must be opened by the caller AFTER this block
        # restores service identity, never while impersonating the source owner.
        with self.impersonate():
            stream, held = _open_source_handles(path, root)
        try:
            yield stream
        finally:
            stream.close()
            for handle in reversed(held):
                handle.close()

    def terminate_owned(self, pid: int, identity: str) -> None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not isinstance(identity, str):
            raise ValueError("Owned process recovery requires PID and creation identity")
        with self.impersonate():
            handle = _k.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
            error = ctypes.get_last_error() if not handle else 0
        if not handle:
            if error == 87:  # process already absent
                return
            raise ctypes.WinError(error)
        with _Handle(handle) as owned:
            if _creation_identity(owned.value) != identity:
                raise PermissionError("Recovery PID belongs to a different process incarnation")
            token = w.HANDLE()
            with self.impersonate():
                _check(_a.OpenProcessToken(owned.value, _TOKEN_QUERY, ctypes.byref(token)))
                with _Handle(token.value) as captured:
                    actual = token_info(captured.value)
            _validate_user(actual, allowed_sid=self.token.info["sid"], token_mode=self.token.token_mode)
            if actual["session_id"] != self.token.info["session_id"]:
                raise PermissionError("Recovery process belongs to another session")
            if _k.WaitForSingleObject(owned.value, 0) == _WAIT_TIMEOUT:
                _check(_k.TerminateProcess(owned.value, 1))
                if _k.WaitForSingleObject(owned.value, 10000) == _WAIT_TIMEOUT:
                    raise TimeoutError("Owned recovery process did not terminate")

    def process_identity(self, pid: int) -> str | None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Process identity requires a positive PID")
        with self.impersonate():
            handle = _k.OpenProcess(0x1000 | 0x100000, False, pid)
            error = ctypes.get_last_error() if not handle else 0
        if not handle:
            if error == 87:
                return None
            raise ctypes.WinError(error)
        with _Handle(handle) as owned:
            state = _k.WaitForSingleObject(owned.value, 0)
            if state == _WAIT_OBJECT_0:
                return None
            if state != _WAIT_TIMEOUT:
                raise ctypes.WinError(ctypes.get_last_error())
            return _creation_identity(owned.value)

    def user_environment(self) -> dict[str, str]:
        block = ctypes.c_void_p()
        # Userenv reads the user's loaded profile; LocalService has no authority
        # over that registry hive. Keep this read under the authenticated user.
        with self.impersonate():
            _check(_u.CreateEnvironmentBlock(ctypes.byref(block), self.token.value, False))
        try:
            result: dict[str, str] = {}
            address = block.value
            while True:
                entry = ctypes.wstring_at(address)
                if not entry:
                    return result
                address += (len(entry) + 1) * ctypes.sizeof(w.WCHAR)
                key, separator, value = entry.partition("=")
                if separator and key:  # omit per-drive hidden environment entries
                    result[key.upper()] = value
        finally:
            _u.DestroyEnvironmentBlock(block)

    def popen(self, args: list[str], *, cwd: str | Path, env: dict[str, str] | None = None,
              log=None, desktop: bool = False) -> WindowsProcess:
        if log is not None:
            return self._popen_relay(args, cwd=cwd, env=env, log=log, desktop=desktop)
        if (not isinstance(args, (list, tuple)) or not args
                or any(not isinstance(arg, str) or "\0" in arg for arg in args)
                or not Path(args[0]).is_absolute()):
            raise ValueError("Process arguments require an absolute executable and NUL-free strings")
        with self.impersonate():
            directory = Path(cwd).resolve(strict=True)
            if not directory.is_dir():
                raise ValueError("Process cwd must be an existing directory")
        values = self.user_environment() if env is None else _normalize_environment(env)
        block = ctypes.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(values.items(), key=lambda x: x[0].upper())) + "\0\0")
        startup, process = _STARTUPINFO(), _PROCESS_INFORMATION()
        startup.cb = ctypes.sizeof(startup)
        # Every child belongs to the authenticated interactive session. A null
        # desktop would inherit the service window station; hidden console work
        # still needs the user's station for DLL initialization.
        startup.lpDesktop = "winsta0\\default"
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(args)))
        _enable_launch_privileges()
        job = _Handle(_k.CreateJobObjectW(None, None))
        limits = _JOB_EXTENDED()
        # This job owns the app tree, not the security boundary. The existing
        # durable task runner explicitly breaks away to survive backend restart.
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x800
        try:
            _check(_k.SetInformationJobObject(job.value, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            _check(_a.CreateProcessAsUserW(self.token.value, args[0], command, None, None, False,
                                          0x00000400 | 0x00000004 | (0 if desktop else 0x08000000),
                                          block, str(directory), ctypes.byref(startup), ctypes.byref(process)))
            try:
                child_token = w.HANDLE()
                # Its token DACL belongs to the user, even though the service
                # owns the suspended process handle returned by creation.
                with self.impersonate():
                    _check(_a.OpenProcessToken(process.hProcess, _TOKEN_QUERY, ctypes.byref(child_token)))
                    with _Handle(child_token.value) as owned:
                        identity = token_info(owned.value)
                _validate_user(identity, allowed_sid=self.token.info["sid"], token_mode=self.token.token_mode)
                if identity["session_id"] != self.token.info["session_id"]:
                    raise PermissionError("Child session differs from authenticated user session")
                security = None
                if getattr(self, "proof_diagnostics", False):
                    security = {"process": _security_diagnostic(process.hProcess, 6),
                                "thread": _security_diagnostic(process.hThread, 6)}
                    with self.impersonate():
                        inspected = w.HANDLE()
                        if _a.OpenProcessToken(process.hProcess, _TOKEN_QUERY | 0x20000, ctypes.byref(inspected)):
                            with _Handle(inspected.value) as owned:
                                security["token"] = _security_diagnostic(owned.value, 6)
                        else:
                            security["token"] = {"winerror": ctypes.get_last_error()}
                _check(_k.AssignProcessToJobObject(job.value, process.hProcess))
                if _k.ResumeThread(process.hThread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                result = WindowsProcess(process.hProcess, job.value, process.dwProcessId, list(args), identity)
                if security is not None:
                    result.security = security
                job.value = None
                return result
            except BaseException:
                _k.TerminateProcess(process.hProcess, 1)
                _k.WaitForSingleObject(process.hProcess, 5000)
                _k.CloseHandle(process.hProcess)
                raise
            finally:
                _k.CloseHandle(process.hThread)
        finally:
            job.close()

    def _popen_relay(self, args, *, cwd, env, log, desktop) -> WindowsProcess:
        # The runner has the user token and inherits no service handles. Only
        # this service-side thread owns the duplicated destination descriptor.
        if (not isinstance(args, (list, tuple)) or not args
                or any(not isinstance(arg, str) or "\0" in arg for arg in args)):
            raise ValueError("Process arguments require NUL-free strings")
        with self.impersonate():
            directory = Path(cwd).resolve(strict=True)
            if not directory.is_dir():
                raise ValueError("Process cwd must be an existing directory")
        values = self.user_environment() if env is None else _normalize_environment(env)
        log.flush()
        descriptor = os.dup(log.fileno())
        os.set_inheritable(descriptor, False)
        sink = os.fdopen(descriptor, "wb", buffering=0)
        pipe = child = None
        try:
            name = "\\\\.\\pipe\\EliraFoundation-relay-" + uuid.uuid4().hex
            pipe = _new_pipe(name, _current_server_sid(), self.token.info["sid"], first=True)
            child = self.popen([self.runner_python, "-I", "-S", "-B", self.runner_script,
                                "--worker", name, str(os.getpid()), self.token.token_mode],
                               cwd=Path(self.runner_script).parent, env=self.user_environment())
            _connect_server(pipe.value, threading.Event(), deadline=time.monotonic() + 20)
            hello = _receive(pipe.value, time.monotonic() + 10)
            peer = w.ULONG()
            _check(_k.GetNamedPipeClientProcessId(pipe.value, ctypes.byref(peer)))
            if peer.value != child.pid or hello != {"ready": True}:
                raise PermissionError("Log relay peer differs from the owned user runner")
            with _capture_pipe_token(pipe.value, self.token.info["sid"], token_mode=self.token.token_mode) as peer_token:
                if peer_token.info["session_id"] != self.token.info["session_id"]:
                    raise PermissionError("Log relay peer belongs to another session")
            _send(pipe.value, {"args": list(args), "cwd": str(directory), "env": values,
                               "desktop": bool(desktop)}, time.monotonic() + 10)
            started = _receive(pipe.value, time.monotonic() + 20)
            if set(started) != {"started"} or type(started["started"]) is not int or started["started"] <= 0:
                raise RuntimeError("User runner could not start command: " + str(started.get("error", "invalid response")))
            child.command_pid = started["started"]
            child.args = list(args)
            relay = _LogRelay(pipe, sink, child)
            relay.start()
            child._relay = relay
            pipe, sink = None, None
            return child
        except BaseException:
            if child is not None:
                child.close()
            raise
        finally:
            if pipe is not None:
                pipe.close()
            if sink is not None:
                sink.close()

    def run(self, args: list[str], *, cwd: str | Path, env: dict[str, str] | None = None,
            log=None, timeout: float | None = None) -> subprocess.CompletedProcess:
        with self.popen(args, cwd=cwd, env=env, log=log) as child:
            try:
                code = child.wait(timeout)
            except BaseException:
                child.kill()
                # A deliberately killed runner cannot send a normal EOF/exit
                # receipt; preserve the original timeout/cancellation error.
                with contextlib.suppress(RuntimeError):
                    child.wait(10)
                raise
            result = subprocess.CompletedProcess(args, code)
            result.check_returncode()
            return result


class _LogRelay:
    """Bounded messages to an already-open service log; never a client path."""
    def __init__(self, pipe: _Handle, sink, child: WindowsProcess):
        self.pipe, self.sink, self.child = pipe, sink, child
        self.done = threading.Event()
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.exit_code: int | None = None
        self.thread = threading.Thread(target=self._pump, name="foundation-log-relay", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _pump(self) -> None:
        try:
            while True:
                frame = _receive(self.pipe.value, float("inf"), stop_event=self.stop)
                if set(frame) == {"output"} and isinstance(frame["output"], str):
                    data = base64.b64decode(frame["output"], validate=True)
                    if len(data) > 49152:
                        raise ValueError("Oversized log relay output chunk")
                    remaining = memoryview(data)
                    while remaining:
                        written = self.sink.write(remaining)
                        if not written:
                            raise OSError("Log destination did not accept output")
                        remaining = remaining[written:]
                elif (set(frame) == {"exit"} and type(frame["exit"]) is int
                      and -(2**31) <= frame["exit"] < 2**32):
                    self.exit_code = frame["exit"] & 0xFFFFFFFF
                    self.sink.flush()
                    _send(self.pipe.value, {"ack": True}, time.monotonic() + 10)
                    return
                else:
                    raise RuntimeError("Invalid log relay result: " + str(frame.get("error", "invalid frame")))
        except BaseException as exc:
            self.error = exc
            if self.child._job.value is not None:
                _k.TerminateJobObject(self.child._job.value, 125)
        finally:
            self.pipe.close()
            self.sink.close()
            self.done.set()

    def check(self, returncode: int) -> None:
        if self.error is not None:
            raise RuntimeError("User command log/result relay failed") from self.error
        if self.exit_code != returncode:
            raise RuntimeError("User command exit receipt differs from owned runner exit")

    def close(self) -> None:
        self.stop.set()
        if not self.done.is_set() and self.pipe.value is not None:
            _k.CancelIoEx(self.pipe.value, None)
        self.thread.join(5)
        if self.thread.is_alive():
            raise TimeoutError("Owned log relay did not close")


def _normalize_environment(values: dict[str, str]) -> dict[str, str]:
    if not isinstance(values, dict) or any(
            not isinstance(k, str) or not k or "=" in k or "\0" in k
            or not isinstance(v, str) or "\0" in v for k, v in values.items()):
        raise ValueError("Invalid process environment")
    return {key.upper(): value for key, value in values.items()}


def _current_server_sid() -> str:
    current = w.HANDLE()
    _check(_a.OpenProcessToken(_k.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(current)))
    with _Handle(current.value) as owned:
        info = token_info(owned.value)
    if info["sid"] != "S-1-5-19":
        return info["sid"]
    service_sids = [g["sid"] for g in info["groups"] if g["attributes"] & 4
                    and g["sid"].startswith("S-1-5-80-") and g["sid"] != "S-1-5-80-0"]
    if len(service_sids) != 1:
        raise PermissionError("An unambiguous per-service SID is required for the log relay")
    return service_sids[0]


def _wait_io(handle: int, overlapped: _OVERLAPPED, deadline: float,
             stop_event: threading.Event | None = None) -> int:
    while True:
        if (stop_event is not None and stop_event.is_set()) or time.monotonic() >= deadline:
            _k.CancelIoEx(handle, ctypes.byref(overlapped))
            transferred = w.DWORD()
            # Completion must be observed before the OVERLAPPED/buffer goes away.
            _k.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), True)
            raise TimeoutError("Foundation pipe operation timed out or stopped")
        result = _k.WaitForSingleObject(overlapped.hEvent, 100)
        if result == _WAIT_OBJECT_0:
            transferred = w.DWORD()
            _check(_k.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), False))
            return transferred.value
        if result != _WAIT_TIMEOUT:
            raise ctypes.WinError(ctypes.get_last_error())


def _io(handle: int, buffer, *, write: bool, size: int, deadline: float,
        stop_event: threading.Event | None = None) -> int:
    with _Handle(_k.CreateEventW(None, True, False, None)) as event:
        overlapped, transferred = _OVERLAPPED(), w.DWORD()
        overlapped.hEvent = event.value
        function = _k.WriteFile if write else _k.ReadFile
        if function(handle, buffer, size, ctypes.byref(transferred), ctypes.byref(overlapped)):
            return transferred.value
        error = ctypes.get_last_error()
        if error != _ERROR_IO_PENDING:
            raise ctypes.WinError(error)
        return _wait_io(handle, overlapped, deadline, stop_event)


def _read_exact(handle: int, size: int, deadline: float,
                stop_event: threading.Event | None = None) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        buffer = ctypes.create_string_buffer(size - len(chunks))
        count = _io(handle, buffer, write=False, size=len(buffer), deadline=deadline, stop_event=stop_event)
        if count == 0:
            raise EOFError("Foundation pipe closed before completing a frame")
        chunks.extend(buffer.raw[:count])
    return bytes(chunks)


def _receive(handle: int, deadline: float, *, stop_event: threading.Event | None = None) -> dict:
    length = int.from_bytes(_read_exact(handle, 4, deadline, stop_event), "little")
    if length < 2 or length > MAX_FRAME:
        raise ValueError("Invalid Foundation frame length")
    value = json.loads(_read_exact(handle, length, deadline, stop_event).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Foundation frame must contain an object")
    return value


def _send(handle: int, value: dict, deadline: float) -> None:
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ValueError("Foundation frame exceeds maximum size")
    data = len(body).to_bytes(4, "little") + body
    offset = 0
    while offset < len(data):
        buffer = ctypes.create_string_buffer(data[offset:])
        count = _io(handle, buffer, write=True, size=len(data) - offset, deadline=deadline)
        if count == 0:
            raise EOFError("Foundation pipe closed during write")
        offset += count


def _pipe_name(name: str) -> str:
    if not isinstance(name, str) or not name.startswith("\\\\.\\pipe\\"):
        raise ValueError("Foundation uses only local named pipes")
    suffix = name[len("\\\\.\\pipe\\"):]
    if not suffix or len(suffix) > 180 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in suffix):
        raise ValueError("Invalid local pipe name")
    return name


def _new_pipe(name: str, service_sid: str, allowed_sid: str, *, first: bool) -> _Handle:
    descriptor = ctypes.c_void_p()
    sddl = (f"D:P(A;;GA;;;SY)(A;;GA;;;{service_sid})(A;;0x{_PIPE_RIGHTS:x};;;{allowed_sid})"
            "S:(ML;;NW;;;ME)")
    _check(_a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None))
    try:
        attributes = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
        return _Handle(_k.CreateNamedPipeW(_pipe_name(name), 3 | 0x40000000 | (0x00080000 if first else 0),
                                           0x8, 16, 65536, 65536, 0, ctypes.byref(attributes)))
    finally:
        _k.LocalFree(descriptor)


def _pipe_security(handle: int) -> str:
    return _object_security(handle, 1)


def _object_security(handle: int, kind: int) -> str:
    descriptor, value = ctypes.c_void_p(), w.LPWSTR()
    error = _a.GetSecurityInfo(handle, kind, 4 | 0x10, None, None, None, None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        _check(_a.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 4 | 0x10, ctypes.byref(value), None))
        try:
            return value.value
        finally:
            _k.LocalFree(ctypes.cast(value, ctypes.c_void_p))
    finally:
        _k.LocalFree(descriptor)


def _security_diagnostic(handle: int, kind: int) -> dict:
    try:
        return {"sddl": _object_security(handle, kind)}
    except OSError as exc:
        return {"winerror": exc.winerror}


def _default_dacl_diagnostic(token: int) -> str:
    buffer = _token_buffer(token, 6)
    dacl = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
    descriptor, text = ctypes.create_string_buffer(40), w.LPWSTR()
    _check(_a.InitializeSecurityDescriptor(descriptor, 1))
    _check(_a.SetSecurityDescriptorDacl(descriptor, True, dacl, False))
    _check(_a.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text), None))
    try:
        return text.value
    finally:
        _k.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _connect_server(handle: int, stop_event: threading.Event, *, deadline: float = float("inf")) -> None:
    with _Handle(_k.CreateEventW(None, True, False, None)) as event:
        overlapped = _OVERLAPPED()
        overlapped.hEvent = event.value
        if _k.ConnectNamedPipe(handle, ctypes.byref(overlapped)):
            return
        error = ctypes.get_last_error()
        if error == _ERROR_PIPE_CONNECTED:
            return
        if error != _ERROR_IO_PENDING:
            raise ctypes.WinError(error)
        _wait_io(handle, overlapped, deadline, stop_event)


def service_pid(name: str) -> int:
    _windows()
    manager = _a.OpenSCManagerW(None, None, 1)
    _check(manager)
    try:
        service = _a.OpenServiceW(manager, name, 4)
        _check(service)
        try:
            status, required = _SERVICE_STATUS_PROCESS(), w.DWORD()
            _check(_a.QueryServiceStatusEx(service, 0, ctypes.byref(status), ctypes.sizeof(status), ctypes.byref(required)))
            if not status.dwProcessId or status.dwCurrentState not in {2, 4}:
                raise PermissionError("Expected Foundation service is not running")
            return status.dwProcessId
        finally:
            _a.CloseServiceHandle(service)
    finally:
        _a.CloseServiceHandle(manager)


def pipe_request(pipe_name: str, request: dict, *, service_name: str,
                 timeout: float = 30, token: UserToken | None = None) -> dict:
    _windows()
    if not isinstance(request, dict) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("A request object and positive timeout are required")
    deadline = time.monotonic() + timeout
    context = _impersonate(token) if token is not None else contextlib.nullcontext()
    with context:
        while True:
            pipe = _k.CreateFileW(_pipe_name(pipe_name), _PIPE_RIGHTS, 0, None, 3,
                                 0x40000000 | 0x00100000 | 0x00020000, None)
            if pipe not in (None, 0, _INVALID):
                break
            error = ctypes.get_last_error()
            if error not in {2, 231} or time.monotonic() >= deadline:
                raise ctypes.WinError(error)
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        with _Handle(pipe) as owned:
            actual = w.ULONG()
            _check(_k.GetNamedPipeServerProcessId(owned.value, ctypes.byref(actual)))
            if actual.value != service_pid(service_name):
                raise PermissionError("Named pipe server PID differs from SCM service PID")
            _send(owned.value, request, deadline)
            response = _receive(owned.value, deadline)
            _send(owned.value, {"ack": True}, deadline)
            return response


def serve_pipe(pipe_name: str, *, service_name: str, allowed_user_sid: str,
               handler: Callable[[dict, WindowsProcessHost], dict], stop_event: threading.Event,
               diagnostic_path: Path | None = None, token_mode: str = "limited") -> None:
    _windows()
    service_sid = account_sid("NT SERVICE\\" + service_name)
    listener = _new_pipe(pipe_name, service_sid, allowed_user_sid, first=True)
    try:
        if diagnostic_path is not None:
            process_token = w.HANDLE()
            _check(_a.OpenProcessToken(_k.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(process_token)))
            with _Handle(process_token.value) as owned:
                diagnostic = {"service_pid": os.getpid(), "token": token_info(owned.value),
                              "pipe": pipe_name, "client_access_mask": _PIPE_RIGHTS,
                              "security": _pipe_security(listener.value),
                              "first_instance": True, "remote_clients_rejected": True}
            # Used only by the explicitly installed fixed proof mode. The parent
            # is created and ACL-protected by its installer, never by a client.
            with diagnostic_path.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(diagnostic, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        while not stop_event.is_set():
            try:
                _connect_server(listener.value, stop_event)
            except TimeoutError:
                if stop_event.is_set():
                    break
                raise
            accepted = listener
            # Reserve the next instance before releasing the accepted one. This
            # keeps pipe ownership stable even with a serial operation handler.
            listener = _new_pipe(pipe_name, service_sid, allowed_user_sid, first=False)
            with accepted:
                try:
                    request = _receive(accepted.value, time.monotonic() + 30)
                    with _capture_pipe_token(accepted.value, allowed_user_sid, token_mode=token_mode) as token:
                        with WindowsProcessHost(token) as host:
                            peer_pid = w.ULONG()
                            _check(_k.GetNamedPipeClientProcessId(accepted.value, ctypes.byref(peer_pid)))
                            host.peer = {"pid": peer_pid.value, "pipe_security": _pipe_security(accepted.value)}
                            result = handler(request, host)
                    if not isinstance(result, dict):
                        raise TypeError("Foundation handler must return an object")
                    response = {"ok": True, "result": result}
                except Exception as exc:
                    if diagnostic_path is not None:
                        # Private fixed proof output, never part of the IPC reply.
                        diagnostic["last_error"] = {"type": type(exc).__name__,
                                                    "message": str(exc),
                                                    "traceback": traceback.format_exc()}
                        with diagnostic_path.open("w", encoding="utf-8", newline="\n") as stream:
                            json.dump(diagnostic, stream, ensure_ascii=False, indent=2)
                            stream.write("\n")
                    response = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
                try:
                    _send(accepted.value, response, time.monotonic() + 10)
                    # Client confirms receipt, avoiding DisconnectNamedPipe
                    # discarding buffered response bytes. Never unbounded Flush.
                    acknowledgement = _receive(accepted.value, time.monotonic() + 2)
                    if acknowledgement != {"ack": True}:
                        raise ValueError("Invalid Foundation response acknowledgement")
                except (OSError, EOFError, ValueError, TimeoutError):
                    pass
                finally:
                    _k.DisconnectNamedPipe(accepted.value)
    finally:
        listener.close()


def serve_service(service_name: str, callback: Callable[[threading.Event], None]) -> None:
    _windows()
    stop_event = threading.Event()
    errors: list[BaseException] = []
    status_handle = None

    def report(state: int, error: int = 0):
        status = _SERVICE_STATUS(0x10, state, 1 | 4 if state == 4 else 0, error, 0,
                                 1 if state in {2, 3} else 0, 5000 if state in {2, 3} else 0)
        _check(_a.SetServiceStatus(status_handle, ctypes.byref(status)))

    @_SERVICE_HANDLER
    def control(code, _event_type, _event_data, _context):
        if code in {1, 5}:
            stop_event.set()
        return 0

    @_SERVICE_MAIN
    def main(_argc, _argv):
        nonlocal status_handle
        try:
            status_handle = _a.RegisterServiceCtrlHandlerExW(service_name, control, None)
            _check(status_handle)
            report(2)
            report(4)
            callback(stop_event)
            report(1)
        except BaseException as exc:
            errors.append(exc)
            if status_handle:
                try:
                    report(1, 1066)
                except OSError:
                    pass

    table = (_SERVICE_TABLE_ENTRY * 2)()
    table[0].lpServiceName, table[0].lpServiceProc = service_name, main
    _check(_a.StartServiceCtrlDispatcherW(table))
    if errors:
        raise RuntimeError("Foundation service failed") from errors[0]


def _relay_worker(name: str, expected_server_pid: int, token_mode: str = "limited") -> int:
    """Protected code, running as the installation's authenticated command owner."""
    if expected_server_pid <= 0:
        raise ValueError("Expected relay server PID is required")
    with current_user_token(limited=False, token_mode=token_mode):
        pass  # Validate the actual process identity before receiving a command.
    deadline = time.monotonic() + 20
    while True:
        handle = _k.CreateFileW(_pipe_name(name), _PIPE_RIGHTS, 0, None, 3,
                               0x40000000 | 0x00100000 | 0x00020000, None)
        if handle not in (None, 0, _INVALID):
            break
        error = ctypes.get_last_error()
        if error not in {2, 231} or time.monotonic() >= deadline:
            raise ctypes.WinError(error)
        time.sleep(0.05)
    with _Handle(handle) as pipe:
        actual = w.ULONG()
        _check(_k.GetNamedPipeServerProcessId(pipe.value, ctypes.byref(actual)))
        if actual.value != expected_server_pid:
            raise PermissionError("Log relay server differs from the launching process")
        _send(pipe.value, {"ready": True}, deadline)
        request = _receive(pipe.value, deadline)
        if set(request) != {"args", "cwd", "env", "desktop"}:
            raise ValueError("Invalid user command relay request")
        args, cwd, env, desktop = (request[key] for key in ("args", "cwd", "env", "desktop"))
        if (not isinstance(args, list) or not args
                or any(not isinstance(arg, str) or "\0" in arg for arg in args)
                or not isinstance(cwd, str) or not Path(cwd).is_absolute() or "\0" in cwd
                or not isinstance(env, dict) or type(desktop) is not bool
                or any(not isinstance(k, str) or not k or "=" in k or "\0" in k
                       or not isinstance(v, str) or "\0" in v for k, v in env.items())):
            raise ValueError("Invalid user command arguments or environment")
        child = None
        try:
            env = _normalize_environment(env)
            executable = args[0] if Path(args[0]).is_absolute() else shutil.which(args[0], path=env.get("PATH", ""))
            if executable is None:
                raise FileNotFoundError(f"Command not found in user PATH: {args[0]}")
            command: list[str] | str = [executable, *args[1:]]
            if Path(executable).suffix.lower() in {".cmd", ".bat"}:
                executable = str(Path(env.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "cmd.exe")
                command = subprocess.list2cmdline([executable, "/d", "/s", "/c"]) + ' "' + subprocess.list2cmdline(command) + '"'
            child = subprocess.Popen(command, executable=executable, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     close_fds=True, creationflags=0 if desktop else 0x08000000)
            _send(pipe.value, {"started": child.pid}, time.monotonic() + 10)
            while True:
                block = os.read(child.stdout.fileno(), 49152)
                if not block:
                    break
                _send(pipe.value, {"output": base64.b64encode(block).decode("ascii")}, time.monotonic() + 30)
            code = child.wait()
            _send(pipe.value, {"exit": code}, time.monotonic() + 10)
            if _receive(pipe.value, time.monotonic() + 10) != {"ack": True}:
                raise ValueError("Invalid log relay acknowledgement")
            return code
        except BaseException as exc:
            try:
                _send(pipe.value, {"error": f"{type(exc).__name__}: {str(exc)[:2000]}"}, time.monotonic() + 5)
            except (OSError, TimeoutError):
                pass
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(5)
            return 125
        finally:
            if child is not None and child.stdout is not None:
                child.stdout.close()


def _proof_service(service_name: str, allowed_sid: str) -> None:
    def handler(request: dict, host: WindowsProcessHost) -> dict:
        if service_name == "EliraFoundationProof" and request == {"operation": "lifecycle_proof"}:
            # Fixed administrator-installed test fixture, never a candidate path.
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from foundation_fixture import lifecycle_proof
            return lifecycle_proof(host)
        if service_name == "EliraFoundationProof" and request == {"operation": "relay_proof"}:
            target = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / service_name / "relay-proof.log"
            with target.open("wb") as log:
                host.run([sys.executable, "-I", "-S", "-c",
                          "import sys;sys.stdout.buffer.write('stdout:Привет\\n'.encode());sys.stdout.flush();sys.stderr.write('stderr:ok\\n')"],
                         cwd=Path(__file__).parent, log=log, timeout=15)
            captured = target.read_bytes()
            nonzero = None
            with target.open("ab") as log:
                try:
                    host.run([sys.executable, "-I", "-S", "-c", "raise SystemExit(7)"],
                             cwd=Path(__file__).parent, log=log, timeout=15)
                except subprocess.CalledProcessError as exc:
                    nonzero = exc.returncode
            timed_out = False
            with target.open("ab") as log:
                try:
                    host.run([sys.executable, "-I", "-S", "-c", "import time;time.sleep(30)"],
                             cwd=Path(__file__).parent, log=log, timeout=0.3)
                except subprocess.TimeoutExpired:
                    timed_out = True
            return {"stdout": captured.decode("utf-8"), "nonzero_exit": nonzero,
                    "timeout_observed": timed_out, "log_path": str(target)}
        if request != {"operation": "identity_launch"}:
            raise ValueError("Unknown fixed proof operation")
        host.proof_diagnostics = True
        current = w.HANDLE()
        _check(_a.OpenProcessToken(_k.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(current)))
        with _Handle(current.value) as owned:
            service_identity = token_info(owned.value)
        command = [sys.executable, "-I", "-S", "-c", "import time; time.sleep(1)"]
        with host.popen(command, cwd=Path(__file__).parent) as child:
            result = {"service": service_identity, "service_pid": os.getpid(), "client": host.token.info,
                      "pipe": host.peer,
                      "child": child.identity, "child_pid": child.pid,
                      "child_creation_identity": child.creation_identity, "child_image": child.image_path,
                      "child_security": child.security,
                      "host_token_default_dacl": _default_dacl_diagnostic(host.token.value),
                      "exit_code": child.wait(15)}
        result["comparisons"] = []
        for executable, arguments in (
                (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe",
                 ["/d", "/c", "exit", "0"]),
                (Path(r"C:\Program Files\Python310\python.exe"), ["-I", "-S", "-c", "pass"])):
            if not executable.is_file():
                result["comparisons"].append({"image": str(executable), "status": "not_installed"})
                continue
            with host.popen([str(executable), *arguments], cwd=executable.parent) as child:
                result["comparisons"].append({"image": child.image_path, "token": child.identity,
                                              "security": child.security,
                                              "exit_code": child.wait(15)})
        return result

    serve_service(service_name, lambda stop: serve_pipe(
        "\\\\.\\pipe\\" + service_name + "-proof", service_name=service_name,
        allowed_user_sid=allowed_sid, handler=handler, stop_event=stop,
        diagnostic_path=Path(os.environ.get("ProgramData", r"C:\ProgramData")) / service_name / "pipe-diagnostic.json"))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--proof-service":
        _proof_service(sys.argv[2], sys.argv[3])
    elif len(sys.argv) in {4, 5} and sys.argv[1] == "--worker":
        raise SystemExit(_relay_worker(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) == 5 else "limited"))
    else:
        raise SystemExit("Expected --proof-service SERVICE_NAME ALLOWED_USER_SID or --worker PIPE_NAME SERVER_PID")
