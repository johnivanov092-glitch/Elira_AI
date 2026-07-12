"""Executor isolation preflight — VERIFIED at startup, fail-closed.

The executor refuses to run unless its runtime is really isolated from the main Elira user:
  * a mandatory `ELIRA_CHANGE_EXECUTOR_ROOT`, and the executor package (__file__) AND
    `sys.executable` (the venv python) must live INSIDE it — running from the main
    repo/backend or a shared venv fails closed;
  * the executor must NOT be inside a git working tree (a dev checkout);
  * every executor-owned sensitive path (store, registry, IPC token file, each target's
    known_hosts + identity key) must live INSIDE the executor root — so the recursive root
    check covers their whole ancestor chain — and none may be writable by a non-owner;
  * no sys.path entry may be writable by others (a route to shadow the frozen code),
    including importable zip/egg FILES on sys.path, with path-aware base containment;
  * the executor must be OWNED by and RUNNING AS the configured `ELIRA_CHANGE_EXECUTOR_ACCOUNT`
    (SID-based, not env/`getpass`). Ownership is verified PER-FILE across the whole recursive
    walk (root tree, venv, interpreter, sys.path), because on Windows an owner keeps implicit
    WRITE_DAC even with a clean DACL — so a tree owned by the main user (who could then rewrite
    any DACL and overwrite the frozen code) fails closed. Provisioning must
    `icacls <root> /setowner <account> /T` and run the executor AS that account.
`_writable_by_others` fails CLOSED — if it cannot prove a path is safe, it is unsafe.

TCB / trust assumptions (must hold operationally):
  * The main Elira runtime (and thus run_bash) must NOT be elevated and must have no write
    to the executor tree/venv/interpreter via its effective token. An elevated human/admin
    is deliberately treated as part of the TCB (Administrators is a trusted writer below —
    on Windows practically every file grants Administrators, so it cannot be the boundary).
    If run_bash could run elevated, the DACL is not a boundary; the strong guarantee then
    requires the executor on a SEPARATE host/VM.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from . import approvals
from ._frozen import decode_console
from .registry import RegistryError, load_registry

_REQUIRED_ENVS = ("ELIRA_CHANGE_STORE_PATH", "ELIRA_CHANGE_REGISTRY_PATH",
                  "ELIRA_CHANGE_BOT_TOKEN", "ELIRA_CHANGE_EXECUTOR_ROOT",
                  "ELIRA_CHANGE_IPC_TOKEN_FILE", "ELIRA_CHANGE_EXECUTOR_ACCOUNT")
# Trusted writers are resolved to FULL domain\name by _safe_writer_names() from well-known
# SIDs. Administrators is unavoidably trusted (nearly every Windows file grants it), so it
# cannot be the boundary — the contract requires the main runtime to be non-elevated
# (module docstring: elevated admin = TCB).
# icacls right tokens that grant write (locale-independent, ASCII).
_WRITE_TOKENS = {"f", "m", "w", "wd", "ad", "wa", "wea", "d", "dc", "wo", "wdac", "ga"}
_WRITE_WORDS = ("modify", "full", "write", "append", "delete", "change permissions",
                "take ownership", "generic")
# The canonical SID strings of the system principals that may own/write TCB paths. SYSTEM and
# Administrators are unavoidable owners on Windows; the contract requires the main runtime to
# be non-elevated (docstring: elevated admin = TCB). TrustedInstaller owns much of the OS.
_SYSTEM_SIDS = frozenset({
    "S-1-5-18",           # NT AUTHORITY\SYSTEM
    "S-1-5-32-544",       # BUILTIN\Administrators
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
})
_system_names_cache: set[str] | None = None
_safe_names_by_account: dict[str, set[str]] = {}
_account_sid_cache: dict[str, str | None] = {}


class PreflightError(RuntimeError):
    """Executor isolation could not be verified — refuse to run (fail-closed)."""


# --------------------------------------------------------------------------------------------
# Windows identity (SID-based). Names and env (USERNAME/USERDOMAIN, getpass) are spoofable and
# would trust "whoever ran preflight"; owner/runner identity is resolved to canonical SIDs so a
# preflight run by the WRONG principal (e.g. the main user) cannot produce a false green.
# --------------------------------------------------------------------------------------------
def _win():
    import ctypes
    from ctypes import wintypes
    return ctypes, wintypes, ctypes.windll.advapi32, ctypes.windll.kernel32


def _to_sid_string(psid) -> str | None:
    try:
        ctypes, wintypes, adv, k32 = _win()
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        out = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            return None
        try:
            return (out.value or "").upper() or None
        finally:
            k32.LocalFree(out)
    except Exception:  # noqa: BLE001
        return None


def _sid_to_name(sid_str: str) -> str | None:
    """Canonical SID string → lowercased full `domain\\name` (for DACL matching)."""
    try:
        ctypes, wintypes, adv, k32 = _win()
        adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        adv.ConvertStringSidToSidW.restype = wintypes.BOOL
        psid = ctypes.c_void_p()
        if not adv.ConvertStringSidToSidW(sid_str, ctypes.byref(psid)):
            return None
        try:
            name = ctypes.create_unicode_buffer(256)
            dom = ctypes.create_unicode_buffer(256)
            cn, cd, use = wintypes.DWORD(256), wintypes.DWORD(256), wintypes.DWORD()
            if adv.LookupAccountSidW(None, psid, name, ctypes.byref(cn), dom,
                                     ctypes.byref(cd), ctypes.byref(use)):
                return (f"{dom.value}\\{name.value}" if dom.value else name.value).lower()
            return None
        finally:
            k32.LocalFree(psid)
    except Exception:  # noqa: BLE001
        return None


def _account_sid(account: str) -> str | None:
    """Resolve a `DOMAIN\\name` OR a raw `S-1-...` string to a canonical SID string (None on
    failure). Cached per input for the process lifetime."""
    account = (account or "").strip()
    if not account:
        return None
    if account in _account_sid_cache:
        return _account_sid_cache[account]
    result: str | None = None
    try:
        ctypes, wintypes, adv, k32 = _win()
        if account.upper().startswith("S-1-"):
            adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
            adv.ConvertStringSidToSidW.restype = wintypes.BOOL
            psid = ctypes.c_void_p()
            if adv.ConvertStringSidToSidW(account, ctypes.byref(psid)):
                try:
                    result = _to_sid_string(psid)
                finally:
                    k32.LocalFree(psid)
        else:
            adv.LookupAccountNameW.argtypes = [
                wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
            adv.LookupAccountNameW.restype = wintypes.BOOL
            cb, cd, use = wintypes.DWORD(0), wintypes.DWORD(0), wintypes.DWORD()
            adv.LookupAccountNameW(None, account, None, ctypes.byref(cb), None,
                                   ctypes.byref(cd), ctypes.byref(use))     # size query
            if cb.value:
                sid_buf = (ctypes.c_byte * cb.value)()
                dom_buf = ctypes.create_unicode_buffer(max(cd.value, 1))
                if adv.LookupAccountNameW(None, account, sid_buf, ctypes.byref(cb),
                                          dom_buf, ctypes.byref(cd), ctypes.byref(use)):
                    result = _to_sid_string(ctypes.cast(sid_buf, ctypes.c_void_p))
    except Exception:  # noqa: BLE001
        result = None
    _account_sid_cache[account] = result
    return result


def _current_sid() -> str | None:
    """SID string of the CURRENT process token's user — how we prove we run AS the expected
    account rather than trusting an env-derived username."""
    try:
        ctypes, wintypes, adv, k32 = _win()
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        adv.OpenProcessToken.restype = wintypes.BOOL
        adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        adv.GetTokenInformation.restype = wintypes.BOOL
        TOKEN_QUERY, TokenUser = 0x0008, 1
        tok = wintypes.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            n = wintypes.DWORD(0)
            adv.GetTokenInformation(tok, TokenUser, None, 0, ctypes.byref(n))   # size query
            if not n.value:
                return None
            buf = (ctypes.c_byte * n.value)()
            if not adv.GetTokenInformation(tok, TokenUser, buf, n, ctypes.byref(n)):
                return None
            # TOKEN_USER = { SID_AND_ATTRIBUTES User } ; User.Sid is the first pointer-sized field
            psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            return _to_sid_string(ctypes.c_void_p(psid))
        finally:
            k32.CloseHandle(tok)
    except Exception:  # noqa: BLE001
        return None


def _owner_sid(path: str) -> str | None:
    """SID string of *path*'s OWNER. The owner can rewrite a DACL regardless of current ACEs,
    so an owner that is not the executor account is unsafe even with no write ACE present."""
    try:
        ctypes, wintypes, adv, k32 = _win()
        adv.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p)]
        adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
        SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION = 1, 0x00000001
        owner, psd = ctypes.c_void_p(), ctypes.c_void_p()
        if adv.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                     ctypes.byref(owner), None, None, None, ctypes.byref(psd)) != 0:
            return None
        try:
            return _to_sid_string(owner)
        finally:
            if psd:
                k32.LocalFree(psd)
    except Exception:  # noqa: BLE001
        return None


def _system_safe_names() -> set[str]:
    """SID-resolved FULL `domain\\name` for SYSTEM/Administrators/TrustedInstaller (cached)."""
    global _system_names_cache
    if _system_names_cache is not None:
        return _system_names_cache
    names: set[str] = set()
    if os.name == "nt":
        for sid in _SYSTEM_SIDS:
            n = _sid_to_name(sid)
            if n:
                names.add(n)
    _system_names_cache = names
    return names


def _expected_account_name() -> str | None:
    """The configured executor account as a lowercased full `domain\\name` (resolving a raw
    SID form if that is how it was configured)."""
    account = str(os.environ.get("ELIRA_CHANGE_EXECUTOR_ACCOUNT", "")).strip()
    if not account:
        return None
    if os.name == "nt" and account.upper().startswith("S-1-"):
        return _sid_to_name(account)
    return account.lower()


def _safe_writer_names() -> set[str]:
    """Trusted-writer FULL `domain\\name` identities: SYSTEM/Administrators/TrustedInstaller
    (SID-resolved) PLUS the configured `ELIRA_CHANGE_EXECUTOR_ACCOUNT` — deliberately NOT
    `getpass.getuser()` (env-derived, spoofable, and it would trust whoever ran preflight).
    Matched by full `domain\\name` — never a bare leaf. Account unset/unresolvable ⇒ system-only
    set ⇒ the check OVER-flags (fail-closed)."""
    acct = str(os.environ.get("ELIRA_CHANGE_EXECUTOR_ACCOUNT", "")).strip()
    if acct in _safe_names_by_account:
        return _safe_names_by_account[acct]
    names = set(_system_safe_names())
    full = _expected_account_name()
    if full:
        names.add(full)
    _safe_names_by_account[acct] = names
    return names


def _identity_problems(root: str, sensitive_paths: list[str]) -> list[str]:
    """Verify the executor is OWNED by and RUNNING AS the configured
    `ELIRA_CHANGE_EXECUTOR_ACCOUNT` — not merely "whoever ran preflight". A path owned by the
    main user can have its DACL rewritten by that user at will, so an owner mismatch is unsafe
    even when no write ACE is present, and a preflight run by the wrong principal must not go
    green. SID-based; fails closed on any inability to determine identity. This checks running-as
    plus the owner of the key roots + sensitive files; EVERY file's owner is additionally verified
    per-file by the recursive walk (`_recursive_writable(..., owner_check=...)`), because on
    Windows an owner keeps implicit WRITE_DAC even with a clean DACL — so ownership, not the DACL,
    is the real write boundary. Provisioning must `icacls <root> /setowner <account> /T`."""
    account = str(os.environ.get("ELIRA_CHANGE_EXECUTOR_ACCOUNT", "")).strip()
    if not account:
        return []   # missing-env already reported by the required-envs loop
    problems: list[str] = []
    roots = [root, str(Path(__file__).resolve().parent), sys.executable, sys.prefix]
    if os.name == "nt":
        expected = _account_sid(account)
        if not expected:
            return [f"ELIRA_CHANGE_EXECUTOR_ACCOUNT {account!r} could not be resolved to a SID"]
        cur = _current_sid()
        if cur is None:
            problems.append("could not determine the current process SID (cannot prove running-as)")
        elif cur != expected:
            problems.append("executor is NOT running as ELIRA_CHANGE_EXECUTOR_ACCOUNT — a run by "
                            "any other principal (incl. the main user) would be a false green")
        trusted = {expected} | set(_SYSTEM_SIDS)
        seen: set[str] = set()
        for p in [*roots, *sensitive_paths]:
            rp = os.path.normcase(os.path.abspath(p)) if p else ""
            if not rp or rp in seen or not os.path.exists(p):
                continue
            seen.add(rp)
            owner = _owner_sid(p)
            if owner is None:
                problems.append(f"could not read owner SID of {p} (cannot verify ownership)")
            elif owner not in trusted:
                problems.append(f"path OWNED by a non-executor principal (owner can rewrite its DACL): {p}")
        return problems
    # POSIX: uid-based owner + euid running-as.
    try:
        import pwd
        expected_uid = pwd.getpwnam(account).pw_uid
    except (KeyError, ImportError):
        return [f"ELIRA_CHANGE_EXECUTOR_ACCOUNT {account!r} is not a known local account"]
    if hasattr(os, "geteuid") and os.geteuid() != expected_uid:
        problems.append("executor is NOT running as ELIRA_CHANGE_EXECUTOR_ACCOUNT")
    seen = set()
    for p in [*roots, *sensitive_paths]:
        if not p or p in seen or not os.path.exists(p):
            continue
        seen.add(p)
        try:
            if os.stat(p).st_uid not in (expected_uid, 0):   # 0 = root (system)
                problems.append(f"path OWNED by a non-executor principal: {p}")
        except OSError:
            problems.append(f"could not stat {p} (cannot verify ownership)")
    return problems


def _make_owner_check():
    """Return a callable `(path) -> bool` that is True when *path*'s OWNER is NOT the executor
    account (or a system principal) — for per-file ownership verification inside the recursive
    walk. On Windows the owner holds implicit WRITE_DAC regardless of the DACL, so this is the
    real write boundary and must be checked on every file, not just a few roots. Returns None
    (no owner check) when the account is unset/unresolvable — running-as already reports that.
    Fails closed: an unreadable owner counts as untrusted."""
    account = str(os.environ.get("ELIRA_CHANGE_EXECUTOR_ACCOUNT", "")).strip()
    if not account:
        return None
    if os.name == "nt":
        expected = _account_sid(account)
        if not expected:
            return None      # unresolvable account is reported by _identity_problems
        trusted = {expected} | set(_SYSTEM_SIDS)

        def _chk(p: str) -> bool:
            o = _owner_sid(p)
            return o is None or o not in trusted
        return _chk
    try:
        import pwd
        trusted_uids = {pwd.getpwnam(account).pw_uid, 0}
    except (KeyError, ImportError):
        return None

    def _chk_posix(p: str) -> bool:
        try:
            return os.stat(p).st_uid not in trusted_uids
        except OSError:
            return True
    return _chk_posix


def _ace_grants_write_to_nonowner(line: str, safe: set[str], path: str = "") -> bool:
    """Parse ONE icacls ACE line. True iff a NON-safe principal is granted (not denied) a
    write-bearing right. The principal is matched as a FULL `domain\\name` (icacls' leading
    path on the first line is stripped, spaces preserved) — never a bare leaf — and rights
    are split per parenthesized group so combined masks like `(RX,W)`/`(I)(RX,WD)` are caught."""
    idx = line.find(":(")
    if idx <= 0:
        return False
    account_part = line[:idx]
    if path and account_part.startswith(path):          # icacls line 1 = "<path> <account>"
        account_part = account_part[len(path):]
    account = account_part.strip().lower()              # full domain\name (may contain spaces)
    if not account or account in safe:
        return False
    groups = re.findall(r"\(([^)]*)\)", line[idx:])
    if any(g.strip().lower() == "deny" for g in groups):
        return False                                    # a DENY ACE grants nothing
    tokens = {tok.strip().lower() for g in groups for tok in g.split(",")}
    joined = " ".join(groups).lower()
    return bool(tokens & _WRITE_TOKENS) or any(w in joined for w in _WRITE_WORDS)


def _writable_by_others(path: str) -> bool:
    """True if any principal other than the owner (or SYSTEM/Administrators) can write
    *path*. Fails CLOSED — any inability to determine returns True (unsafe)."""
    try:
        if not os.path.exists(path):
            return False
        if os.name != "nt":
            return bool(os.stat(path).st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        out = subprocess.run(["icacls", path], capture_output=True, timeout=10)
        if out.returncode != 0:
            return True
        safe = _safe_writer_names()
        for line in decode_console(out.stdout).splitlines():
            if _ace_grants_write_to_nonowner(line, safe, path):
                return True
        return False
    except Exception:  # noqa: BLE001
        return True


def _within(child: str, parent: str) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def _recursive_writable(root_dir: str, owner_check=None) -> str | None:
    """The first path (dir OR file) under *root_dir* writable by a non-owner, OWNED by a
    non-executor principal (when *owner_check* is given), OR a directory symlink whose target
    escapes *root_dir* (a code-shadow route os.walk does not descend into) — else None. Per-FILE
    so a weak ACL OR a wrong owner on engine.py/_frozen.py is caught even when the dir is safe:
    on Windows the owner keeps implicit WRITE_DAC regardless of the DACL, so ownership is the
    real write boundary and must be verified on every file, not just a few roots. os.walk does
    NOT descend into directory SYMLINKS (followlinks defaults False), so a symlinked subdir is
    treated as unsafe if it escapes root. (Junctions are not islink and ARE descended.)
    Fails closed: an un-walkable tree returns the root (unsafe)."""
    try:
        root_real = os.path.realpath(root_dir)
        for dirpath, dirs, files in os.walk(root_dir):
            if _writable_by_others(dirpath) or (owner_check and owner_check(dirpath)):
                return dirpath
            for dn in dirs:                              # os.walk lists but won't descend symlinked dirs
                dp = os.path.join(dirpath, dn)
                if os.path.islink(dp) and (
                        not _within(os.path.realpath(dp), root_real) or _writable_by_others(dp)):
                    return dp
            for fn in files:
                fp = os.path.join(dirpath, fn)
                if _writable_by_others(fp) or (owner_check and owner_check(fp)):
                    return fp
    except OSError:
        return root_dir
    return None


def _has_git_ancestor(path: Path) -> bool:
    try:
        p = path.resolve()
    except OSError:
        return True     # can't resolve → treat as unsafe
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return True
    return False


def _tree_and_parents(path: str) -> list[str]:
    try:
        p = Path(path).resolve()
    except OSError:
        return [path]
    parents = list(p.parents)
    return [str(p)] + [str(d) for d in parents[:-1]]   # drop the drive anchor


def verify(*, registry_path: str | None = None) -> list[str]:
    """Return a list of isolation problems (empty ⇒ verifiably isolated)."""
    problems: list[str] = []
    for env in _REQUIRED_ENVS:
        if not str(os.environ.get(env, "")).strip():
            problems.append(f"env {env} not set")
    users, chats = approvals.approver_allowlists()
    if not users:
        problems.append("ITOPS_CHANGE_APPROVER_USER_IDS empty or malformed")
    if not chats:
        problems.append("ITOPS_CHANGE_APPROVER_CHAT_IDS empty or malformed")

    pkg_dir = Path(__file__).resolve().parent
    root = str(os.environ.get("ELIRA_CHANGE_EXECUTOR_ROOT", "")).strip()
    owner_check = _make_owner_check()      # per-file owner verification (Windows implicit WRITE_DAC)
    if root:
        if not _within(str(pkg_dir), root):
            problems.append(f"executor package {pkg_dir} is NOT inside ELIRA_CHANGE_EXECUTOR_ROOT {root}")
        if not _within(sys.executable, root):
            problems.append(f"sys.executable {sys.executable} is NOT inside ELIRA_CHANGE_EXECUTOR_ROOT {root}")
        off = _recursive_writable(root, owner_check)    # the WHOLE executor tree, per-file (ACL + owner)
        if off:
            problems.append(f"EXECUTOR_ROOT tree file/dir writable-or-misowned: {off}")
        for d in _tree_and_parents(root)[1:]:           # its parents (root itself walked above)
            if _writable_by_others(d):
                problems.append(f"EXECUTOR_ROOT parent writable by others: {d}")
            if owner_check and os.path.exists(d) and owner_check(d):   # a misowned parent can swap the subtree
                problems.append(f"EXECUTOR_ROOT parent owned by a non-executor principal: {d}")
    if _has_git_ancestor(pkg_dir):
        problems.append(f"executor is running from a git working tree (dev checkout): {pkg_dir}")
    # Recursive ACL over the executor CODE, the interpreter dir, and the venv (per-file, so a
    # weak ACL on engine.py/_frozen.py, python.exe, a venv DLL, or a site-packages .pth can't
    # slip through even when the containing dir is safe).
    if str(Path(sys.prefix).resolve()) == str(Path(sys.base_prefix).resolve()):
        problems.append("executor is not running in a dedicated venv (sys.prefix == base_prefix)")
    if _writable_by_others(sys.executable):
        problems.append(f"interpreter writable by others: {sys.executable}")
    exe_dir = str(Path(sys.executable).resolve().parent)
    for d in dict.fromkeys([str(pkg_dir), exe_dir, sys.prefix]):
        off = _recursive_writable(d, owner_check)
        if off:
            problems.append(f"code/interpreter/venv file/dir writable-or-misowned: {off}")
    # The base runtime must be proven immutable to non-executor principals: a single Lib\*.py
    # with a MAINUSER:(W) ACE (clean dir ACL, correct owner) is a shadow-import route. When the
    # account is configured (a real run) verify the base PER-FILE — owner AND writability — via the
    # same walk used for the package/venv. Gated on owner_check so the test suite (account unset)
    # keeps the cheap directory-level path; a full per-file walk of a clean base is a one-time
    # startup cost for a rarely-restarted privileged service (base = dedicated, read-only install).
    if owner_check:
        off = _recursive_writable(sys.base_prefix, owner_check)
        if off:
            problems.append(f"base runtime file/dir writable-or-misowned: {off}")
        for d in _tree_and_parents(sys.base_prefix)[1:]:   # a misowned/writable ancestor can swap the base
            if _writable_by_others(d):
                problems.append(f"base runtime parent writable by others: {d}")
            if os.path.exists(d) and owner_check(d):
                problems.append(f"base runtime parent owned by a non-executor principal: {d}")
    else:
        for d in dict.fromkeys([sys.base_prefix, str(Path(sys.base_prefix) / "Lib")]):
            if d and _writable_by_others(d):
                problems.append(f"base interpreter dir writable by others: {d}")

    # Every executor-owned sensitive path (store, registry, IPC token file, and each target's
    # known_hosts + identity KEY) MUST live INSIDE ELIRA_CHANGE_EXECUTOR_ROOT. The recursive
    # root check above (tree + parents) then covers their ENTIRE ancestor chain — a sensitive
    # path placed outside root would leave a writable ancestor able to swap the subtree, and
    # checking only the immediate parent (as before) does not catch a writable grandparent.
    # Requiring containment is strictly stronger than walking each path's full parent chain.
    sensitive: list[tuple[str, str]] = []
    for env in ("ELIRA_CHANGE_STORE_PATH", "ELIRA_CHANGE_REGISTRY_PATH", "ELIRA_CHANGE_IPC_TOKEN_FILE"):
        p = str(os.environ.get(env, "")).strip()
        if p:
            sensitive.append((env, p))
    try:
        reg = load_registry(registry_path)
    except RegistryError as exc:
        problems.append(f"registry invalid: {exc}")
        reg = {}
    for tid, t in reg.items():
        for label, p in (("known_hosts", t.known_hosts), ("identity_file", t.identity_file)):
            if not os.path.isfile(p):
                problems.append(f"target {tid}: {label} missing: {p}")
            else:
                sensitive.append((f"target {tid} {label}", p))
    to_check: list[str] = []
    for label, p in sensitive:
        if root and not _within(p, root):
            problems.append(f"{label} path is NOT inside ELIRA_CHANGE_EXECUTOR_ROOT: {p}")
        to_check += [p, str(Path(p).resolve().parent)]   # file (if present) + parent dir (belt-and-suspenders)
    for p in dict.fromkeys(to_check):      # de-dup, preserve order
        if os.path.exists(p) and _writable_by_others(p):
            problems.append(f"path writable by others: {p}")

    # OWNERSHIP + RUNNING-AS: the executor must be owned by and running AS the configured
    # ELIRA_CHANGE_EXECUTOR_ACCOUNT (SID-based) — not "whoever ran preflight". This closes the
    # false green where a deploy/verify run by the main user leaves the main user as owner
    # (able to rewrite any DACL) and trusted.
    if root:
        problems += _identity_problems(root, [p for _label, p in sensitive])

    # sys.path is a code-shadow surface. Resolve '' / relative entries to cwd so an
    # attacker-writable cwd on sys.path (e.g. `python -m`) can't be skipped. Containment in
    # the base install is PATH-AWARE (`_within`, not startswith — else `C:\Python\base_evil`
    # would masquerade as `C:\Python\base` and skip the per-file walk). Directories outside
    # base are walked per-file; IMPORTABLE FILES on sys.path (zip/egg) are checked per-file
    # too — skipping non-directories would let a writable importable archive shadow code.
    base = str(Path(sys.base_prefix).resolve())
    for entry in sys.path:
        e = str(entry or "").strip() or os.getcwd()
        try:
            resolved = str(Path(e).resolve())
        except OSError:
            problems.append(f"sys.path entry unresolvable (code-shadow risk): {e}")
            continue
        in_base = _within(resolved, base)
        if os.path.isdir(e):
            if in_base:                                  # base stdlib → dir-level (per-file impractical)
                if _writable_by_others(e) or (owner_check and owner_check(e)):
                    problems.append(f"sys.path base dir writable-or-misowned: {e}")
            else:
                off = _recursive_writable(e, owner_check)   # cwd / site-packages / script dir → per-file
                if off:
                    problems.append(f"sys.path entry file/dir writable-or-misowned (code-shadow risk): {off}")
        elif os.path.exists(e):                          # importable zip/egg archive on sys.path
            if _writable_by_others(e) or (owner_check and owner_check(e)):
                problems.append(f"sys.path importable file writable-or-misowned (code-shadow risk): {e}")
        # a non-existent entry imports nothing; Python skips it (no check).
    return problems


def require_isolated(*, registry_path: str | None = None) -> None:
    """Raise PreflightError unless the executor is verifiably isolated."""
    problems = verify(registry_path=registry_path)
    if problems:
        raise PreflightError("executor isolation preflight failed: " + "; ".join(problems))
