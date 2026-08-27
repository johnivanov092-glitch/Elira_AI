"""Durable per-run journal for the code-agent.

This is an observability adapter around the existing agent loop, model router,
and ToolExecutor. It does not execute tools or call models itself.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SECRET_KEYS = re.compile(r"(?:api[_-]?key|authorization|password|secret|token|cookie)", re.I)
_TOKEN_METRIC_KEYS = re.compile(
    r"^(?:(?:prompt|completion|total|input|output|cached|cached[_-]prompt|reasoning|audio|"
    r"current|free|available[_-]input|reserved[_-]output|reserved[_-]system|"
    r"safety[_-]margin|max|max[_-]context|max[_-]output|compacted|history|"
    r"user|budget|doc|left|right|accepted[_-]prediction|rejected[_-]prediction|"
    r"cache[_-]read[_-]input|cache[_-]creation[_-]input)[_-]tokens|"
    r"(?:prompt[_-])?tokens[_-](?:per[_-]second|before|after)|token[_-]budget)$",
    re.I,
)
_MAX_STRING = 40_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_root() -> Path:
    configured = os.getenv("ELIRA_AGENT_RUNS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[4] / ".agent" / "runs"


def _clean(value: Any, *, key: str = "") -> Any:
    """Bound event payloads and redact obvious structured secrets."""
    numeric_token_metric = (
        _TOKEN_METRIC_KEYS.fullmatch(key) is not None
        and type(value) in (int, float)
    )
    if _SECRET_KEYS.search(key) and not numeric_token_metric:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _clean(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, str) and len(value) > _MAX_STRING:
        return value[:_MAX_STRING] + "\n[... truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(_clean(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


class RunJournal:
    """Owns state/events/commands/health files for one stable run id."""

    def __init__(self, run_id: str, *, runs_root: Path | None = None) -> None:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError("run_id must contain only letters, digits, dot, underscore or dash")
        self.run_id = run_id
        self.runs_root = (runs_root or _runtime_root()).resolve()
        self.run_dir = self.runs_root / run_id
        self.events_path = self.run_dir / "events.jsonl"
        self.state_path = self.run_dir / "state.json"
        self.commands_path = self.run_dir / "logs" / "commands.jsonl"
        self.health_path = self.run_dir / "health.json"
        self.lock_path = self.run_dir / "run.lock"
        self.agent_lock_path = self.runs_root.parent / "agent.lock"
        self._locked = False
        self._agent_locked = False
        self._stale_lock: dict[str, Any] | None = None
        self._state: dict[str, Any] = {}

    @classmethod
    def load(cls, run_id: str, *, runs_root: Path | None = None) -> "RunJournal":
        journal = cls(run_id, runs_root=runs_root)
        if not journal.state_path.is_file():
            raise FileNotFoundError(f"run state not found: {run_id}")
        with journal.state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state, dict):
            raise ValueError(f"invalid run state: {run_id}")
        journal._state = state
        return journal

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def acquire(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.commands_path.parent.mkdir(parents=True, exist_ok=True)
        self._acquire_agent_lock()
        try:
            self._acquire_run_lock()
        except RuntimeError:
            self._release_agent_lock()
            raise

    def _acquire_run_lock(self) -> None:
        """Per-run_id write lock only (O_EXCL run.lock) — no global agent.lock."""
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"run is already active: {self.run_id}") from exc
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"pid": os.getpid(), "acquired_at": _utc_now()}, handle)
            handle.write("\n")
        self._locked = True

    def _acquire_agent_lock(self) -> None:
        payload = {
            "run_id": self.run_id,
            "pid": os.getpid(),
            "started_at": _utc_now(),
            "mode": "write",
            "current_phase": "startup",
        }
        for attempt in range(2):
            try:
                fd = os.open(self.agent_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                try:
                    existing = json.loads(self.agent_lock_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    existing = {"invalid": True}
                pid = int(existing.get("pid") or 0) if isinstance(existing, dict) else 0
                if attempt == 0 and not _pid_alive(pid):
                    self._stale_lock = existing if isinstance(existing, dict) else {"invalid": True}
                    try:
                        self.agent_lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                raise RuntimeError(
                    f"another write run is active: {existing.get('run_id') if isinstance(existing, dict) else 'unknown'}"
                ) from exc
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
            self._agent_locked = True
            return
        raise RuntimeError("failed to acquire agent lock")

    def start(self, request: dict[str, Any], capabilities: dict[str, Any]) -> None:
        self.acquire()
        now = _utc_now()
        self._state = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": "running",
            "task": str(request.get("user_message") or ""),
            "mode": "full-machine",
            "current_phase": "startup",
            "step": 0,
            "created_at": now,
            "started_at": now,
            "updated_at": now,
            "last_successful_step": 0,
            "current_goal": str(request.get("user_message") or ""),
            "todo": [],
            "done": [],
            "changed_files": [],
            "mutated_files": [],
            "verifications": [],
            "failed_attempts": [],
            "project_epoch": 0,
            "criteria_epoch": 0,
            "resumable": False,
            "resume_instruction": "",
            "project_root": str(request.get("project_root") or ""),
            "docs_root": str(
                os.getenv("ELIRA_SERVER_DOCS_ROOT", "").strip()
                or (Path(__file__).resolve().parents[4].parent / "Elira_AI_Server")
            ),
            "request": _clean(request),
            "capabilities": _clean(capabilities),
            "active_capabilities": [
                name for name, value in capabilities.items()
                if isinstance(value, dict) and value.get("available")
            ],
            "active_skills": [],
            "runtime_activation": {
                "mcp_server_ids": [],
                "lsp_server_ids": [],
                "ssh": False,
                "itops": False,
                "capability_groups": [],
            },
            "tool_decisions": [],
            "web_sources": [],
            "unavailable_tools": [],
            "unavailable_capabilities": list(capabilities.get("missing") or []),
            "last_response": "",
            "error": None,
            "last_error": None,
        }
        self._write_state()
        self._write_health("running")
        if self._stale_lock:
            self.append_event({"type": "stale_lock_detected", "previous": self._stale_lock})

    def resume(self) -> None:
        self.acquire()
        self._state["status"] = "running"
        self._state["resumable"] = False
        self._state["updated_at"] = _utc_now()
        self._state["resume_count"] = int(self._state.get("resume_count") or 0) + 1
        self._write_state()
        self._write_health("running")

    def append_event(self, event: dict[str, Any]) -> None:
        record = {"timestamp": _utc_now(), "run_id": self.run_id, **_clean(event)}
        self._append_jsonl(self.events_path, record)
        event_type = str(event.get("type") or "")
        step = int(event.get("step") or event.get("steps") or 0)
        runtime_activation = event.get("runtime_activation")
        if isinstance(runtime_activation, dict):
            self._state["runtime_activation"] = _clean(runtime_activation)
        self._state["step"] = max(int(self._state.get("step") or 0), step)
        if event_type == "step_started":
            self._state["current_phase"] = "agent_step"
        if event_type in {"step_started", "tool_call", "final_response", "done"}:
            self._state["last_successful_step"] = max(
                int(self._state.get("last_successful_step") or 0), step
            )
        if event_type == "final_response":
            self._state["last_response"] = str(event.get("text") or "")
        if event_type == "tool_call" and event.get("media"):
            from app.application.code_agent.answer_media import merge_answer_media

            self._state["answer_media"] = merge_answer_media(
                self._state.get("answer_media") or [],
                event.get("media") or [],
            )
        if event_type == "context_resolved":
            self._state["context_resolution"] = _clean(event)
        if event_type == "reasoning_fallback":
            # Delivery (B): the thinking-OFF fallback is one-shot for the whole
            # RUN identity, not just the current process/HTTP session. Durable
            # server-owned marker — continuation builders force thinking=False
            # off it; the original request.thinking is never rewritten.
            self._state["thinking_fallback_applied"] = True
        if event_type == "planning_started":
            # Durable "the planner already ran once" marker — so a Resume /
            # auto-continuation after a planning_fallback (no stored plan) does
            # NOT re-plan (bounded, fail-safe, never an infinite re-plan loop).
            self._state["planning_attempted"] = True
        if event_type == "plan_ready":
            # Bounded planning: the validated PlanArtifact is durable so Resume /
            # auto-continuation REUSE it and never re-plan.
            plan = event.get("plan")
            if isinstance(plan, dict):
                self._state["plan"] = _clean(plan)
        if event_type == "phase_changed":
            # The ACTUALLY-applied brain mode (planning/off/raw) —
            # separate from request.thinking, which is never rewritten.
            mode = event.get("applied_thinking_mode")
            if mode:
                self._state["applied_thinking_mode"] = str(mode)
            self._state["current_phase"] = str(event.get("phase") or self._state.get("current_phase"))
        if event_type == "tool_call":
            touched = str(event.get("touched_path") or "").strip()
            if touched and touched not in self._state["changed_files"]:
                self._state["changed_files"].append(touched)
            if event.get("state_changed"):
                self._state["project_epoch"] = (
                    int(self._state.get("project_epoch") or 0) + 1
                )
                self._state["criteria_epoch"] = -1
                self._state["criteria"] = []
                self._state["verifications"] = []
                self._state["completion_status"] = "unverified"
                if touched:
                    mutated = list(self._state.get("mutated_files") or [])
                    if touched not in mutated:
                        self._state["mutated_files"] = [*mutated, touched][-500:]
            verification = str(event.get("task_state_verification") or "").strip()
            if verification:
                self._state["verifications"] = [
                    *list(self._state.get("verifications") or []),
                    verification[:400],
                ][-200:]
            failure = str(event.get("task_state_failure") or "").strip()
            if failure:
                self._state["failed_attempts"] = [
                    *list(self._state.get("failed_attempts") or []),
                    failure[:240],
                ][-200:]
        if event_type == "tool_decision":
            self._state["tool_decisions"] = [
                *list(self._state.get("tool_decisions") or []),
                _clean(event),
            ][-200:]
        if event_type == "web_source":
            self._state["web_sources"] = [
                *list(self._state.get("web_sources") or []),
                _clean(event),
            ][-100:]
        if event_type == "done":
            stop_reason = str(event.get("stop_reason") or "error")
            resumable = bool(event.get("resumable"))
            # FIX-3: "completed" is the TASK result, NOT runtime `ok`. A run is
            # completed only when its criteria are verifier-confirmed, or when there
            # are no criteria and it reached a clean answer. failed / unverified /
            # partial and every runtime failure are NOT "completed".
            completion = str(event.get("completion_status") or "none")
            reached_answer = bool(event.get("ok")) and stop_reason == "answer"
            if completion == "confirmed" or (completion == "none" and reached_answer):
                status = "completed"
            else:
                status = stop_reason
            self._state.update({
                "status": status,
                "stop_reason": stop_reason,
                "completion_status": completion,
                "criteria": list(event.get("criteria") or []),
                "criteria_epoch": int(self._state.get("project_epoch") or 0),
                "resumable": resumable,
                "error": event.get("error"),
                "last_error": event.get("error"),
                "resume_instruction": (
                    f"POST /api/code-agent/runs/{self.run_id}/resume" if resumable else ""
                ),
            })
            self._state["current_phase"] = "terminal"
        self._state["updated_at"] = _utc_now()
        self._write_state()

    def append_external_event(self, event: dict[str, Any]) -> None:
        """Append-only OBSERVER write for a supervisor that does NOT hold the
        run lock (e.g. the delivery-session slice boundary): events.jsonl only.
        _state and state.json are never touched, so this can never race the
        lock-held writers (start/resume/append_event) or clobber a concurrent
        manual resume's status."""
        record = {"timestamp": _utc_now(), "run_id": self.run_id, **_clean(event)}
        self._append_jsonl(self.events_path, record)

    def record_terminal_event(self, event: dict[str, Any]) -> None:
        """Lock-held TERMINAL write for a supervisor acting BETWEEN slices (no
        live loop owns THIS run): serialized per run_id via run.lock ONLY — the
        global agent.lock is deliberately NOT taken, so a different run's live
        activity can never block recording a terminal for this inactive run.
        The event goes through the normal append_event state machine (a
        cancelled `done` lands as status='cancelled' exactly like an in-loop
        terminal); health is refreshed. Raises if a writer owns THIS run."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._acquire_run_lock()
        try:
            self.append_event(event)
            self._write_health(str(self._state.get("status") or "unknown"))
        finally:
            self.release()

    def append_command(self, event: dict[str, Any]) -> None:
        self._append_jsonl(
            self.commands_path,
            {"timestamp": _utc_now(), "run_id": self.run_id, **_clean(event)},
        )

    def finish(self, *, interrupted: bool = False) -> None:
        if interrupted and self._state.get("status") == "running":
            self._state.update({
                "status": "interrupted",
                "resumable": True,
                "updated_at": _utc_now(),
            })
            self._write_state()
        self._write_health(str(self._state.get("status") or "unknown"))
        self.release()

    def release(self) -> None:
        if self._locked:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
            self._locked = False
        self._release_agent_lock()

    def _release_agent_lock(self) -> None:
        if self._agent_locked:
            try:
                self.agent_lock_path.unlink()
            except FileNotFoundError:
                pass
            self._agent_locked = False

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_state(self) -> None:
        _atomic_json(self.state_path, self._state)

    def _write_health(self, status: str) -> None:
        _atomic_json(
            self.health_path,
            {
                "run_id": self.run_id,
                "status": status,
                "updated_at": _utc_now(),
                "state_readable": self.state_path.is_file(),
                "events_readable": self.events_path.is_file(),
                "disk_free_bytes": shutil.disk_usage(self.run_dir.parent).free,
            },
        )


def discover_capabilities(*, model: str, tools: list[str]) -> dict[str, Any]:
    """Report configured capabilities without probing or inventing endpoints."""
    from app.application.code_agent.tool_schemas import build_tool_schemas
    from app.infrastructure.llm.openai_compatible import local_embed_config, local_llm_config
    from app.infrastructure.llm.vision_ocr import is_ocr_enabled, is_vision_enabled, vision_config

    known_tools = set(tools)
    known_tools.update(
        str((schema.get("function") or {}).get("name") or "")
        for schema in build_tool_schemas()
    )
    known_tools.discard("")
    llm = local_llm_config()
    embed = local_embed_config()
    tesseract = shutil.which("tesseract")
    if not tesseract:
        try:
            from app.application.pdf.runtime import _TESSERACT_CANDIDATES

            tesseract = next((path for path in _TESSERACT_CANDIDATES if Path(path).is_file()), None)
        except (ImportError, OSError):
            tesseract = None
    # OCR is reachable via the configured server (OCR_ENABLED -> :8002 PaddleOCR)
    # or a local tesseract fallback — report either. Vision follows VISION_ENABLED
    # (-> :8004). Report CONFIGURED state only (no network probe — keep run-start
    # fast and non-blocking, per this function's contract). The old code hardcoded
    # vision=False and only checked local tesseract, so a fully-working server
    # vision/OCR setup was falsely reported as a missing capability.
    vision_ok = bool(is_vision_enabled())
    ocr_server = bool(is_ocr_enabled())
    available = {
        "llm": {"available": bool(llm.enabled), "model": model},
        "embedding": {"available": bool(embed.enabled), "model": embed.model},
        "ocr": {
            "available": ocr_server or bool(tesseract),
            "provider": "server-ocr" if ocr_server else ("tesseract" if tesseract else None),
        },
        "vision": {"available": vision_ok, "model": vision_config().model if vision_ok else None},
        "web": {"available": "web_search" in known_tools and "web_fetch" in known_tools},
        "tools": {"available": True, "names": sorted(known_tools)},
    }
    available["missing"] = [name for name, value in available.items() if isinstance(value, dict) and not value.get("available")]
    return available


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
