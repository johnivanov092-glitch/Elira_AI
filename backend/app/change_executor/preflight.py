"""Executor isolation preflight — VERIFIED at startup, fail-closed.

The executor refuses to run unless its runtime is really isolated from the main Elira user:
  * a mandatory `ELIRA_CHANGE_EXECUTOR_ROOT`, and the executor package (__file__) AND
    `sys.executable` (the venv python) must live INSIDE it — running from the main
    repo/backend or a shared venv fails closed;
  * the executor must NOT be inside a git working tree (a dev checkout);
  * the executor tree + its parents, the registry, the DB parent, and the key/known_hosts
    files + their parents must not be writable by any non-owner principal;
  * no sys.path entry may be writable by others (a route to shadow the frozen code).
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

import getpass
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
                  "ELIRA_CHANGE_IPC_TOKEN_FILE")
# Trusted writers are resolved to FULL domain\name by _safe_writer_names() from well-known
# SIDs. Administrators is unavoidably trusted (nearly every Windows file grants it), so it
# cannot be the boundary — the contract requires the main runtime to be non-elevated
# (module docstring: elevated admin = TCB).
# icacls right tokens that grant write (locale-independent, ASCII).
_WRITE_TOKENS = {"f", "m", "w", "wd", "ad", "wa", "wea", "d", "dc", "wo", "wdac", "ga"}
_WRITE_WORDS = ("modify", "full", "write", "append", "delete", "change permissions",
                "take ownership", "generic")
_safe_names_cache: set[str] | None = None


class PreflightError(RuntimeError):
    """Executor isolation could not be verified — refuse to run (fail-closed)."""


def _safe_writer_names() -> set[str]:
    """Trusted-writer FULL `domain\\name` identities: SYSTEM, Administrators, TrustedInstaller
    resolved from their well-known SIDs (locale-correct), plus the current OWNER's full name.
    Matched by full `domain\\name` — NEVER a bare leaf — so a space-containing group like
    `BUILTIN\\Hyper-V Administrators` (a non-TCB group) or a foreign account sharing a leaf
    (`OTHERDOMAIN\\Root`) is not mistaken for a safe writer. If SID resolution is unavailable
    the safe set is minimal → the check OVER-flags (fail-closed), never under-flags."""
    global _safe_names_cache
    if _safe_names_cache is not None:
        return _safe_names_cache
    names: set[str] = set()
    user = getpass.getuser()
    dom_env = str(os.environ.get("USERDOMAIN", "")).strip()
    if user:
        names.add((f"{dom_env}\\{user}" if dom_env else user).lower())
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            adv = ctypes.windll.advapi32
            wellknown = ("S-1-5-18", "S-1-5-32-544",   # SYSTEM, Administrators
                         "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")  # TrustedInstaller
            for sid_str in wellknown:
                psid = ctypes.c_void_p()
                if not adv.ConvertStringSidToSidW(ctypes.c_wchar_p(sid_str), ctypes.byref(psid)):
                    continue
                name = ctypes.create_unicode_buffer(256)
                dom = ctypes.create_unicode_buffer(256)
                cn, cd, use = wintypes.DWORD(256), wintypes.DWORD(256), wintypes.DWORD()
                if adv.LookupAccountSidW(None, psid, name, ctypes.byref(cn), dom,
                                         ctypes.byref(cd), ctypes.byref(use)):
                    full = f"{dom.value}\\{name.value}" if dom.value else name.value
                    names.add(full.lower())
        except Exception:  # noqa: BLE001
            pass
    _safe_names_cache = names
    return names


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


def _recursive_writable(root_dir: str) -> str | None:
    """The first path (dir OR file) under *root_dir* writable by a non-owner, else None.
    Per-FILE so a weak ACL on engine.py/_frozen.py is caught even when the dir is safe.
    Fails closed: an un-walkable tree returns the root (unsafe)."""
    try:
        for dirpath, _dirs, files in os.walk(root_dir):
            if _writable_by_others(dirpath):
                return dirpath
            for fn in files:
                fp = os.path.join(dirpath, fn)
                if _writable_by_others(fp):
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
    if root:
        if not _within(str(pkg_dir), root):
            problems.append(f"executor package {pkg_dir} is NOT inside ELIRA_CHANGE_EXECUTOR_ROOT {root}")
        if not _within(sys.executable, root):
            problems.append(f"sys.executable {sys.executable} is NOT inside ELIRA_CHANGE_EXECUTOR_ROOT {root}")
        off = _recursive_writable(root)                 # the WHOLE executor tree, per-file
        if off:
            problems.append(f"EXECUTOR_ROOT tree file/dir writable by others: {off}")
        for d in _tree_and_parents(root)[1:]:           # its parents (root itself walked above)
            if _writable_by_others(d):
                problems.append(f"EXECUTOR_ROOT parent writable by others: {d}")
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
        off = _recursive_writable(d)
        if off:
            problems.append(f"code/interpreter/venv file/dir writable by others: {off}")
    # The large base install is checked at DIRECTORY level; a fully executor-owned, read-only
    # base Python is a provisioning requirement (recursing all of stdlib per-file is impractical).
    for d in dict.fromkeys([sys.base_prefix, str(Path(sys.base_prefix) / "Lib")]):
        if d and _writable_by_others(d):
            problems.append(f"base interpreter dir writable by others: {d}")

    to_check: list[str] = []
    for env in ("ELIRA_CHANGE_STORE_PATH", "ELIRA_CHANGE_REGISTRY_PATH", "ELIRA_CHANGE_IPC_TOKEN_FILE"):
        p = str(os.environ.get(env, "")).strip()
        if p:
            to_check += [p, str(Path(p).resolve().parent)]   # file (if present) + parent dir
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
                to_check += [p, str(Path(p).resolve().parent)]
    for p in dict.fromkeys(to_check):      # de-dup, preserve order
        if os.path.exists(p) and _writable_by_others(p):
            problems.append(f"path writable by others: {p}")

    # sys.path is a code-shadow surface. Walk each entry PER-FILE (like the executor tree),
    # except the large base stdlib which is dir-level only. Resolve '' / relative entries to
    # cwd so an attacker-writable cwd on sys.path (e.g. `python -m`) can't be skipped.
    base = str(Path(sys.base_prefix).resolve())
    for entry in sys.path:
        e = str(entry or "").strip() or os.getcwd()
        if not os.path.isdir(e):
            continue
        if str(Path(e).resolve()).startswith(base):     # base stdlib → dir-level (per-file impractical)
            if _writable_by_others(e):
                problems.append(f"sys.path base dir writable by others: {e}")
        else:
            off = _recursive_writable(e)                 # cwd / site-packages / script dir → per-file
            if off:
                problems.append(f"sys.path entry file/dir writable by others (code-shadow risk): {off}")
    return problems


def require_isolated(*, registry_path: str | None = None) -> None:
    """Raise PreflightError unless the executor is verifiably isolated."""
    problems = verify(registry_path=registry_path)
    if problems:
        raise PreflightError("executor isolation preflight failed: " + "; ".join(problems))
