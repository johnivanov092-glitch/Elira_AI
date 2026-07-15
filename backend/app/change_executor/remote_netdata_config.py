#!/usr/bin/python3
"""Root-owned remote helper for one typed Netdata configuration change.

This file is copied verbatim to `/usr/local/sbin/elira-netdata-config` and pinned by
SHA-256 in the executor-owned registry. It accepts no path, section, key, value, unit, or
command from the caller. The only mutation is:

    /etc/netdata/netdata.conf :: [global] update every = 1

`apply` performs a hash-CAS, durable snapshot, atomic replace, service restart and
post-check. A definite post-check failure triggers one compensating rollback. Transport
loss remains unknown to the Windows executor and is resolved by a later typed inspect.
The helper never emits raw config or command output.
"""
from __future__ import annotations

import argparse
import configparser
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator

PROTOCOL_VERSION = 1
CONFIG_ID = "netdata-main"
CONFIG_PATH = Path("/etc/netdata/netdata.conf")
STATE_DIR = Path("/var/lib/elira-change/netdata")
SYSTEMCTL = "/usr/bin/systemctl"
UNIT = "netdata.service"
MAX_BYTES = 256 * 1024
DESIRED_UPDATE_EVERY = 1
POSTCHECK_ATTEMPTS = 6
POSTCHECK_INTERVAL = 2.0
SERVICE_INSPECT_TIMEOUT = 15
RESTART_TIMEOUT = 30

_RUN_ID_RE = re.compile(r"^chg-[0-9a-f]{32}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*(?:[#;].*)?$")
_UPDATE_RE = re.compile(r"^(\s*)update\s+every\s*=", re.IGNORECASE)
_SHOW_PROPS = "Id,LoadState,ActiveState,SubState,MainPID"


class HelperError(RuntimeError):
    """A bounded, safe-to-report helper failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _self_sha256() -> str:
    try:
        return _sha(Path(__file__).read_bytes())
    except OSError as exc:
        raise HelperError("helper_hash_unavailable") from exc


def _read_config() -> tuple[bytes, os.stat_result]:
    try:
        st = os.lstat(CONFIG_PATH)
        if not stat.S_ISREG(st.st_mode):
            raise HelperError("config_not_regular")
        if hasattr(os, "geteuid") and (st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise HelperError("config_ownership_or_mode_unsafe")
        data = CONFIG_PATH.read_bytes()
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError("config_unreadable") from exc
    if len(data) > MAX_BYTES:
        raise HelperError("config_too_large")
    if data.startswith(b"\xef\xbb\xbf"):
        raise HelperError("config_bom_not_allowed")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HelperError("config_not_utf8") from exc
    return data, st


def _parser(data: bytes) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str.lower
    try:
        parser.read_string(data.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise HelperError("config_parse_failed") from exc
    return parser


def _comment_only(data: bytes) -> bool:
    for line in data.decode("utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", ";")):
            return False
    return True


def _safe_settings(parser: configparser.ConfigParser) -> dict[str, int]:
    if not parser.has_option("global", "update every"):
        return {}
    try:
        value = parser.getint("global", "update every")
    except (ValueError, configparser.Error) as exc:
        raise HelperError("update_every_not_integer") from exc
    if not 1 <= value <= 60:
        raise HelperError("update_every_out_of_range")
    return {"global.update_every": value}


def _patched(data: bytes) -> bytes:
    """Preserve unrelated bytes/comments while setting the one typed key."""
    _parser(data)  # fail closed before the line-preserving edit
    text = data.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    global_start: int | None = None
    global_end = len(lines)
    for i, line in enumerate(lines):
        match = _SECTION_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        if global_start is not None:
            global_end = i
            break
        if match.group(1).strip().lower() == "global":
            global_start = i

    if global_start is None:
        suffix = "" if not text or text.endswith(("\n", "\r")) else newline
        out = text + suffix + f"[global]{newline}update every = {DESIRED_UPDATE_EVERY}{newline}"
    else:
        found: int | None = None
        indent = ""
        for i in range(global_start + 1, global_end):
            match = _UPDATE_RE.match(lines[i].rstrip("\r\n"))
            if match:
                found, indent = i, match.group(1)
                break
        replacement = f"{indent}update every = {DESIRED_UPDATE_EVERY}{newline}"
        if found is not None:
            lines[found] = replacement
        else:
            if global_end > 0 and not lines[global_end - 1].endswith(("\n", "\r")):
                lines[global_end - 1] += newline
            lines.insert(global_end, replacement)
        out = "".join(lines)

    encoded = out.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise HelperError("planned_config_too_large")
    parsed = _parser(encoded)
    if _safe_settings(parsed).get("global.update_every") != DESIRED_UPDATE_EVERY:
        raise HelperError("planned_config_validation_failed")
    return encoded


def _service_fields(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict[str, Any]:
    try:
        proc = run([SYSTEMCTL, "show", "-p", _SHOW_PROPS, UNIT],
                   capture_output=True, timeout=SERVICE_INSPECT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError("service_inspect_failed") from exc
    if proc.returncode != 0:
        raise HelperError("service_inspect_nonzero")
    try:
        text = proc.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HelperError("service_inspect_decode_failed") from exc
    raw: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            raw[key] = value
    try:
        pid = int(raw.get("MainPID", ""))
    except ValueError as exc:
        raise HelperError("service_inspect_bad_pid") from exc
    fields: dict[str, Any] = {
        "id": raw.get("Id", ""),
        "load_state": raw.get("LoadState", ""),
        "active_state": raw.get("ActiveState", ""),
        "sub_state": raw.get("SubState", ""),
        "main_pid": pid,
    }
    if not all(isinstance(fields[k], str) and fields[k] for k in
               ("id", "load_state", "active_state", "sub_state")):
        raise HelperError("service_inspect_incomplete")
    if fields["id"] != UNIT:
        raise HelperError("service_unit_mismatch")
    return fields


def _healthy(fields: dict[str, Any]) -> bool:
    return (fields.get("id") == UNIT and fields.get("load_state") == "loaded"
            and fields.get("active_state") == "active" and fields.get("sub_state") == "running"
            and isinstance(fields.get("main_pid"), int) and int(fields["main_pid"]) > 0)


def inspect(*, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict[str, Any]:
    data, _st = _read_config()
    parser = _parser(data)
    planned = _patched(data)
    return {
        "protocol": PROTOCOL_VERSION,
        "helper_sha256": _self_sha256(),
        "status": "ok",
        "config_id": CONFIG_ID,
        "path": str(CONFIG_PATH),
        "before_sha256": _sha(data),
        "planned_sha256": _sha(planned),
        "size_bytes": len(data),
        "comment_only": _comment_only(data),
        "safe_settings": _safe_settings(parser),
        "desired_settings": {"global.update_every": DESIRED_UPDATE_EVERY},
        "service": _service_fields(run),
    }


def _ensure_state_dir() -> None:
    try:
        st = os.lstat(STATE_DIR)
    except OSError as exc:
        raise HelperError("state_dir_missing") from exc
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise HelperError("state_dir_not_safe")
    if hasattr(os, "geteuid") and (st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700):
        raise HelperError("state_dir_ownership_or_mode_unsafe")


@contextlib.contextmanager
def _lock() -> Iterator[None]:
    _ensure_state_dir()
    try:
        import fcntl  # Linux-only; imported lazily so unit tests can import on Windows.
    except ImportError as exc:
        raise HelperError("flock_unavailable") from exc
    try:
        fd = os.open(STATE_DIR / ".lock", os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise HelperError("lock_file_missing_or_unsafe") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, st: os.stat_result) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), stat.S_IMODE(st.st_mode))
            if hasattr(os, "fchown"):
                os.fchown(fh.fileno(), st.st_uid, st.st_gid)
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _write_snapshot(run_id: str, data: bytes) -> Path:
    path = STATE_DIR / f"{run_id}.snapshot"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise HelperError("snapshot_already_exists") from exc
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_dir(STATE_DIR)
    return path


def _write_journal(run_id: str, payload: dict[str, Any]) -> None:
    payload = {**payload, "run_id": run_id, "config_id": CONFIG_ID,
               "updated_at": time.time()}
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path = STATE_DIR / f"{run_id}.json"
    uid = getattr(os, "getuid", lambda: 0)()
    gid = getattr(os, "getgid", lambda: 0)()
    st = os.stat_result((stat.S_IFREG | 0o600, 0, 0, 0, uid, gid,
                         len(raw), time.time(), time.time(), time.time()))
    _atomic_write(path, raw, st)


def _best_effort_journal(run_id: str, payload: dict[str, Any]) -> None:
    """Final outcome is still returned if its journal write fails. In particular, a
    `rollback_failed` must never be downgraded to `command_failed` (which would free the
    target). A lost response is later resolved from a fresh typed inspect."""
    try:
        _write_journal(run_id, payload)
    except Exception:  # final telemetry failure must not change the already-known outcome
        pass


def _restart(run: Callable[..., subprocess.CompletedProcess]) -> bool:
    try:
        proc = run([SYSTEMCTL, "restart", UNIT], capture_output=True,
                   timeout=RESTART_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _postcheck(before_pid: int, expected_sha: str, *,
               run: Callable[..., subprocess.CompletedProcess],
               sleep: Callable[[float], None]) -> tuple[bool, dict[str, Any]]:
    last: dict[str, Any] = {}
    for attempt in range(POSTCHECK_ATTEMPTS):
        try:
            data, _ = _read_config()
            fields = _service_fields(run)
            last = fields
            parsed = _parser(data)
            if (_sha(data) == expected_sha and _healthy(fields)
                    and fields["main_pid"] != before_pid
                    and _safe_settings(parsed).get("global.update_every") == DESIRED_UPDATE_EVERY):
                return True, fields
        except HelperError:
            pass
        if attempt < POSTCHECK_ATTEMPTS - 1:
            sleep(POSTCHECK_INTERVAL)
    return False, last


def _rollback(*, snapshot: Path, before: bytes, before_st: os.stat_result,
              before_sha: str, after_sha: str,
              run: Callable[..., subprocess.CompletedProcess],
              sleep: Callable[[float], None]) -> dict[str, Any]:
    try:
        current, _ = _read_config()
        if _sha(current) != after_sha:
            return {"status": "rollback_failed", "reason": "rollback_cas_mismatch"}
        restored = snapshot.read_bytes()
        if restored != before or _sha(restored) != before_sha:
            return {"status": "rollback_failed", "reason": "snapshot_mismatch"}
        _atomic_write(CONFIG_PATH, restored, before_st)
        try:
            rollback_before_pid = int(_service_fields(run)["main_pid"])
        except HelperError:
            rollback_before_pid = None
        if not _restart(run):
            return {"status": "rollback_failed", "reason": "rollback_restart_failed"}
        for attempt in range(POSTCHECK_ATTEMPTS):
            fields = _service_fields(run)
            now, _ = _read_config()
            if (_sha(now) == before_sha and _healthy(fields)
                    and rollback_before_pid is not None
                    and int(fields.get("main_pid") or 0) != rollback_before_pid):
                return {"status": "rolled_back", "reason": "postcheck_failed",
                        "service": fields}
            if attempt < POSTCHECK_ATTEMPTS - 1:
                sleep(POSTCHECK_INTERVAL)
        return {"status": "rollback_failed", "reason": "rollback_postcheck_failed"}
    except Exception:  # rollback must fail closed on any unexpected local/runtime error
        return {"status": "rollback_failed", "reason": "rollback_exception"}


def apply(*, run_id: str, before_sha256: str, after_sha256: str,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
          sleep: Callable[[float], None] = time.sleep,
          lock: Callable[[], contextlib.AbstractContextManager] = _lock) -> dict[str, Any]:
    if not _RUN_ID_RE.fullmatch(run_id):
        return {"status": "command_failed", "reason": "invalid_run_id"}
    if not _SHA_RE.fullmatch(before_sha256) or not _SHA_RE.fullmatch(after_sha256):
        return {"status": "command_failed", "reason": "invalid_sha256"}
    with lock():
        before, before_st = _read_config()
        if _sha(before) != before_sha256:
            return {"status": "aborted_before_apply", "reason": "config_drift"}
        after = _patched(before)
        if _sha(after) != after_sha256 or after == before:
            return {"status": "aborted_before_apply", "reason": "planned_hash_mismatch"}
        before_service = _service_fields(run)
        if not _healthy(before_service):
            return {"status": "aborted_before_apply", "reason": "service_not_healthy"}
        # Re-check immediately before snapshot/write. The main runtime cannot write this
        # root-owned file, but a package/admin change during the service inspect must still
        # lose the hash-CAS rather than be overwritten.
        confirmed, confirmed_st = _read_config()
        if confirmed != before or _sha(confirmed) != before_sha256:
            return {"status": "aborted_before_apply", "reason": "config_drift"}
        before_st = confirmed_st
        snapshot = _write_snapshot(run_id, before)
        base = {"before_sha256": before_sha256, "after_sha256": after_sha256,
                "helper_sha256": _self_sha256()}
        _write_journal(run_id, {**base, "status": "snapshot_created"})
        # Snapshot/journal IO is intentionally outside the config transaction, so re-pin
        # the source bytes immediately before replace. A package/admin edit in that window
        # must lose the CAS instead of being overwritten by our planned bytes.
        final_before, final_st = _read_config()
        if final_before != before or _sha(final_before) != before_sha256:
            result = {**base, "status": "aborted_before_apply", "reason": "config_drift",
                      "rollback_attempted": False}
            _best_effort_journal(run_id, result)
            return result
        before_st = final_st
        try:
            # `_atomic_write` may raise only after os.replace() succeeded (for example,
            # directory fsync failed). From this point onward the write outcome is therefore
            # ambiguous until a hash check proves otherwise, and compensation is mandatory.
            _atomic_write(CONFIG_PATH, after, before_st)
            _write_journal(run_id, {**base, "status": "config_written"})
            restarted = _restart(run)
            if restarted:
                ok, fields = _postcheck(int(before_service["main_pid"]), after_sha256,
                                        run=run, sleep=sleep)
                if ok:
                    result = {**base, "status": "applied", "service": fields,
                              "rollback_attempted": False}
                    # A journal failure is an observability failure, not a failed targeted
                    # verifier. The caller still persists typed executor evidence; do not
                    # undo a healthy approved change merely because local telemetry failed.
                    _best_effort_journal(run_id, result)
                    return result
            rolled = _rollback(snapshot=snapshot, before=before, before_st=before_st,
                               before_sha=before_sha256, after_sha=after_sha256,
                               run=run, sleep=sleep)
            result = {**base, **rolled, "rollback_attempted": True}
            _best_effort_journal(run_id, result)
            return result
        except Exception:
            try:
                current, _ = _read_config()
            except HelperError:
                current = b""
            if current == before and _sha(current) == before_sha256:
                # The write definitely did not replace the source file.
                result = {**base, "status": "command_failed", "reason": "write_failed",
                          "rollback_attempted": False}
            else:
                # The replace may have succeeded, or another writer may have drifted the
                # file. `_rollback` restores only when its after-hash CAS wins; otherwise
                # it returns locking `rollback_failed` and never overwrites the drift.
                rolled = _rollback(snapshot=snapshot, before=before, before_st=before_st,
                                   before_sha=before_sha256, after_sha=after_sha256,
                                   run=run, sleep=sleep)
                result = {**base, **rolled, "rollback_attempted": True}
            _best_effort_journal(run_id, result)
            return result


def _exit_code(status: str) -> int:
    return {"ok": 0, "applied": 0, "aborted_before_apply": 10, "rolled_back": 20,
            "rollback_failed": 21, "command_failed": 22}.get(status, 23)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", add_help=False)
    ap = sub.add_parser("apply", add_help=False)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--before-sha256", required=True)
    ap.add_argument("--after-sha256", required=True)
    args = parser.parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        result = {"status": "command_failed", "reason": "root_required"}
    else:
        try:
            if args.command == "inspect":
                with _lock():
                    result = inspect()
            else:
                result = apply(run_id=args.run_id, before_sha256=args.before_sha256,
                               after_sha256=args.after_sha256)
        except HelperError as exc:
            result = {"status": "command_failed", "reason": exc.code}
        except Exception:  # fail closed; never expose exception/config details
            result = {"status": "command_failed", "reason": "internal_error"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return _exit_code(str(result.get("status") or ""))


if __name__ == "__main__":
    raise SystemExit(main())
