"""Windows SCM/IPC adapter for the existing release lifecycle.

Only the installed, protected copy is a service entry point. Candidate imports
and commands always run in the authenticated user's process host.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import logging
from pathlib import Path
import queue
import re
import sys
import threading
import time

# -I -S deliberately excludes cwd/site-packages. This path is part of the
# administrator-installed host, not a directory supplied by an IPC request.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from elira_release import (ReleaseLayout, ReleaseManager, _lock, _read_json, _write_json,
                           _begin_progress, _advance_progress, read_release_progress)
from elira_release import _progress_metadata, saved_release_action
from foundation_storage import FoundationStorage
from foundation_windows import serve_pipe, serve_service


LOG = logging.getLogger("elira.foundation")
_REQUEST_ID = re.compile(r"[a-z0-9-]{16,64}")
_RELEASE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_MUTATIONS = {"prepare", "verify", "request", "confirm", "rollback", "open", "close", "register"}


def _session_key(host) -> tuple:
    info = host.token.info
    logons = tuple(sorted(group["sid"] for group in info.get("groups", [])
                          if group.get("attributes", 0) & 0xC0000000 == 0xC0000000))
    return info.get("sid"), info["session_id"], logons


def validate_request(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported Foundation protocol")
    operation = value.get("operation")
    if not isinstance(operation, str) or operation not in _MUTATIONS | {"status", "operation_status"}:
        raise ValueError("Unknown Foundation operation")
    fields = {"version", "operation"}
    if operation in _MUTATIONS:
        fields.add("request_id")
        if not isinstance(value.get("request_id"), str) or not _REQUEST_ID.fullmatch(value["request_id"]):
            raise ValueError("A stable request_id is required")
    if operation in {"prepare", "verify", "request"}:
        fields.add("release_id")
        if (not isinstance(value.get("release_id"), str)
                or not _RELEASE_ID.fullmatch(value["release_id"]) or value["release_id"] in {".", ".."}):
            raise ValueError("Invalid release_id")
    if operation == "operation_status":
        fields.add("operation_id")
        if not isinstance(value.get("operation_id"), str) or not _REQUEST_ID.fullmatch(value["operation_id"]):
            raise ValueError("Invalid operation_id")
    if operation == "confirm":
        fields.add("confirmation_id")
        if not isinstance(value.get("confirmation_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["confirmation_id"]):
            raise ValueError("Invalid confirmation_id")
    if operation == "rollback" and "expected_active" in value:
        fields.update({"expected_active", "expected_previous", "confirm"})
        if (value.get("confirm") is not True
                or any(not isinstance(value.get(key), str) or not _RELEASE_ID.fullmatch(value[key])
                       or value[key] in {".", ".."} for key in ("expected_active", "expected_previous"))
                or value["expected_active"] == value["expected_previous"]):
            raise ValueError("Invalid confirmed rollback selection")
    if set(value) != fields:
        raise ValueError("Unexpected or missing Foundation request fields")
    return dict(value)


class Foundation:
    def __init__(self, config: dict):
        if config.get("application_token_mode", "limited") not in {"limited", "administrator"}:
            raise ValueError("Invalid protected application token mode")
        self.config = config
        self.store = Path(config["store"])
        self.worker_root = Path(config["platform"]) / ".runtime" / "foundation-work"
        self.operations = self.store / "operations"
        self.operations.mkdir(exist_ok=True)
        self.queue = queue.Queue(maxsize=32)
        self.lock = threading.Lock()
        self.host = None
        self.manager = None
        self.desired = bool(_read_json(self.store / "desired.json", {}).get("running", False))
        self.restarts = deque()
        self.retry_at = 0.0
        self.last_error = None
        self.recovered = False
        self.snapshot = {"application": "awaiting_user_session", "running": False}
        # A crash cannot turn an interrupted command into a successful receipt.
        # Repeated request_id returns this result; the client decides the retry.
        for path in self.operations.glob("*.json"):
            record = _read_json(path)
            if record.get("status") in {"queued", "running"}:
                record.update(status="interrupted", error="Foundation restarted before this operation completed")
                _write_json(path, record)

    def dispatch(self, request: dict, host) -> dict:
        request = validate_request(request)
        operation = request["operation"]
        if operation == "status":
            with self.lock:
                snapshot = dict(self.snapshot)
            # The pipe thread remains responsive while the owner verifies a
            # candidate. Read its atomic events rather than its pre-operation cache.
            state = _read_json(self.store / "state.json", {})
            snapshot.update({key: state.get(key) for key in (
                "active", "previous", "pending", "confirmation", "last_confirmation", "error")})
            transition = state.get("transition")
            snapshot["transition"] = transition.get("phase") if isinstance(transition, dict) else None
            snapshot["last_error"] = self.last_error or state.get("error")
            snapshot["progress"] = read_release_progress(self.store)
            return snapshot
        if operation == "operation_status":
            return _read_json(self.operations / (request["operation_id"] + ".json"))
        operation_id = request["request_id"]
        signature = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with self.lock:
            path = self.operations / (operation_id + ".json")
            if path.exists():
                record = _read_json(path)
                if record.get("request_sha256") != signature:
                    raise ValueError("request_id already belongs to a different operation")
                return record
            record = {"operation_id": operation_id, "operation": operation,
                      "request_sha256": signature, "status": "queued", "created_at": time.time()}
            if self.queue.full():
                raise RuntimeError("Foundation operation queue is busy; retry this request_id")
            session = host.clone()
            try:
                _write_json(path, record)
                self.queue.put_nowait((request, session))
            except BaseException:
                session.close()
                raise
            return record

    def _attach(self, host) -> None:
        if self.host is not None:
            if _session_key(self.host) != _session_key(host):
                children = (self.manager.backend, self.manager.ui)
                if any(child is not None and child.poll() is None for child in children):
                    host.close()
                    raise RuntimeError("Another interactive session owns the running application")
                try:
                    self.manager._stop_ui()
                    self.manager._stop_backend()
                except BaseException:
                    host.close()
                    raise
                self.host.close()
                self.host, self.manager = None, None
                self.recovered = False
            else:
                host.close()
                return
        config = self.config
        try:
            layout = ReleaseLayout(
                store=self.store, candidates=Path(config["candidates"]), published=Path(config["published"]),
                data=Path(config["data"]), journals=Path(config["journals"]),
                config_root=Path(config["platform"]) / "backend", worker_root=self.worker_root,
                worker_python=Path(config["python"]), worker_script=Path(__file__).with_name("elira_release.py"),
                service_name=config["service_name"],
            )
            storage = FoundationStorage(host, protected_root=self.store, worker_root=self.worker_root)
            manager = ReleaseManager(Path(config["platform"]), port=config["port"],
                                     layout=layout, host=host, storage=storage)
        except BaseException:
            host.close()
            raise
        self.host, self.manager = host, manager

    def _set_desired(self, running: bool) -> None:
        _write_json(self.store / "desired.json", {"running": running, "updated_at": time.time()})
        self.desired = running

    def _stop_application(self) -> bool:
        if self.manager is None:
            return True
        stopped = True
        for stop in (self.manager._stop_ui, self.manager._stop_backend):
            try:
                stop()
            except Exception:
                stopped = False
                LOG.exception("Could not stop an owned application process; retaining its handle")
        return stopped

    def _operation(self, request: dict, host) -> None:
        path = self.operations / (request["request_id"] + ".json")
        record = _read_json(path)
        record.update(status="running", started_at=time.time())
        _write_json(path, record)
        with self.lock:
            self.snapshot.update(operation_id=request["request_id"], operation=request["operation"])
        previous_progress = _progress_metadata(self.store).get("operation_id")
        manager_operation_started = False
        try:
            self._attach(host)
            manager = self.manager
            operation = request["operation"]
            if operation in {"prepare", "verify", "request"}:
                manager_operation_started = True
                result = getattr(manager, operation)(request["release_id"])
                if operation == "request":
                    result = {"state": result, "deployed": result.get("active") == request["release_id"],
                              "handoff": "Await explicit user confirmation of this proposal before installation."}
            elif operation == "confirm":
                result = manager.confirm(request["confirmation_id"])
            elif operation == "rollback":
                previous = manager.state().get("previous")
                if not previous:
                    raise ValueError("No previous verified release")
                manager_operation_started = True
                result = manager.request(request.get("expected_previous", previous), operation="rollback",
                                         expected_active=request.get("expected_active"),
                                         expected_previous=request.get("expected_previous"))
                if request.get("confirm"):
                    result = manager.confirm(result["confirmation"]["request_id"])
            elif operation in {"open", "register"}:
                if operation == "open":
                    self._set_desired(True)
                    self.restarts.clear()
                    self.retry_at = 0
                    self.last_error = None
                result = {"registered": True, "desired_running": self.desired}
            else:
                if manager.backend is not None and manager.backend.poll() is None:
                    if not manager._http("drain").get("idle"):
                        manager._http("resume")
                        raise RuntimeError("Application still has active work; close was not applied")
                self._set_desired(False)
                manager._stop_ui()
                manager._stop_backend()
                result = {"application": "stopped", "foundation": "running"}
            record.update(status="completed", result=result)
        except Exception as exc:
            LOG.exception("Foundation operation %s failed", request["operation"])
            record.update(status="failed", error=str(exc)[:2000])
            if (request["operation"] in {"prepare", "verify", "request", "rollback"}
                    and not manager_operation_started
                    and _progress_metadata(self.store).get("operation_id") == previous_progress):
                progress_id = _begin_progress(self.store, request["operation"], request.get("release_id"), "failed")
                _advance_progress(self.store, progress_id, "failed", error=str(exc))
        finally:
            record["finished_at"] = time.time()
            _write_json(path, record)

    def _update_snapshot(self) -> None:
        manager = self.manager
        state = manager.state() if manager else _read_json(self.store / "state.json", {})
        backend = manager.backend if manager else None
        ui = manager.ui if manager else None
        running = backend is not None and backend.poll() is None
        snapshot = {"foundation": "running", "application": "running" if running else
                    ("awaiting_user_session" if manager is None else "stopped"),
                    "running": running, "desired_running": self.desired,
                    "application_token_mode": self.config.get("application_token_mode", "limited"),
                    "active": state.get("active"), "previous": state.get("previous"),
                    "saved_release_action": saved_release_action(state),
                    "confirmation": state.get("confirmation"),
                    "last_confirmation": state.get("last_confirmation"),
                    "pending": state.get("pending"), "transition": state.get("transition", {}).get("phase")
                    if isinstance(state.get("transition"), dict) else None,
                    "backend_pid": backend.pid if running else None,
                    "ui_pid": ui.pid if ui is not None and ui.poll() is None else None,
                    "last_error": self.last_error or state.get("error"), "error": state.get("error"),
                    "progress": read_release_progress(self.store)}
        with self.lock:
            self.snapshot = snapshot

    def _tick(self) -> None:
        manager = self.manager
        if manager is None or not self.desired or time.monotonic() < self.retry_at:
            return
        if not self.recovered:
            manager._reap_owned_processes()
            manager.recover()
            self.recovered = True
        # An unexpected child exit cannot terminate the Windows service. Keep
        # data, release receipts and bounded restart history under this owner.
        if (manager.backend is not None and manager.backend.poll() is not None
                or manager.ui is not None and manager.ui.poll() is not None):
            raise RuntimeError("Application process exited; restoring its verified release")
        if manager.backend is None:
            state = manager.state()
            if state.get("active"):
                manager._launch_active(state["active"])
                self.last_error = None
            elif not state.get("pending"):
                raise RuntimeError("No published verified release is selected")
        if manager.apply_pending():
            progress = _progress_metadata(self.store)
            state = manager.state()
            if (progress.get("operation") == "rollback" and progress.get("phase") == "completed"
                    and saved_release_action(state) == "update"
                    and state.get("previous") and not state.get("pending") and not state.get("confirmation")
                    and not state.get("transition")):
                try:
                    # Older applications already show installation proposals.
                    # Offer the preserved version there; never approve it.
                    manager.request(state["previous"])
                except Exception:
                    LOG.exception("Saved-version proposal could not be created; admitted application remains running")

    def advance_lifecycle(self) -> None:
        """One service iteration; also exercised by the installed Windows proof."""
        try:
            self._tick()
        except Exception as exc:
            LOG.exception("Application lifecycle failed; Foundation remains available")
            self.last_error = str(exc)[:2000]
            progress = _progress_metadata(self.store)
            if progress.get("phase") in {"preparing", "checking", "switching", "rolling_back"}:
                _advance_progress(self.store, progress["operation_id"], "failed", error=self.last_error)
            if self._stop_application():
                # Reconcile a persisted admission transaction before restarting;
                # do not leave a healthy retry stuck in an old transition.
                self.recovered = False
            now = time.monotonic()
            while self.restarts and now - self.restarts[0] > 300:
                self.restarts.popleft()
            self.restarts.append(now)
            if len(self.restarts) >= 3:
                self._set_desired(False)
            self.retry_at = now + 5
        self._update_snapshot()

    def run(self, stop_event: threading.Event) -> None:
        with _lock(self.store / "supervisor.lock"):
            pipe = threading.Thread(target=serve_pipe, kwargs={
                "pipe_name": "\\\\.\\pipe\\" + self.config["pipe_name"], "service_name": self.config["service_name"],
                "allowed_user_sid": self.config["user_sid"], "handler": self.dispatch,
                "stop_event": stop_event,
                "token_mode": self.config.get("application_token_mode", "limited"),
            }, name="foundation-pipe", daemon=True)
            pipe.start()
            try:
                while not stop_event.is_set():
                    try:
                        request, host = self.queue.get(timeout=0.25)
                    except queue.Empty:
                        pass
                    else:
                        self._operation(request, host)
                    self.advance_lifecycle()
                    if not pipe.is_alive():
                        raise RuntimeError("Foundation IPC server exited")
            finally:
                stop_event.set()
                self._stop_application()
                while not self.queue.empty():
                    _, host = self.queue.get_nowait()
                    host.close()
                if self.host:
                    self.host.close()
                pipe.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    config = _read_json(args.config)
    if config.get("version") != 1 or config.get("service_name") not in {"EliraFoundation", "EliraFoundationProof"}:
        raise ValueError("Invalid protected installation configuration")
    logging.basicConfig(filename=Path(config["store"]) / "foundation.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    foundation = Foundation(config)
    serve_service(config["service_name"], foundation.run)


if __name__ == "__main__":
    main()
