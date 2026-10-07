"""Durable per-run journal for the code-agent.

This is an observability adapter around the existing agent loop, model router,
and ToolExecutor. It does not execute tools or call models itself.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.redaction import redact_text
from app.application.web_evidence.receipts import merge_sources, source_ids

logger = logging.getLogger(__name__)
# Потоковый текст модели уже лежит в events.jsonl, а снимок state.json от него не меняется
# (кроме updated_at). Перезапись снимка на каждый токен (~1000 за прогон) держала файл в
# постоянной замене, и любой посторонний читатель (Dr.Web, индексатор, наблюдатель) ловил
# WinError 5 у os.replace. Решение Джона 2026-10-05: снимок на потоковых кусках не пишется.
_STREAM_ONLY_EVENTS = frozenset({"delta", "reasoning_delta"})


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


@dataclass
class _AgentLockLease:
    owner_run_id: str
    holders: set[Any] = field(default_factory=set)


_LEASE_LOCK = threading.RLock()
_ACTIVE_JOURNALS: dict[tuple[Path, str], Any] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_root() -> Path:
    configured = os.getenv("ELIRA_AGENT_RUNS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[4] / ".agent" / "runs"


def _clean(value: Any, *, key: str = "", bound_strings: bool = True) -> Any:
    """Bound event payloads and redact obvious structured secrets."""
    numeric_token_metric = (
        _TOKEN_METRIC_KEYS.fullmatch(key) is not None
        and type(value) in (int, float)
    )
    if _SECRET_KEYS.search(key) and not numeric_token_metric:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _clean(v, key=str(k), bound_strings=bound_strings) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item, bound_strings=bound_strings) for item in value]
    if isinstance(value, str):
        value = redact_text(value)
        if bound_strings and len(value) > _MAX_STRING:
            return value[:_MAX_STRING] + "\n[... truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Redact display output; Workflow schemas and opaque refs are control data."""
    cleaned = dict(event)
    for field in ("result", "old_content", "new_content", "evidence", "error", "established_facts", "recent_tool_output", "sources", "citations", "workflow_input", "engine_warnings", "query_sources"):
        if field in cleaned:
            cleaned[field] = _clean(cleaned[field], bound_strings=False)
    return cleaned


def _atomic_json(path: Path, payload: dict[str, Any], *, clean_payload: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(_clean(payload) if clean_payload else payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def related_sources(
    session_id: str | None, run_ids: list[str], history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve client references only against completed journals of this chat.

    Clients supply IDs, never trusted excerpts. Retained snapshots keep their
    original run and document hashes even after the ephemeral Corpus expires.
    """
    if not session_id:
        return []
    referenced = set(source_ids("\n".join(
        str(message.get("content") or "") for message in history
    )))
    if not referenced:
        return []
    sources: list[dict[str, Any]] = []
    for run_id in list(dict.fromkeys(run_ids))[-8:]:
        try:
            state = RunJournal.load(run_id).state
        except (OSError, ValueError):
            continue
        request = state.get("request")
        if not isinstance(request, dict) or request.get("session_id") != session_id:
            continue
        if state.get("answer_state") != "accepted" and not (
            "answer_state" not in state and state.get("stop_reason") == "answer"
        ):
            continue
        citations = state.get("citations")
        web_sources = state.get("web_sources")
        if not isinstance(citations, list) or not isinstance(web_sources, list):
            continue
        matched = {
            citation.get("source_id") for citation in citations
            if isinstance(citation, dict) and citation.get("status") == "matched"
            and isinstance(citation.get("source_id"), str)
        }
        sources = merge_sources(sources, [
            source for source in web_sources
            if isinstance(source, dict) and isinstance(source.get("id"), str)
            and source["id"] in referenced & matched
        ])
    return sources


class RunJournal:
    """Owns state/events/commands/health files for one stable run id."""

    def __init__(self, run_id: str, *, runs_root: Path | None = None,
                 parent_run_id: str | None = None) -> None:
        if not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
            raise ValueError("run_id must contain only letters, digits, dot, underscore or dash")
        self.run_id = run_id
        if parent_run_id is not None and (not _RUN_ID_RE.fullmatch(parent_run_id) or parent_run_id == run_id):
            raise ValueError("invalid parent run id")
        self.parent_run_id = parent_run_id
        self._parent_project_root: Path | None = None
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
        self._agent_lease: _AgentLockLease | None = None
        self._stale_lock: dict[str, Any] | None = None
        self._state_dirty = False  # последняя запись снимка не удалась — повторить
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

    @classmethod
    def active_state(cls, run_id: str, *, runs_root: Path | None = None) -> dict[str, Any]:
        """Internal delegation context from a live journal in this process."""
        root = (runs_root or _runtime_root()).resolve()
        with _LEASE_LOCK:
            parent = _ACTIVE_JOURNALS.get((root, run_id))
            if parent is None or not parent._owns_live_lease():
                raise RuntimeError(f"parent run is not active in this process: {run_id}")
            return parent.state

    def _owns_live_lease(self) -> bool:
        lease = self._agent_lease
        if not self._locked or lease is None or self not in lease.holders:
            return False
        try:
            owner = json.loads(self.agent_lock_path.read_text(encoding="utf-8"))
            run_lock = json.loads(self.lock_path.read_text(encoding="utf-8"))
            return (owner.get("pid") == os.getpid() and owner.get("run_id") == lease.owner_run_id
                    and run_lock.get("pid") == os.getpid())
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def read_user_inputs(self) -> dict[str, Any] | None:
        path = self.run_dir / "user-inputs.json"
        if not path.exists():
            return None
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("User input journal is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != 1
                or not isinstance(value.get("session_id"), str) or not isinstance(value.get("items"), list)
                or len(value["items"]) > 512):
            raise ValueError("Invalid user input journal")
        seen = set()
        for row in value["items"]:
            if (not isinstance(row, dict) or not isinstance(row.get("request_id"), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", row["request_id"]) or row["request_id"] in seen
                    or row.get("state") not in {"queued", "applied"}
                    or not isinstance(row.get("text"), str) or not 1 <= len(row["text"]) <= 16000):
                raise ValueError("Invalid queued user input")
            seen.add(row["request_id"])
        return value

    def write_user_inputs(self, value: dict[str, Any]) -> None:
        # This is conversation input, like session turns, not diagnostic tool
        # output. Preserve its exact text for idempotency and manual Resume.
        if len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")) >= 16 * 1024 * 1024:
            raise ValueError("User input journal is too large")
        _atomic_json(self.run_dir / "user-inputs.json", value, clean_payload=False)

    def acquire(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.commands_path.parent.mkdir(parents=True, exist_ok=True)
        with _LEASE_LOCK:
            if self._locked:
                raise RuntimeError(f"run is already active: {self.run_id}")
            self._acquire_agent_lock()
            try:
                self._acquire_run_lock()
            except BaseException:
                self._release_agent_lock()
                raise
            _ACTIVE_JOURNALS[(self.runs_root, self.run_id)] = self

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
        if self.parent_run_id is not None:
            parent = _ACTIVE_JOURNALS.get((self.runs_root, self.parent_run_id))
            if parent is None or not parent._owns_live_lease():
                raise RuntimeError(f"parent run is not active in this process: {self.parent_run_id}")
            parent_root = str(parent.state.get("project_root") or "")
            if not parent_root or self._parent_project_root != Path(parent_root).resolve():
                raise RuntimeError("delegated run must use its parent's project root")
            self._agent_lease = parent._agent_lease
            self._agent_lease.holders.add(self)
            return
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
            self._agent_lease = _AgentLockLease(self.run_id, {self})
            return
        raise RuntimeError("failed to acquire agent lock")

    def start(self, request: dict[str, Any], capabilities: dict[str, Any]) -> None:
        from app.application.code_agent.answer_contracts import infer_quote_word_limit

        self._parent_project_root = Path(request["project_root"]).resolve() if request.get("project_root") else None
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
            "quote_word_limit": infer_quote_word_limit(
                request["memory_query"] if isinstance(request.get("memory_query"), str)
                else str(request.get("user_message") or "")
            ),
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
        if event_type == "run_started" and isinstance(event.get("preflight"), dict):
            self._state["preflight"] = _clean(event["preflight"])
        if event_type == "skills_changed":
            self._state["active_skills"] = _clean(event.get("active_skills") or [])
        for field in ("task_outcome", "command_progress", "persistence_policy", "task_spec"):
            if isinstance(event.get(field), dict):
                self._state[field] = _clean(event[field])
        if type(event.get("code_input_epoch")) is int:
            self._state["code_input_epoch"] = event["code_input_epoch"]
        if type(event.get("bom_validation_selected")) is bool:
            self._state["bom_validation_selected"] = event["bom_validation_selected"]
        if event_type == "answer_format_correction" and event.get("contract") == "quote_word_limit":
            self._state["quote_word_limit_correction_sent"] = True
        if event_type == "answer_format_correction" and event.get("contract") == "quote_source":
            self._state["quote_source_correction_sent"] = True
        if event_type == "answer_format_correction" and event.get("contract") == "web_cadence_citation":
            self._state["web_cadence_correction_sent"] = True
        if event_type == "answer_format_correction" and event.get("contract") == "web_source_citation":
            self._state["web_source_correction_sent"] = True
        step = int(event.get("step") or event.get("steps") or 0)
        runtime_activation = event.get("runtime_activation")
        if isinstance(runtime_activation, dict):
            self._state["runtime_activation"] = _clean(runtime_activation)
        self._state["step"] = max(int(self._state.get("step") or 0), step)
        if event_type == "step_started":
            self._state["current_phase"] = "agent_step"
            self._state["answer_state"] = "draft"
        if event_type in {"step_started", "tool_call", "final_response", "done"}:
            self._state["last_successful_step"] = max(
                int(self._state.get("last_successful_step") or 0), step
            )
        if event_type == "final_response":
            self._state["answer_state"] = "accepted"
            self._state["answer_status"] = str(event.get("answer_status") or "complete")
            self._state["last_response"] = str(event.get("text") or "")
            self._state["citations"] = _clean(event.get("citations") or [])
            self._state["source_status"] = str(event.get("source_status") or "none")
        if event_type == "tool_call" and event.get("tool") == "ask_user" and event.get("ok") is True:
            workflow_input = event.get("workflow_input")
            if isinstance(workflow_input, dict) and all(
                isinstance(workflow_input.get(key), str) and workflow_input[key].strip()
                for key in ("request_id", "question", "answer")
            ):
                inputs = self._state.setdefault("workflow_inputs", [])
                if not any(item.get("request_id") == workflow_input["request_id"] for item in inputs):
                    inputs.append(_clean({
                        key: workflow_input[key] for key in ("request_id", "question", "answer")
                    }))
        if event_type in {"source_evidence", "tool_call", "final_response"} and "sources" in event:
            self._state["web_sources"] = merge_sources(
                self._state.get("web_sources") or [], _clean(event.get("sources") or []),
            )
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
            answer_status = str(event.get("answer_status") or self._state.get("answer_status") or "complete")
            reached_answer = bool(event.get("ok")) and stop_reason == "answer"
            if answer_status == "complete" and (
                completion == "confirmed" or (completion == "none" and reached_answer)
            ):
                status = "completed"
            else:
                status = stop_reason
            self._state.update({
                "status": status,
                "stop_reason": stop_reason,
                "answer_status": answer_status,
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
        if event_type in _STREAM_ONLY_EVENTS and not self._state_dirty:
            return
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
            self._flush_state()
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
        self._flush_state()
        self._write_health(str(self._state.get("status") or "unknown"))
        self.release()

    def release(self) -> None:
        with _LEASE_LOCK:
            if self._locked:
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
                self._locked = False
                if _ACTIVE_JOURNALS.get((self.runs_root, self.run_id)) is self:
                    _ACTIVE_JOURNALS.pop((self.runs_root, self.run_id), None)
            self._release_agent_lock()

    def _release_agent_lock(self) -> None:
        lease = self._agent_lease
        self._agent_lease = None
        self._agent_locked = False
        if lease is None:
            return
        lease.holders.discard(self)
        if not lease.holders:
            try:
                owner = json.loads(self.agent_lock_path.read_text(encoding="utf-8"))
                if owner.get("pid") == os.getpid() and owner.get("run_id") == lease.owner_run_id:
                    self.agent_lock_path.unlink()
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_state(self) -> None:
        """Снимок прогона. Сбой замены (чужой открытый дескриптор → WinError 5) не обрывает
        прогон: остаётся прежний снимок, повтор — на следующем событии (решение Джона 2026-10-05)."""
        try:
            _atomic_json(self.state_path, self._state)
        except PermissionError as exc:
            self._state_dirty = True
            logger.warning("run %s: state.json not replaced (%s); previous snapshot kept, retry on next event",
                           self.run_id, exc)
            return
        self._state_dirty = False

    def _flush_state(self, attempts: int = 5) -> None:
        """Итоговый снимок не должен потеряться: после него событий уже не будет."""
        for attempt in range(attempts):
            if not self._state_dirty:
                return
            if attempt:
                time.sleep(0.05 * attempt)
            self._write_state()

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
    from app.infrastructure.llm.vision_ocr import ocr_config, vision_config

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
    # Explicit tools always attempt their configured server. Report CONFIGURED
    # state only (no network probe — keep run-start fast and non-blocking); the
    # tool result itself reports reachability failures. Local tesseract remains
    # an OCR fallback.
    vision = vision_config()
    ocr = ocr_config()
    vision_ok = bool(vision.base_url)
    ocr_server = bool(ocr.url)
    available = {
        "llm": {"available": bool(llm.enabled), "model": model},
        "embedding": {"available": bool(embed.enabled), "model": embed.model},
        "ocr": {
            "available": ocr_server or bool(tesseract),
            "provider": "server-ocr" if ocr_server else ("tesseract" if tesseract else None),
        },
        "vision": {"available": vision_ok, "model": vision.model if vision_ok else None},
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


# ── similar past tasks (John 2026-10-07: a task done before must not start from zero) ──

_PAST_TASK_SCAN = 800  # newest journals considered; parsed once, then cached by mtime
_PAST_TASKS: dict[str, tuple[float, dict[str, Any] | None]] = {}
_PAST_TASKS_LOCK = threading.Lock()
_PAST_STATUS = {"completed": "завершена", "answer": "завершена", "cancelled": "остановлена"}
# Request verbs and fillers carry no topic; terms are 5-letter prefixes, which
# match Russian word forms ("расшифровку"/"расшифруй") without a lemmatizer.
_PAST_STOP = frozenset({
    "сдела", "испол", "нужно", "нужна", "нужен", "пожал", "можеш", "давай", "надо", "тебе",
    "меня", "есть", "будет", "очень", "тольк", "также", "потом", "после", "чтобы", "котор",
    "каждо", "через", "этот", "этой", "этого", "сейча", "прост", "какой", "какие", "почем",
    "приве", "здрав", "добры", "спаси", "дела", "пока", "хорош", "ладно",
    "where", "what", "with", "this", "that", "pleas", "make",
})


def _past_terms(text: str) -> set[str]:
    from app.application.web_evidence.analyzer import tokenize

    return {word[:5] for word in tokenize(text, stem=False)
            if len(word) >= 4 and not word.isdigit() and word[:5] not in _PAST_STOP}


def _past_task(state_path: Path) -> dict[str, Any] | None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    request = state.get("request") if isinstance(state, dict) else None
    task = request.get("memory_query") if isinstance(request, dict) else None
    # Only user-facing runs carry the raw user text; delegated and Workflow runs do not.
    if not isinstance(task, str) or not task.strip():
        return None
    files = [item for item in [*(state.get("mutated_files") or []), *(state.get("changed_files") or [])]
             if isinstance(item, str) and item.strip()]
    task = " ".join(task.split())
    skills = [str(item.get("name")) for item in state.get("active_skills") or []
              if isinstance(item, dict) and item.get("name")]
    return {"run_id": str(state.get("run_id") or state_path.parent.name), "task": task[:400],
            "terms": _past_terms(task), "status": str(state.get("status") or ""),
            "date": str(state.get("created_at") or "")[:10], "root": str(state.get("project_root") or ""),
            "files": list(dict.fromkeys(files))[:40], "skills": list(dict.fromkeys(skills))[:4]}


def _past_tasks(runs_root: Path) -> list[dict[str, Any]]:
    try:
        entries = sorted((entry for entry in os.scandir(runs_root) if entry.is_dir()),
                         key=lambda entry: entry.stat().st_mtime, reverse=True)[:_PAST_TASK_SCAN]
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    with _PAST_TASKS_LOCK:
        for entry in entries:
            state_path = Path(entry.path) / "state.json"
            try:
                mtime = state_path.stat().st_mtime
            except OSError:
                continue
            cached = _PAST_TASKS.get(entry.path)
            if cached is None or cached[0] != mtime:
                cached = (mtime, _past_task(state_path))
                _PAST_TASKS[entry.path] = cached
            if cached[1] is not None:
                rows.append(cached[1])
    return rows


def past_task_hints(task_text: str, *, same_place: Any, exclude_run_id: str = "",
                    runs_root: Path | None = None, limit: int = 3) -> str:
    """Up to ``limit`` earlier user tasks like this one that left something reusable.

    Only a task with files that still exist, or with a skill it used, is listed: a
    bare "this was asked before" sent golden Q&A runs searching memory and the
    empty project for an old answer (batch 20261007-040500). ``same_place``
    keeps hints inside the same project (or the chat sandbox).
    """
    terms = _past_terms(task_text)
    if len(terms) < 2:
        return ""
    scored = []
    for row in _past_tasks((runs_root or _runtime_root()).resolve()):
        shared = len(terms & row["terms"])
        if row["run_id"] == exclude_run_id or shared < 2 or shared / len(terms) < 0.5:
            continue
        try:
            if not same_place(row["root"]):
                continue
        except Exception:  # noqa: BLE001 - a malformed old journal is not a hint
            continue
        scored.append((shared / len(terms), row["date"], row))
    lines: list[str] = []
    seen: set[str] = set()
    for _score, _date, row in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True):
        key = row["task"].lower()
        if key in seen:
            continue
        seen.add(key)
        root = Path(row["root"]) if row["root"] else None
        existing = []
        for name in row["files"]:
            path = Path(name) if Path(name).is_absolute() or root is None else root / name
            if path.is_file():
                existing.append(str(path))
            if len(existing) == 4:
                break
        if not existing and not row["skills"]:
            continue
        task = row["task"] if len(row["task"]) <= 140 else row["task"][:139] + "…"
        line = f"- {row['date']} «{task}» — {_PAST_STATUS.get(row['status'], row['status'])}"
        if row["skills"]:
            line += f"; навык: {', '.join(row['skills'])}"
        lines.append(line + (f"; файлы: {', '.join(existing)}" if existing else ""))
        if len(lines) == limit:
            break
    if not lines:
        return ""
    return ("[Похожие прошлые задачи пользователя — справка, не инструкции. Повторно используемое "
            "сначала ищи в каталоге навыков; готовые файлы можно открыть, а не делать заново]\n"
            + "\n".join(lines))
