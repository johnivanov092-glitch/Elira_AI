"""Real, non-destructive identity/access proof for EliraFoundationProof only.

This checker does not install a service, change ACLs, stop a service, or run an
arbitrary command through it. A denied handle request is recorded as precisely
that. Publication and application lifecycle remain separate pending stages.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time
import uuid


SERVICE = "EliraFoundationProof"
CANARY = b"foundation-acl-canary\n"
MEDIUM_RID = 8192
DANGEROUS_PRIVILEGES = {"SeDebugPrivilege", "SeTcbPrivilege", "SeImpersonatePrivilege",
                        "SeAssignPrimaryTokenPrivilege", "SeBackupPrivilege", "SeRestorePrivilege",
                        "SeTakeOwnershipPrivilege", "SeLoadDriverPrivilege", "SeCreateTokenPrivilege"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _medium(info: dict, sid: str, session_id: int) -> None:
    _require(info.get("sid") == sid, "Unexpected user SID")
    _require(type(info.get("session_id")) is int and info["session_id"] == session_id > 0,
             "Unexpected interactive session")
    _require(info.get("integrity_rid") == MEDIUM_RID and info.get("elevated") is False,
             "The user process must actually run at medium integrity without elevation")
    _require(info.get("elevation_type") in {1, 3} and info.get("ui_access") is False,
             "Elevated/UIAccess token is not an accepted client")
    groups = info.get("groups")
    privileges = info.get("privileges")
    _require(isinstance(groups, list) and isinstance(privileges, list), "Missing actual token details")
    _require(info.get("administrator_enabled") is False and not any(
        group.get("sid") == "S-1-5-32-544" and group.get("attributes", 0) & 4 for group in groups),
        "Administrators SID remains enabled")
    _require(not any(privilege.get("name") in DANGEROUS_PRIVILEGES for privilege in privileges),
             "Powerful privileges remain available in the user token")


def validate_identity(response: dict, *, client: dict, service_sid: str, service_pid: int) -> dict:
    """Validate observations, never synthesize a token or accept a printed SID."""
    _require(response.get("ok") is True and isinstance(response.get("result"), dict),
             "The installed service did not complete identity_launch")
    result = response["result"]
    _medium(client, client["sid"], client["session_id"])
    for key in ("client", "child"):
        _medium(result.get(key, {}), client["sid"], client["session_id"])
    actual_service = result.get("service", {})
    _require(actual_service.get("sid") == "S-1-5-19" and actual_service.get("session_id") == 0,
             "The actual service is not LocalService in session zero")
    _require(any(group.get("sid") == service_sid and group.get("attributes", 0) & 4
                 for group in actual_service.get("groups", [])), "Service SID is not enabled")
    _require(type(result.get("service_pid")) is int and result["service_pid"] == service_pid,
             "The response belongs to another service PID")
    _require(type(result.get("child_pid")) is int and result["child_pid"] > 0
             and result["child_pid"] != service_pid, "Missing distinct child PID")
    _require(type(result.get("exit_code")) is int and result["exit_code"] == 0,
             "The real child did not exit successfully")
    creation = result.get("child_creation_identity", "")
    _require(isinstance(creation, str) and creation.startswith("win:")
             and creation[4:].isdigit() and int(creation[4:]) > 0, "Missing actual child creation identity")
    _require(isinstance(result.get("child_image"), str) and bool(result["child_image"]),
             "Missing actual child image")
    return result


def _no_reparse(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    _require(path.is_absolute(), "The proof requires absolute paths")
    for item in (path, *path.parents):
        _require(not getattr(item.lstat(), "st_file_attributes", 0) & 0x400,
                 f"Reparse point in proof path: {item}")
    return resolved


def load_installation(config_path: Path) -> tuple[dict, Path, dict]:
    _require(os.name == "nt", "The real Foundation proof requires Windows")
    install = _no_reparse(Path(os.environ["ProgramFiles"]) / SERVICE)
    store = _no_reparse(Path(os.environ["ProgramData"]) / SERVICE)
    _require(_no_reparse(config_path) == store / "installation.json", "Unexpected installer configuration")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _require(config.get("service_name") == SERVICE and config.get("diagnostic") is True,
             "Only the explicitly installed TEST service is accepted")
    _require(config.get("version") == 1 and 1024 <= config.get("port", 0) <= 65535
             and config["port"] != 8000, "Invalid isolated test port")
    _require(_no_reparse(Path(config["store"])) == store, "Unexpected protected state root")
    platform = _no_reparse(Path(config["platform"]))
    _require(platform != Path(__file__).resolve().parents[1], "Production checkout is not a test platform")
    _require(Path(config["candidates"]).resolve() == platform / ".runtime" / "releases" / "candidates",
             "Unexpected editable candidate root")
    _require(_no_reparse(Path(config["python"])) == install / "python" / "python.exe",
             "Unexpected protected interpreter")
    manifest = json.loads((store / "installation-manifest.json").read_text(encoding="utf-8"))
    _require(isinstance(manifest, dict) and bool(manifest), "Missing installed source manifest")
    for relative, expected in manifest.items():
        path = _no_reparse(install / relative)
        _require(path.is_relative_to(install) and path != install, "Manifest path escaped protected host")
        _require(sha256(path) == expected, f"Installed source hash mismatch: {relative}")
    for required in ("python/python.exe", "host/foundation_windows.py", "acl-canary.txt"):
        _require(required in {str(key).replace("\\", "/") for key in manifest},
                 f"Missing protected manifest member: {required}")
    return config, install, manifest


class WindowsProbe:
    """Read identity and request access rights; never apply dangerous operations."""

    def __init__(self, windows):
        self.windows = windows
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self._bind(self.k, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
        self._bind(self.k, "CloseHandle", w.BOOL, w.HANDLE)
        self._bind(self.k, "GetProcessTimes", w.BOOL, w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4))
        self._bind(self.k, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
        self._bind(self.k, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                   ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
        self._bind(self.a, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
        self._bind(self.a, "OpenSCManagerW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.DWORD)
        self._bind(self.a, "OpenServiceW", w.HANDLE, w.HANDLE, w.LPCWSTR, w.DWORD)
        self._bind(self.a, "CloseServiceHandle", w.BOOL, w.HANDLE)

    @staticmethod
    def _bind(dll, name, restype, *argtypes):
        function = getattr(dll, name)
        function.restype, function.argtypes = restype, list(argtypes)

    @staticmethod
    def _check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def identity(self, pid: int) -> dict:
        process = self._check(self.k.OpenProcess(0x1000, False, pid))
        try:
            created, exited, kernel, user = (w.FILETIME() for _ in range(4))
            self._check(self.k.GetProcessTimes(process, ctypes.byref(created), ctypes.byref(exited),
                                               ctypes.byref(kernel), ctypes.byref(user)))
            image, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
            self._check(self.k.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(size)))
            token = w.HANDLE()
            self._check(self.a.OpenProcessToken(process, 8, ctypes.byref(token)))
            try:
                info = self.windows.token_info(token.value)
            finally:
                self.k.CloseHandle(token)
            return {"pid": pid, "created_filetime": (created.dwHighDateTime << 32) | created.dwLowDateTime,
                    "image": image.value, "token": info}
        finally:
            self.k.CloseHandle(process)

    def access(self, kind: str, target, rights: int) -> dict:
        manager = None
        if kind in {"file", "directory"}:
            flags = 0x00200000 | (0x02000000 if kind == "directory" else 0)
            handle = self.k.CreateFileW(str(target), rights, 7, None, 3, flags, None)
            invalid = handle in (None, 0, ctypes.c_void_p(-1).value)
            closer = self.k.CloseHandle
        elif kind == "service":
            manager = self._check(self.a.OpenSCManagerW(None, None, 1))
            handle = self.a.OpenServiceW(manager, SERVICE, rights)
            invalid, closer = not handle, self.a.CloseServiceHandle
        elif kind == "process":
            handle = self.k.OpenProcess(rights, False, target)
            invalid, closer = not handle, self.k.CloseHandle
        else:
            raise ValueError("Unknown access probe")
        error = ctypes.get_last_error() if invalid else None
        try:
            if not invalid:
                closer(handle)
            return {"kind": kind, "target": str(target), "desired_access": rights,
                    "handle_granted": not invalid, "winerror": error, "passed": invalid and error == 5}
        finally:
            if manager:
                self.a.CloseServiceHandle(manager)


def run(config_path: Path, evidence: Path) -> dict:
    evidence.mkdir(parents=True, exist_ok=False)
    report = {"version": 1, "service": SERVICE, "started_at": time.time(), "status": "INCOMPLETE",
              "phase": "preflight", "checker_sha256": sha256(Path(__file__)),
              "publication": "NOT_RUN", "application_lifecycle": "NOT_RUN", "checks": []}
    try:
        config, install, manifest = load_installation(config_path)
        import foundation_windows as windows

        probe = WindowsProbe(windows)
        report["configuration"] = config
        report["installation_manifest"] = manifest
        report["client_primitives_sha256"] = sha256(Path(windows.__file__))
        report["windows_version"] = list(sys.getwindowsversion())
        report["controller"] = probe.identity(os.getpid())
        service_pid = windows.service_pid(SERVICE)
        before = probe.identity(service_pid)
        report["service_before"] = before
        _require(Path(before["image"]).resolve() == Path(config["python"]).resolve(), "Service image mismatch")
        _require(before["token"]["sid"] == "S-1-5-19", "Actual service is not LocalService")
        with windows.current_user_token(limited=True) as client:
            report["client"] = client.info
            report["client_token_origin"] = getattr(client, "origin", "unspecified")
            _medium(client.info, config["user_sid"], client.info["session_id"])
            report["phase"] = "identity_launch"
            response = windows.pipe_request("\\\\.\\pipe\\" + SERVICE + "-proof",
                                            {"operation": "identity_launch"}, service_name=SERVICE,
                                            timeout=30, token=client)
            report["identity_response"] = response
            validate_identity(response, client=client.info, service_sid=config["service_sid"],
                              service_pid=service_pid)
            _require(Path(response["result"]["child_image"]).resolve() == Path(config["python"]).resolve(),
                     "The actual child did not use the protected interpreter")
            report["identity"] = "PASS"
            report["phase"] = "access_boundary"
            files = [install / "acl-canary.txt", Path(config["store"]) / "acl-canary.txt",
                     Path(config["store"]) / "records" / "acl-canary.txt"]
            for path in files:
                _no_reparse(path)
                _require(path.read_bytes() == CANARY, f"Unexpected canary bytes: {path}")
            before_hashes = {str(path): sha256(path) for path in files}
            report["canaries_before"] = before_hashes
            with windows.WindowsProcessHost(client) as host:
                with host.impersonate():
                    for path in files:
                        for right in (0x40000000, 0x40000, 0x80000, 0x10000):
                            result = probe.access("file", path, right)
                            report["checks"].append(result)
                            _require(result["passed"], "Protected file access was not denied by Windows")
                    for path in (install, Path(config["store"]), Path(config["store"]) / "records"):
                        for right in (0x2, 0x40, 0x40000, 0x80000, 0x10000):
                            result = probe.access("directory", path, right)
                            report["checks"].append(result)
                            _require(result["passed"], "Protected directory replacement access was not denied")
                    for right in (0x20, 0x2, 0x40000, 0x80000):
                        result = probe.access("service", SERVICE, right)
                        report["checks"].append(result)
                        _require(result["passed"], "Service mutation access was not denied by Windows")
                    result = probe.access("process", service_pid, 0x1)
                    report["checks"].append(result)
                    _require(result["passed"], "Service termination access was not denied by Windows")
                    candidate = Path(config["candidates"]) / ("foundation-proof-access-" + uuid.uuid4().hex)
                    candidate.mkdir(parents=True, exist_ok=False)
                    marker = candidate / "candidate-marker.txt"
                    with marker.open("xb") as stream:
                        stream.write(CANARY)
                    _require(marker.read_bytes() == CANARY, "Candidate write/read did not preserve bytes")
                    report["candidate_marker"] = {"path": str(marker), "sha256": sha256(marker)}
            report["canaries_after"] = {str(path): sha256(path) for path in files}
            _require(report["canaries_after"] == before_hashes, "Canary bytes changed")
        _require(windows.service_pid(SERVICE) == service_pid, "Service PID changed during proof")
        after = probe.identity(service_pid)
        report["service_after"] = after
        for field in ("pid", "created_filetime", "image"):
            _require(before[field] == after[field], "Service identity changed during proof")
        for relative, expected in manifest.items():
            _require(sha256(install / relative) == expected, f"Protected source changed: {relative}")
        report["status"] = "PASS_IDENTITY_AND_ACCESS_ONLY"
    except Exception as exc:
        report["status"] = "NOT_READY" if report["phase"] == "preflight" else "FAIL"
        report["error"] = {"type": type(exc).__name__, "message": str(exc),
                           "winerror": getattr(exc, "winerror", None)}
    finally:
        if "canaries_before" in report:
            try:
                report["canaries_after"] = {path: sha256(Path(path)) for path in report["canaries_before"]}
                report["canaries_unchanged"] = report["canaries_before"] == report["canaries_after"]
                if not report["canaries_unchanged"]:
                    report["status"] = "FAIL"
            except OSError as exc:
                report["canary_postflight_error"] = str(exc)
                report["status"] = "FAIL"
        report["finished_at"] = time.time()
        with (evidence / "proof.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return report


def run_lifecycle(config_path: Path, evidence: Path, identity_proof: Path) -> dict:
    """Independent postflight for the installed, fixed stub lifecycle operation."""
    evidence.mkdir(parents=True, exist_ok=False)
    report = {"status": "INCOMPLETE", "started_at": time.time(), "full_elira": "NOT_RUN",
              "native_tauri": "NOT_RUN", "checker_sha256": sha256(Path(__file__))}
    try:
        previous = json.loads(identity_proof.read_text(encoding="utf-8"))
        _require(previous.get("status") == "PASS_IDENTITY_AND_ACCESS_ONLY", "An actual identity/access PASS is required")
        report["identity_access_evidence"] = {"path": str(identity_proof.resolve()), "sha256": sha256(identity_proof)}
        config, install, manifest = load_installation(config_path)
        lifecycle_modules = ("foundation_fixture.py", "foundation_service.py", "foundation_storage.py",
                             "foundation_windows.py", "elira_release.py")
        manifest_members = {str(name).replace("\\", "/") for name in manifest}
        _require(all("host/" + name in manifest_members for name in lifecycle_modules),
                 "The installed manifest does not cover every lifecycle module")
        import foundation_windows as windows
        from elira_release import ReleaseManager

        probe = WindowsProbe(windows)
        before = probe.identity(windows.service_pid(SERVICE))
        report["service_before"] = before
        with windows.current_user_token(limited=True) as client:
            _medium(client.info, config["user_sid"], client.info["session_id"])
            report["installed_module_access"] = []
            with windows.WindowsProcessHost(client) as access_host, access_host.impersonate():
                for name in lifecycle_modules:
                    for right in (0x40000000, 0x40000, 0x80000):
                        denial = probe.access("file", install / "host" / name, right)
                        report["installed_module_access"].append(denial)
                        _require(denial["passed"], "Installed lifecycle module has mutation access")
            response = windows.pipe_request("\\\\.\\pipe\\" + SERVICE + "-proof",
                {"operation": "identity_launch"}, service_name=SERVICE, timeout=30, token=client)
            validate_identity(response, client=client.info, service_sid=config["service_sid"], service_pid=before["pid"])
            report["fresh_identity_response"] = response
            response = windows.pipe_request("\\\\.\\pipe\\" + SERVICE + "-proof",
                {"operation": "lifecycle_proof"}, service_name=SERVICE, timeout=300, token=client)
            report["lifecycle_response"] = response
            _require(response.get("ok") is True and isinstance(response.get("result"), dict), "Lifecycle fixture failed")
            result = response["result"]
            _require(result.get("status") == "PASS_STUB_RELEASE_LIFECYCLE", "The real stub lifecycle did not pass")
            expected_events = {"published-a", "published-b", "published-launcher", "pending-while-busy",
                "failed-startup-rollback", "active-a", "active-b", "automatic-child-recovery",
                "durable-worker-survived", "post-admission-code-rollback"}
            events = {event["name"]: event["value"] for event in result.get("events", [])}
            _require(expected_events <= events.keys(), "Required lifecycle observation is missing")
            _require(result.get("cleanup_ok") is True and result.get("durable_job_cleanup") is True
                     and result.get("remaining_owned_processes") == []
                     and result.get("remaining_backend_pid") is None and result.get("remaining_ui_pid") is None,
                     "Owned fixture processes remain")
            _require(result.get("service_pid") == before["pid"], "Fixture ran in another service process")
            _require(bool(result.get("processes")), "Actual process observations are missing")
            for process in result["processes"]:
                _medium(process["token"], client.info["sid"], client.info["session_id"])
            _require(bool(result.get("workers")), "Actual user worker token observations are missing")
            for worker in result["workers"]:
                _medium(worker["identity"], client.info["sid"], client.info["session_id"])
            for name in ("active-a", "active-b", "published-launcher"):
                identity = events[name].get("fixture_identity", events[name])
                _medium(identity["token"], client.info["sid"], client.info["session_id"])
            _medium(events["durable-worker-survived"]["after"]["identity"]["token"],
                    client.info["sid"], client.info["session_id"])
            roots = result["roots"]
            attempt = result["attempt"]
            _require(isinstance(attempt, str) and attempt.startswith("lifecycle-")
                     and len(attempt) == len("lifecycle-") + 32
                     and all(c in "0123456789abcdef" for c in attempt[len("lifecycle-"):]), "Invalid proof attempt identity")
            expected_roots = {"store": Path(config["store"]) / "lifecycle-proofs" / attempt,
                "data": Path(config["data"]) / attempt, "published": Path(config["published"]) / attempt,
                "candidates": Path(config["candidates"]) / attempt, "platform": Path(config["platform"]) / attempt}
            for key, path in expected_roots.items():
                _no_reparse(path)
                _require(Path(roots[key]).resolve() == path.resolve(), "Fixture root escaped its installed namespace")
            source = Path(sys.modules[ReleaseManager.__module__].__file__)
            installed_release = install / "host/elira_release.py"
            _require(sha256(source) == sha256(installed_release), "Checker release hashing code differs from installed code")
            reader = object.__new__(ReleaseManager)  # Only pure file fingerprint methods; no manager/worker startup.
            report["receipt_checks"] = {}
            for release_id in ("a", "b"):
                published = expected_roots["published"] / release_id
                path = expected_roots["store"] / "records" / (release_id + ".json")
                receipt = json.loads(path.read_text(encoding="utf-8"))
                fingerprint = reader.fingerprint(published, executable="desktop.py")
                _require(receipt.get("status") == "verified" and receipt.get("sha256") == fingerprint
                         and Path(receipt["root"]).resolve() == published.resolve(), "Published receipt/fingerprint mismatch")
                report["receipt_checks"][release_id] = {"receipt_path": str(path), "receipt_sha256": sha256(path),
                                                      "published_fingerprint": fingerprint}
                canary = published / "backend/stub.py"
                with windows.WindowsProcessHost(client) as host, host.impersonate():
                    denial = probe.access("file", canary, 0x40000000)
                _require(denial["passed"], "Published source is writable by the user")
                report["receipt_checks"][release_id]["write_denial"] = denial
            report["database_checks"] = {}
            for name in ("state.db", "facts.sqlite", "jobs.sqlite3"):
                database = expected_roots["data"] / name
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                try:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    wanted = {"preserved", "chats", "runtime"} if name == "state.db" else {"preserved"}
                    _require(tables == wanted, "Fixture database schema has unexpected tables")
                    rows = connection.execute("SELECT key,value,raw,ratio FROM preserved ORDER BY key").fetchall()
                    _require(rows == [("original", "safe", b"\x00\xff", 1.25)], "Original fixture data changed")
                    if name == "state.db":
                        chats = connection.execute("SELECT id,title FROM chats ORDER BY id").fetchall()
                        _require(chats == [("after", "Created after admission"), ("before", "Original fixture chat")],
                                 "New or original chat was lost")
                        _require(connection.execute("SELECT value FROM runtime").fetchall() == [("a",)], "Code rollback was not applied")
                    report["database_checks"][name] = {"rows_passed": True, "sha256": sha256(database)}
                finally:
                    connection.close()
        after = probe.identity(windows.service_pid(SERVICE))
        report["service_after"] = after
        _require(all(before[key] == after[key] for key in ("pid", "created_filetime", "image")), "Foundation service restarted or changed")
        with socket.socket() as port:
            report["test_port_closed"] = port.connect_ex(("127.0.0.1", config["port"])) != 0
        _require(report["test_port_closed"], "Fixture backend port is still open")
        for relative, expected in manifest.items():
            _require(sha256(install / relative) == expected, "Installed Foundation source changed during proof")
        report["status"] = "PASS_STUB_RELEASE_LIFECYCLE"
    except Exception as exc:
        report["status"] = "FAIL_OR_INCOMPLETE"
        report["error"] = {"type": type(exc).__name__, "message": str(exc), "winerror": getattr(exc, "winerror", None)}
    finally:
        report["finished_at"] = time.time()
        with (evidence / "lifecycle.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--lifecycle", action="store_true")
    parser.add_argument("--identity-proof", type=Path)
    args = parser.parse_args()
    if args.lifecycle and args.identity_proof is None:
        parser.error("--lifecycle requires an existing --identity-proof")
    result = run_lifecycle(args.installation, args.evidence.resolve(), args.identity_proof) if args.lifecycle else run(args.installation, args.evidence.resolve())
    print(json.dumps({"status": result["status"], "evidence": str(args.evidence.resolve())}, ensure_ascii=True))
    raise SystemExit(0 if result["status"] in {"PASS_IDENTITY_AND_ACCESS_ONLY", "PASS_STUB_RELEASE_LIFECYCLE"} else 1)


if __name__ == "__main__":
    main()
