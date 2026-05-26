"""Realtime project file watcher.

Watches a project root for `.py / .ts / .md / ...` edits and triggers
incremental `reindex_file` calls so the RAG memory stays in sync
with the codebase without the user pressing the "Index" button after
every edit.

Design choices:

  * One watcher per project_root. Calling `start_watcher` twice with
    the same root is idempotent — the second call returns the
    existing watcher unchanged.
  * Per-file debounce. Editors save atomically (write to tmp, rename,
    sometimes touch utimes) so a single Cmd+S can fire 2-4 events.
    We coalesce within DEBOUNCE_SECS before kicking off the
    `reindex_file` call.
  * The reindex happens on a worker thread, NOT on the watchdog
    observer thread, because reindex_file talks to SQLite + Ollama
    and could block long enough to make watchdog drop subsequent
    events.
  * SKIP rules match `index_project`'s: dirs in INDEX_SKIP_DIRS plus
    pattern allow-list from DEFAULT_INDEX_PATTERNS. Files outside
    the allow-list are silently ignored.
"""
from __future__ import annotations

import fnmatch
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.application.code_agent.agent_loop import (
    DEFAULT_INDEX_PATTERNS,
    INDEX_SKIP_DIRS,
    reindex_file,
    unindex_file,
)


logger = logging.getLogger(__name__)


# How long to wait after the last change to a file before kicking off
# the reindex. Editors fire 2-4 events per save (tmp write → rename →
# utimes), so we want to coalesce them.
DEBOUNCE_SECS = 0.6

# Hard cap on file size — same threshold index_project uses to skip
# generated/binary files. We re-check here because the watcher might
# pick up changes to a freshly-grown file that's now too big.
MAX_FILE_BYTES = 200_000


def _matches_any(rel_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(rel_path, p.replace("**/", "")) for p in patterns)


def _path_in_skip_dir(path: Path) -> bool:
    return any(part in INDEX_SKIP_DIRS for part in path.parts)


@dataclass
class WatcherState:
    project_root: Path
    observer: Observer
    handler: "DebouncedHandler"
    started_at: float = field(default_factory=time.time)
    events_seen: int = 0
    reindex_runs: int = 0
    last_error: str | None = None


# Global registry of active watchers keyed by absolute project_root.
_WATCHERS: dict[str, WatcherState] = {}
_LOCK = threading.Lock()


class DebouncedHandler(FileSystemEventHandler):
    """Coalesces rapid-fire events per-path and dispatches reindex
    on a worker thread."""

    def __init__(self, project_root: Path, *, on_run: callable | None = None) -> None:
        self.project_root = project_root.resolve()
        self.patterns = list(DEFAULT_INDEX_PATTERNS)
        # path → timer handle. We replace any pending timer each time
        # the same path fires again, so DEBOUNCE_SECS is "from the
        # LAST event", not "from the first".
        self._timers: dict[str, threading.Timer] = {}
        self._timers_lock = threading.Lock()
        # Optional callback the watcher state uses to bump counters.
        self._on_run = on_run

    def _should_handle(self, path_str: str) -> bool:
        try:
            target = Path(path_str).resolve()
        except Exception:
            return False
        if _path_in_skip_dir(target):
            return False
        try:
            rel = str(target.relative_to(self.project_root)).replace("\\", "/")
        except ValueError:
            return False
        if not _matches_any(rel, self.patterns):
            return False
        return True

    def _schedule(self, path_str: str, *, deleted: bool) -> None:
        if not self._should_handle(path_str):
            return
        with self._timers_lock:
            existing = self._timers.pop(path_str, None)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(DEBOUNCE_SECS, self._run, args=(path_str, deleted))
            t.daemon = True
            self._timers[path_str] = t
            t.start()

    def _run(self, path_str: str, deleted: bool) -> None:
        with self._timers_lock:
            self._timers.pop(path_str, None)
        try:
            target = Path(path_str)
            if deleted:
                unindex_file(self.project_root, target)
            else:
                # File might have been deleted between scheduling and
                # firing; reindex_file falls back to unindex internally
                # when target.is_file() is False.
                if not target.exists():
                    unindex_file(self.project_root, target)
                else:
                    try:
                        if target.stat().st_size > MAX_FILE_BYTES:
                            # Too big — drop existing chunks if any.
                            unindex_file(self.project_root, target)
                            return
                    except OSError:
                        return
                    reindex_file(self.project_root, target)
            if self._on_run:
                self._on_run(ok=True)
        except Exception as exc:
            logger.exception("file watcher reindex failed for %s", path_str)
            if self._on_run:
                self._on_run(ok=False, error=str(exc))

    # ── watchdog hooks ────────────────────────────────────────

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path, deleted=False)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path, deleted=False)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path, deleted=True)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # `src_path` is gone, `dest_path` is the new location.
        self._schedule(event.src_path, deleted=True)
        dest = getattr(event, "dest_path", None)
        if dest:
            self._schedule(dest, deleted=False)


def _key(project_root: Path | str) -> str:
    return str(Path(project_root).resolve())


def start_watcher(project_root: Path | str) -> dict[str, Any]:
    """Begin watching `project_root` for source-file changes.

    Idempotent: calling twice with the same root is a no-op that
    returns the existing watcher's status.
    """
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}

    key = _key(root)
    with _LOCK:
        existing = _WATCHERS.get(key)
        if existing is not None:
            return {"ok": True, "already_watching": True, "project_root": str(root)}

        # State holder created up front so the callback can update it.
        state_holder: dict[str, WatcherState] = {}

        def _on_run(ok: bool, error: str | None = None) -> None:
            st = state_holder.get("state")
            if st is None:
                return
            st.events_seen += 1
            if ok:
                st.reindex_runs += 1
            else:
                st.last_error = error

        handler = DebouncedHandler(root, on_run=_on_run)
        observer = Observer()
        observer.schedule(handler, str(root), recursive=True)
        try:
            observer.start()
        except Exception as exc:
            return {"ok": False, "error": f"observer failed to start: {exc}"}

        state = WatcherState(project_root=root, observer=observer, handler=handler)
        state_holder["state"] = state
        _WATCHERS[key] = state
        return {"ok": True, "already_watching": False, "project_root": str(root)}


def stop_watcher(project_root: Path | str) -> dict[str, Any]:
    """Stop watching. Returns ok=True even if no watcher was running
    (idempotent shutdown)."""
    key = _key(project_root)
    with _LOCK:
        state = _WATCHERS.pop(key, None)
    if state is None:
        return {"ok": True, "was_watching": False, "project_root": key}
    try:
        state.observer.stop()
        # `join` waits for the observer's worker thread to actually
        # finish. 2s is plenty in practice.
        state.observer.join(timeout=2.0)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "was_watching": True}
    return {
        "ok": True,
        "was_watching": True,
        "project_root": key,
        "events_seen": state.events_seen,
        "reindex_runs": state.reindex_runs,
    }


def watcher_status(project_root: Path | str | None = None) -> dict[str, Any]:
    """If project_root is given, report just that one watcher.
    Otherwise, return the list of every active watcher."""
    if project_root is not None:
        key = _key(project_root)
        with _LOCK:
            state = _WATCHERS.get(key)
        if state is None:
            return {"watching": False, "project_root": key}
        return {
            "watching": True,
            "project_root": str(state.project_root),
            "started_at": state.started_at,
            "events_seen": state.events_seen,
            "reindex_runs": state.reindex_runs,
            "last_error": state.last_error,
        }
    with _LOCK:
        snapshot = list(_WATCHERS.values())
    return {
        "active": [
            {
                "project_root": str(s.project_root),
                "started_at": s.started_at,
                "events_seen": s.events_seen,
                "reindex_runs": s.reindex_runs,
                "last_error": s.last_error,
            }
            for s in snapshot
        ],
    }
