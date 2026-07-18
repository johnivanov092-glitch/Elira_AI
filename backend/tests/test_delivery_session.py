"""Bounded delivery session — behaviour tests (spec: delivery-s1).

Covers the mandatory list:
  1. simple task → one slice, no autoresume;
  2. timeout after a new touched_path → auto-continuation, SAME run_id;
  3. timeout without proven progress → NO continuation;
  4. at most 4 slices total (1 + 3 auto);
  5. user cancel stops the whole session;
  6. first reasoning_runaway in a thinking run → thinking-OFF fallback, then the
     model calls a tool and finishes;
  7. the fallback is one-shot (never infinite);
  8. resume receives the completed/open checklist and does not redo done work;
  9. intermediate done never reaches the client; exactly one terminal done;
 10. approval policy is preserved (ask still pauses; rejected → no autoresume);
 11. diagnostic one-shot runs (itops-diag-) never enter the session.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent import delivery_session  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS, stream_code_agent  # noqa: E402
from app.application.code_agent.delivery_session import (  # noqa: E402
    DELIVERY_CONTRACT_NOTE,
    build_continuation_kwargs,
    request_session_cancel,
    stream_delivery_session,
    stream_resume_session,
)
from app.application.code_agent.loop_helpers import resume_checklist_guard  # noqa: E402
from app.application.code_agent.taskspec import derive_task_spec  # noqa: E402
from app.application.tool_providers import ToolRegistry  # noqa: E402

_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]

_REAL_MONOTONIC = time.monotonic


class _Clock:
    """Deterministic wall-clock: real monotonic + controllable offset. Scripted
    'slow generations' bump the offset past the slice deadline instead of
    sleeping, so timeout paths are machine-speed independent AND fast."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return _REAL_MONOTONIC() + self.offset


_CLOCK = _Clock()
# One slice budget in tests; a scripted bump of _TIMEOUT_BUMP trips it.
_SLICE_TIMEOUT_S = 60
_TIMEOUT_BUMP = _SLICE_TIMEOUT_S + 1

# Arms TaskSpec (criteria header) → the session treats it as a structural task.
_STRUCTURED_TASK = (
    "Собери мини-проект.\n\n"
    "Цель:\nПодготовить файлы проекта.\n\n"
    "Критерии готовности:\n1. создан файл out.txt\n"
)
_SIMPLE_TASK = "поправь мелочь в файле"


@contextlib.contextmanager
def _loop_env():
    _CLOCK.offset = 0.0
    with patch.object(agent_loop, "_resolve_code_route", return_value=("test-model", 32768, None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch.object(agent_loop, "_server_url_alive", return_value=True), \
         patch.object(agent_loop, "_run_owned_servers", return_value=[]), \
         patch.object(agent_loop, "_stop_run_servers", return_value=[]), \
         patch.object(agent_loop.time, "monotonic", _CLOCK), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


def _call(name, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _final(text="готово"):
    return {"message": {"content": text, "tool_calls": []}}


class _ScriptChat:
    """Scripted model shared across slices: one entry per TOOL-ENABLED call;
    tool-less calls (wrap-up/summarize) always answer with plain text. Each
    entry is either a response dict or a (clock_bump_seconds, response) pair —
    the bump simulates a slow generation so the slice deadline trips
    deterministically (no real sleep)."""

    def __init__(self, steps, fallback=None):
        self.steps = list(steps)
        self.fallback = fallback or _final()
        self.tool_calls = 0
        self.captured = []  # (options, messages) per tool-enabled call

    def __call__(self, **kw):
        if not kw.get("tools"):
            return _final("сводка")
        self.captured.append((dict(kw.get("options") or {}), list(kw.get("messages") or [])))
        idx = self.tool_calls
        self.tool_calls += 1
        entry = self.steps[idx] if idx < len(self.steps) else self.fallback
        if isinstance(entry, tuple):
            bump, response = entry
            _CLOCK.offset += bump
            return response
        return entry


def _run_session(message, chat, tmp, *, run_id, timeout_s=_SLICE_TIMEOUT_S,
                 thinking=False, permission_mode="bypass"):
    return list(stream_delivery_session(
        user_message=message,
        project_root=tmp,
        model="test-model",
        max_steps=20,
        chat_fn=chat,
        run_id=run_id,
        execution_timeout_seconds=timeout_s,
        approval_wait_seconds=0,
        auto_remember=False,
        permission_mode=permission_mode,
        thinking=thinking,
    ))


def _dones(events):
    return [e for e in events if e.get("type") == "done"]


def _continuings(events):
    return [e for e in events if e.get("type") == "delivery_continuing"]


class SimpleTaskSingleSliceTest(unittest.TestCase):
    """(1) No TaskSpec → passthrough: one ordinary run, no session events."""

    def test_simple_task_one_slice_no_autoresume(self):
        chat = _ScriptChat([_call("write_file", path="a.txt", content="A"), _final()])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_SIMPLE_TASK, chat, tmp, run_id="dlv-simple-1")
        self.assertEqual(len(_dones(events)), 1)
        self.assertEqual(_continuings(events), [])
        self.assertEqual(_dones(events)[0]["stop_reason"], "answer")

    def test_simple_task_passthrough_does_not_register_session(self):
        seen = {}

        def fake_stream(**kw):
            seen.update(kw)
            yield {"type": "run_started", "run_id": kw["run_id"]}
            yield {"type": "done", "ok": True, "steps": 1, "stop_reason": "answer", "error": None}

        with patch.object(delivery_session, "stream_code_agent", side_effect=fake_stream):
            events = list(stream_delivery_session(
                user_message=_SIMPLE_TASK, project_root="/nope", run_id="dlv-simple-2",
            ))
        self.assertEqual(len(_dones(events)), 1)
        # Passthrough forwards the ORIGINAL message — no delivery contract note.
        self.assertEqual(seen["user_message"], _SIMPLE_TASK)
        self.assertFalse(request_session_cancel("dlv-simple-2"))

    def test_diag_run_never_enters_session(self):
        """(11) itops-diag- prefixed run: passthrough even for a structural task."""
        seen = {}

        def fake_stream(**kw):
            seen.update(kw)
            yield {"type": "done", "ok": False, "steps": 1, "stop_reason": "timeout",
                   "error": "t", "partial": True}

        with patch.object(delivery_session, "stream_code_agent", side_effect=fake_stream):
            events = list(stream_delivery_session(
                user_message=_STRUCTURED_TASK, project_root="/nope",
                run_id="itops-diag-abc123",
            ))
        self.assertEqual(len(_dones(events)), 1)
        self.assertEqual(_continuings(events), [])
        self.assertNotIn(DELIVERY_CONTRACT_NOTE.strip()[:20], seen["user_message"])


class AutoContinuationTest(unittest.TestCase):
    def test_timeout_with_new_touched_path_continues_same_run_id(self):
        """(2)+(9) budget stop + proven progress → continuation on the SAME
        run_id; the intermediate done never reaches the client."""
        rid = "dlv-continue-1"
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),      # slice 1: real progress
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),                    # slice 1: slow gen → timeout
            _call("write_file", path="out.txt", content="done"),  # slice 2: criterion file
            _final("out.txt создан"),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            self.assertTrue((Path(tmp) / "a.txt").is_file())
            self.assertTrue((Path(tmp) / "out.txt").is_file())
        dones = _dones(events)
        conts = _continuings(events)
        self.assertEqual(len(dones), 1, "exactly one terminal done must reach the client")
        self.assertEqual(len(conts), 1)
        self.assertEqual(conts[0]["stop_reason"], "timeout")
        self.assertGreaterEqual(conts[0]["progress"]["state_changes"], 1)
        self.assertGreaterEqual(conts[0]["progress"]["new_touched_paths"], 1)  # telemetry
        # (2C) the continuation was earned by a real MUTATION: the write_file
        # event carries the server-owned state_changed flag.
        self.assertTrue(any(e.get("type") == "tool_call" and e.get("tool") == "write_file"
                            and e.get("state_changed") for e in events))
        run_ids = {e.get("run_id") for e in events if e.get("run_id")}
        self.assertEqual(run_ids, {rid}, "every slice must share one run_id")
        self.assertEqual(dones[0]["stop_reason"], "answer")
        # run_resumed proves slice 2 resumed the same persisted run.
        self.assertTrue(any(e.get("type") == "run_resumed" for e in events))

    def test_timeout_without_progress_does_not_continue(self):
        """(3) budget stop with NO structural progress → honest terminal."""
        rid = "dlv-noprog-1"
        chat = _ScriptChat([
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),  # slow gen; glob has no touched_path
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
        dones = _dones(events)
        self.assertEqual(_continuings(events), [])
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "timeout")
        self.assertTrue(dones[0].get("resumable"))

    def test_at_most_four_slices_then_honest_partial(self):
        """(4) 1 + 3 auto-continuations, then resumable partial with the budget
        marker — never a fifth slice."""
        rid = "dlv-limit-1"
        steps = []
        for k in range(4):
            steps.append(_call("write_file", path=f"f{k}.txt", content=str(k)))
            steps.append((_TIMEOUT_BUMP, _call("glob", pattern="*")))
        chat = _ScriptChat(steps)
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            for k in range(4):
                self.assertTrue((Path(tmp) / f"f{k}.txt").is_file())
        dones = _dones(events)
        conts = _continuings(events)
        self.assertEqual(len(conts), 3, "exactly three automatic continuations")
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "timeout")
        self.assertEqual(dones[0].get("auto_continuations"), 3)
        self.assertTrue(dones[0].get("resumable"))

    def test_user_cancel_stops_whole_session(self):
        """(5) request_cancel mid-slice → cancelled terminal, no continuation
        even though the slice made progress."""
        rid = "dlv-cancel-1"

        def cancelling_write(**kw):
            return _call("write_file", path="c.txt", content="C")

        class _CancelChat(_ScriptChat):
            def __call__(self, **kw):
                if self.tool_calls == 2:  # first call of slice 2
                    agent_loop.request_cancel(rid)
                return super().__call__(**kw)

        chat = _CancelChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
            _call("write_file", path="b.txt", content="B"),  # slice 2 — cancelled before exec
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
        dones = _dones(events)
        self.assertEqual(len(_continuings(events)), 1)
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "cancelled")

    def test_session_cancel_flag_blocks_next_continuation(self):
        """(5) the session-level flag (POST /cancel path) is honoured at the
        slice boundary even when the slice itself kept making progress."""
        rid = "dlv-cancel-2"

        class _FlagChat(_ScriptChat):
            def __call__(self, **kw):
                if self.tool_calls == 2:  # slice 2 begins → user pressed Stop
                    self.session_found = request_session_cancel(rid)
                return super().__call__(**kw)

        steps = []
        for k in range(3):
            steps.append(_call("write_file", path=f"s{k}.txt", content=str(k)))
            steps.append((_TIMEOUT_BUMP, _call("glob", pattern="*")))
        chat = _FlagChat(steps)
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
        self.assertTrue(chat.session_found, "session must be registered while live")
        dones = _dones(events)
        # slice 2 still finishes (timeout+progress) but the session must NOT
        # start slice 3.
        self.assertEqual(len(_continuings(events)), 1)
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "timeout")


class SessionOwnershipTest(unittest.TestCase):
    def test_stop_in_inter_slice_gap_cancels_before_next_slice(self):
        """(1A) Stop after delivery_continuing but before slice 2: the next
        slice never calls the model or a tool, the terminal is cancelled,
        exactly one done, and the journal agrees with SSE."""
        rid = "dlv-gapcancel-1"
        from app.application.code_agent.run_journal import RunJournal
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
            _call("write_file", path="b.txt", content="B"),  # must NEVER run
            _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            gen = stream_delivery_session(
                user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                max_steps=20, chat_fn=chat, run_id=rid,
                execution_timeout_seconds=_SLICE_TIMEOUT_S,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            )
            events = []
            for ev in gen:
                events.append(ev)
                if ev.get("type") == "delivery_continuing":
                    # user pressed Stop exactly in the inter-slice gap
                    self.assertTrue(request_session_cancel(rid))
                    agent_loop.request_cancel(rid)  # no live loop in the gap
                    break
            for ev in gen:
                events.append(ev)
            self.assertFalse((Path(tmp) / "b.txt").exists(),
                             "slice 2 must not execute any tool after Stop")
            state = RunJournal.load(rid).state
        self.assertEqual(chat.tool_calls, 2, "slice 2 must not call the model")
        dones = _dones(events)
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "cancelled")
        self.assertEqual(state.get("status"), "cancelled")
        self.assertEqual(state.get("stop_reason"), "cancelled")

    def test_cancel_during_continuation_build_stops_next_slice(self):
        """(A) Stop lands INSIDE build_continuation_kwargs (after the boundary
        gap-check): the next slice registers, sees the pending session cancel,
        and terminates with ZERO model calls."""
        rid = "dlv-buildcancel-1"
        from app.application.code_agent.run_journal import RunJournal
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
            _call("write_file", path="b.txt", content="B"),  # must NEVER run
            _final(),
        ])
        orig_build = delivery_session.build_continuation_kwargs

        def cancelling_build(*args, **kwargs):
            request_session_cancel(rid)
            return orig_build(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(delivery_session, "build_continuation_kwargs",
                          side_effect=cancelling_build):
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            self.assertFalse((Path(tmp) / "b.txt").exists())
            state = RunJournal.load(rid).state
        self.assertEqual(chat.tool_calls, 2, "slice 2 must make zero model calls")
        dones = _dones(events)
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "cancelled")
        self.assertEqual(state.get("status"), "cancelled")

    def test_cancel_just_before_slice_registration_stops_slice(self):
        """(B) Stop lands right BEFORE the next slice registers its run: the
        registration itself picks the pending session cancel up — no model or
        tool call happens in that slice."""
        rid = "dlv-regcancel-1"
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
            _call("write_file", path="b.txt", content="B"),  # must NEVER run
            _final(),
        ])
        orig_register = agent_loop._register_run
        seen = {"n": 0}

        def cancelling_register(run_id):
            seen["n"] += 1
            if seen["n"] == 2:  # the second slice's registration
                request_session_cancel(rid)
            return orig_register(run_id)

        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_register_run", side_effect=cancelling_register):
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            self.assertFalse((Path(tmp) / "b.txt").exists())
        self.assertEqual(chat.tool_calls, 2, "cancelled slice must not call the model")
        dones = _dones(events)
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "cancelled")

    def test_cancelled_terminal_durable_despite_foreign_agent_lock(self):
        """(C) a DIFFERENT run holding the global agent.lock must not block
        recording the cancelled terminal for this inactive run."""
        import json as _json
        import os as _os
        from app.application.code_agent.run_journal import RunJournal
        with tempfile.TemporaryDirectory() as tmp:
            rr = Path(tmp) / "runs"
            j = RunJournal("dlv-clock-1", runs_root=rr)
            j.start({"user_message": "т", "project_root": tmp}, {})
            j.append_event({"type": "done", "ok": False, "steps": 1,
                            "stop_reason": "timeout", "error": "t",
                            "partial": True, "resumable": True})
            j.finish()
            # foreign ACTIVE run owns the global write lock (live pid)
            agent_lock = rr.parent / "agent.lock"
            agent_lock.write_text(_json.dumps(
                {"run_id": "other-run", "pid": _os.getpid()}), encoding="utf-8")
            j2 = RunJournal.load("dlv-clock-1", runs_root=rr)
            j2.record_terminal_event({
                "type": "done", "ok": False, "steps": 1,
                "stop_reason": "cancelled", "error": "Cancelled by user",
                "partial": True, "resumable": True,
            })
            state = RunJournal.load("dlv-clock-1", runs_root=rr).state
            self.assertEqual(state.get("status"), "cancelled")
            self.assertEqual(state.get("stop_reason"), "cancelled")
            # the foreign lock is untouched and OUR run.lock is released
            self.assertTrue(agent_lock.is_file())
            self.assertEqual(_json.loads(agent_lock.read_text(encoding="utf-8"))["run_id"],
                             "other-run")
            self.assertFalse((rr / "dlv-clock-1" / "run.lock").exists())

    def test_non_shaped_stream_same_run_id_refused_while_session_active(self):
        """(D) while a delivery session is live, ANY second /stream on the same
        run_id — even a simple non-delivery message — gets the stable duplicate
        error and never starts stream_code_agent."""
        rid = "dlv-dup-2"
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            gen1 = stream_delivery_session(
                user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                max_steps=20, chat_fn=chat, run_id=rid,
                execution_timeout_seconds=_SLICE_TIMEOUT_S,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            )
            events = []
            for ev in gen1:
                events.append(ev)
                if ev.get("type") == "run_started":
                    break
            chat2 = _ScriptChat([_final()])
            dup = list(stream_delivery_session(
                user_message=_SIMPLE_TASK, project_root=tmp, model="test-model",
                chat_fn=chat2, run_id=rid, approval_wait_seconds=0,
            ))
            self.assertEqual(len(dup), 1)
            self.assertEqual(dup[0]["stop_reason"], "error")
            self.assertEqual(dup[0]["error"], "delivery_session_already_active")
            self.assertEqual(chat2.tool_calls, 0, "no stream_code_agent may start")
            for ev in gen1:
                events.append(ev)
        self.assertEqual(len(_dones(events)), 1)

    def test_register_session_is_atomic_claim(self):
        """(1B) a second _register_session with the same run_id never replaces
        the first; cancel signals the FIRST session's token."""
        rid = "dlv-atomic-1"
        ev1 = delivery_session._register_session(rid)
        try:
            with self.assertRaises(delivery_session.DeliverySessionActiveError):
                delivery_session._register_session(rid)
            self.assertTrue(request_session_cancel(rid))
            self.assertTrue(ev1.is_set(), "cancel must reach the first session's token")
            # identity-guarded release: a foreign event cannot evict the owner
            import threading as _threading
            delivery_session._unregister_session(rid, _threading.Event())
            self.assertTrue(request_session_cancel(rid), "owner entry must survive")
        finally:
            delivery_session._unregister_session(rid, ev1)
        self.assertFalse(request_session_cancel(rid))

    def test_duplicate_manual_resume_keeps_original_ownership(self):
        """(1C) a duplicate manual Resume of a LIVE session fails with the
        stable error and does not remove the original's cancel ownership."""
        rid = "dlv-dup-1"
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            gen1 = stream_delivery_session(
                user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                max_steps=20, chat_fn=chat, run_id=rid,
                execution_timeout_seconds=_SLICE_TIMEOUT_S,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            )
            events = []
            for ev in gen1:
                events.append(ev)
                if ev.get("type") == "run_started":
                    break
            # session 1 is live — the duplicate must be refused
            dup = list(stream_resume_session(
                rid, approval_wait_seconds=0, chat_fn=_ScriptChat([_final()]),
            ))
            self.assertEqual(len(dup), 1)
            self.assertEqual(dup[0]["type"], "done")
            self.assertEqual(dup[0]["stop_reason"], "error")
            self.assertEqual(dup[0]["error"], "delivery_session_already_active")
            # original still owns the cancel token
            self.assertTrue(request_session_cancel(rid))
            for ev in gen1:
                events.append(ev)
        self.assertEqual(len(_dones(events)), 1)
        # owner's finally released the token — nothing left to cancel
        self.assertFalse(request_session_cancel(rid))


class MutationProgressTest(unittest.TestCase):
    def test_read_only_touched_path_is_not_structural_progress(self):
        """(2A) read_file of a NEW file + timeout: the file is unchanged and
        NO delivery_continuing fires — a read is not a mutation."""
        rid = "dlv-read-1"
        chat = _ScriptChat([
            _call("read_file", path="r.txt"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            (Path(tmp) / "r.txt").write_text("данные", encoding="utf-8")
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            self.assertEqual((Path(tmp) / "r.txt").read_text(encoding="utf-8"), "данные")
        self.assertEqual(_continuings(events), [])
        dones = _dones(events)
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["stop_reason"], "timeout")
        read_evs = [e for e in events if e.get("type") == "tool_call"
                    and e.get("tool") == "read_file"]
        self.assertTrue(read_evs and read_evs[0].get("touched_path"),
                        "sanity: the read DID report a touched_path")
        self.assertFalse(read_evs[0].get("state_changed"))

    def test_ssh_read_touched_path_is_not_structural_progress(self):
        """(2B) ssh_read + timeout: remote read reports touched_path but the
        session must not continue on it."""
        rid = "dlv-read-2"

        def _exec(req, *a, **kw):
            if req.tool_name == "ssh_read":
                return SimpleNamespace(status="ok", output={
                    "ok": True, "text": "config content",
                    "touched_path": "/etc/app.conf",
                })
            return SimpleNamespace(status="ok", output={"text": "[]"})

        chat = _ScriptChat([
            _call("ssh_read", host="home-srv", path="/etc/app.conf"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", side_effect=_exec):
            events = list(stream_delivery_session(
                user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                max_steps=20, chat_fn=chat, run_id=rid,
                base_tools=(*_CODE_AGENT_BASE_TOOLS, "ssh_read"),
                execution_timeout_seconds=_SLICE_TIMEOUT_S,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            ))
        self.assertEqual(_continuings(events), [])
        self.assertEqual(len(_dones(events)), 1)
        ssh_evs = [e for e in events if e.get("type") == "tool_call"
                   and e.get("tool") == "ssh_read"]
        self.assertTrue(ssh_evs and ssh_evs[0].get("touched_path"))
        self.assertFalse(ssh_evs[0].get("state_changed"))

    def test_write_after_prior_read_of_same_path_counts_on_resume(self):
        """(E) slice 1 only READ same.txt (no continuation); after manual
        Resume a real WRITE of the SAME path is proven progress — session-wide
        path novelty must not veto a genuine mutation."""
        rid = "dlv-samepath-1"
        chat1 = _ScriptChat([
            _call("read_file", path="same.txt"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            (Path(tmp) / "same.txt").write_text("старое", encoding="utf-8")
            first = _run_session(_STRUCTURED_TASK, chat1, tmp, run_id=rid)
            self.assertEqual(_continuings(first), [])
            self.assertTrue(_dones(first)[0].get("resumable"))
            chat2 = _ScriptChat([
                _call("write_file", path="same.txt", content="новое"),
                (_TIMEOUT_BUMP, _call("glob", pattern="*")),
                _final(),
            ])
            resumed = list(stream_resume_session(
                rid, approval_wait_seconds=0, chat_fn=chat2,
            ))
            self.assertEqual((Path(tmp) / "same.txt").read_text(encoding="utf-8"),
                             "новое")
        conts = _continuings(resumed)
        self.assertEqual(len(conts), 1,
                         "the write of a previously-READ path must earn a continuation")
        self.assertGreaterEqual(conts[0]["progress"]["state_changes"], 1)
        self.assertEqual(len(_dones(resumed)), 1)

    def test_repeat_mutation_of_same_file_counts_in_next_slice(self):
        """(F) slice 1 writes a.txt, slice 2 REALLY edits the same a.txt — the
        second mutation is progress and earns a second continuation."""
        rid = "dlv-samepath-2"
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="v1"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
            _call("write_file", path="a.txt", content="v2"),  # same path, real change
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
            _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            self.assertEqual((Path(tmp) / "a.txt").read_text(encoding="utf-8"), "v2")
        conts = _continuings(events)
        self.assertEqual(len(conts), 2,
                         "a real re-edit of the same file must count as progress")
        self.assertGreaterEqual(conts[1]["progress"]["state_changes"], 1)
        self.assertEqual(len(_dones(events)), 1)

    def test_identical_write_is_noop_not_progress(self):
        """(G) overwriting an existing file with the SAME bytes is a proven
        no-op: state_changed=False, no continuation."""
        rid = "dlv-noop-1"
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            (Path(tmp) / "a.txt").write_text("A", encoding="utf-8")
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
        self.assertEqual(_continuings(events), [])
        done = _dones(events)[0]
        self.assertEqual(done["stop_reason"], "timeout")
        writes = [e for e in events if e.get("type") == "tool_call"
                  and e.get("tool") == "write_file"]
        self.assertTrue(writes and writes[0].get("touched_path"),
                        "sanity: the write DID execute and report its path")
        self.assertFalse(writes[0].get("state_changed"))

    def test_state_changed_false_for_failed_blocked_unknown(self):
        """(H) unit contract of the single mutation seam."""
        from app.application.code_agent.loop_helpers import tool_state_changed
        from app.application.tool_registry.runtime import seed_builtin_tools
        seed_builtin_tools()
        # failed/blocked execution → False even with a touched_path
        self.assertFalse(tool_state_changed(
            "write_file", {"touched_path": "x", "old_content": "a", "new_content": "b"},
            exec_ok=False))
        # unknown/unregistered tool → False (conservative)
        self.assertFalse(tool_state_changed(
            "totally_unknown_tool", {"touched_path": "x"}, exec_ok=True))
        # read-only spec → False
        self.assertFalse(tool_state_changed(
            "read_file", {"touched_path": "x"}, exec_ok=True))
        self.assertFalse(tool_state_changed(
            "ssh_read", {"touched_path": "/etc/x"}, exec_ok=True))
        # proven no-op (identical bytes) → False
        self.assertFalse(tool_state_changed(
            "write_file", {"touched_path": "x", "old_content": "s", "new_content": "s"},
            exec_ok=True))
        # no touched target → False
        self.assertFalse(tool_state_changed("write_file", {}, exec_ok=True))
        # real successful writes → True (changed content; brand-new file)
        self.assertTrue(tool_state_changed(
            "write_file", {"touched_path": "x", "old_content": "a", "new_content": "b"},
            exec_ok=True))
        self.assertTrue(tool_state_changed(
            "write_file", {"touched_path": "x", "new_content": "b"}, exec_ok=True))
        self.assertTrue(tool_state_changed(
            "ssh_write", {"touched_path": "/etc/x"}, exec_ok=True))

    def test_two_reads_do_not_consume_continuation_budget(self):
        """(2D) two reads of DIFFERENT files still make zero mutation progress
        — no continuation, no budget spent."""
        rid = "dlv-read-3"
        chat = _ScriptChat([
            _call("read_file", path="r1.txt"),
            _call("read_file", path="r2.txt"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            (Path(tmp) / "r1.txt").write_text("один", encoding="utf-8")
            (Path(tmp) / "r2.txt").write_text("два", encoding="utf-8")
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
        self.assertEqual(_continuings(events), [])
        done = _dones(events)[0]
        self.assertEqual(done["stop_reason"], "timeout")
        self.assertNotIn("auto_continuations", done,
                         "no continuation budget may be spent on reads")


class ReasoningFallbackTest(unittest.TestCase):
    def test_first_runaway_flips_thinking_off_and_run_finishes(self):
        """(6) first runaway in a thinking run: reasoning_fallback event, the
        truncated generation is discarded, subsequent calls run thinking-OFF,
        the model then acts and completes."""
        chat = _ScriptChat([
            {**_call("write_file", path="junk.txt", content="J"), "reasoning_runaway": True},
            _call("write_file", path="real.txt", content="R"),
            _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = list(stream_code_agent(
                user_message="создай файл real.txt", project_root=tmp, model="test-model",
                max_steps=10, chat_fn=chat, run_id="dlv-fb-1",
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", thinking=True,
            ))
            # The runaway generation's tool call must NOT execute.
            self.assertFalse((Path(tmp) / "junk.txt").exists())
            self.assertTrue((Path(tmp) / "real.txt").is_file())
        fallbacks = [e for e in events if e.get("type") == "reasoning_fallback"]
        self.assertEqual(len(fallbacks), 1)
        done = _dones(events)[-1]
        self.assertTrue(done["ok"])
        self.assertEqual(done["stop_reason"], "answer")
        # Call 1 ran thinking-ON; every call after the fallback must omit
        # chat_template_kwargs entirely (server default = off).
        self.assertEqual(chat.captured[0][0].get("chat_template_kwargs"),
                         {"enable_thinking": True})
        for options, _ in chat.captured[1:]:
            self.assertNotIn("chat_template_kwargs", options)
        # The server-owned instruction reached the next call's history.
        _, messages_after = chat.captured[1]
        self.assertTrue(any("[internal correction]" in str(m.get("content") or "")
                            for m in messages_after if m.get("role") == "user"))

    def test_fallback_is_one_shot_second_runaway_hits_loop_guard(self):
        """(7) after the fallback the existing bounded guard still terminates."""
        runaway = {**_call("glob", pattern="*"), "reasoning_runaway": True}
        chat = _ScriptChat([runaway, runaway, runaway], fallback=runaway)
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = list(stream_code_agent(
                user_message="задача", project_root=tmp, model="test-model",
                max_steps=10, chat_fn=chat, run_id="dlv-fb-2",
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", thinking=True,
            ))
        fallbacks = [e for e in events if e.get("type") == "reasoning_fallback"]
        self.assertEqual(len(fallbacks), 1, "fallback must never repeat")
        done = _dones(events)[-1]
        self.assertEqual(done["stop_reason"], "loop_guard")
        self.assertIn("reasoning runaway", str(done.get("error")))

    def test_non_thinking_run_keeps_existing_budget_behaviour(self):
        """Regression: without thinking there is no fallback — two runaways
        still finalize via loop_guard (pre-existing contract)."""
        runaway = {**_call("glob", pattern="*"), "reasoning_runaway": True}
        chat = _ScriptChat([runaway, runaway], fallback=runaway)
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = list(stream_code_agent(
                user_message="задача", project_root=tmp, model="test-model",
                max_steps=10, chat_fn=chat, run_id="dlv-fb-3",
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", thinking=False,
            ))
        self.assertEqual([e for e in events if e.get("type") == "reasoning_fallback"], [])
        done = _dones(events)[-1]
        self.assertEqual(done["stop_reason"], "loop_guard")

    def test_manual_resume_stays_thinking_off_after_fallback(self):
        """P1 fix: the fallback is durable for the RUN identity — after the
        process/HTTP session ended, a manual Resume of the same run_id must not
        re-enable thinking; the journalled request keeps the original flag."""
        rid = "dlv-fb-5"
        from app.application.code_agent.run_journal import RunJournal
        chat = _ScriptChat([
            {**_call("glob", pattern="*"), "reasoning_runaway": True},   # fallback
            _call("write_file", path="a.txt", content="A"),               # progress
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),                  # timeout terminal
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = list(stream_code_agent(
                user_message="создай файл a.txt", project_root=tmp, model="test-model",
                max_steps=10, chat_fn=chat, run_id=rid,
                execution_timeout_seconds=_SLICE_TIMEOUT_S,
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", thinking=True,
            ))
            self.assertTrue(any(e.get("type") == "reasoning_fallback" for e in events))
            self.assertTrue(_dones(events)[-1].get("resumable"))
            state = RunJournal.load(rid).state
            self.assertTrue(state.get("thinking_fallback_applied"))
            # the original request is untouched — only the durable marker exists
            self.assertTrue((state.get("request") or {}).get("thinking"))
            resume_chat = _ScriptChat([
                _call("write_file", path="a.txt", content="A"),
                _final(),
            ])
            resumed = list(stream_resume_session(
                rid, approval_wait_seconds=0, chat_fn=resume_chat,
            ))
        self.assertEqual(len(_dones(resumed)), 1)
        self.assertTrue(resume_chat.captured, "resume slice must call the model")
        for options, _ in resume_chat.captured:
            self.assertNotIn("chat_template_kwargs", options,
                             "no model call after the fallback may re-enable thinking")

    def test_session_keeps_thinking_off_after_fallback(self):
        """(B, session scope) a fallback in slice 1 pins thinking-OFF for the
        following slices of the same session."""
        rid = "dlv-fb-4"
        chat = _ScriptChat([
            {**_call("glob", pattern="*"), "reasoning_runaway": True},   # slice 1: fallback
            _call("write_file", path="a.txt", content="A"),               # slice 1: progress
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),                            # slice 1: timeout
            _call("write_file", path="out.txt", content="ok"),            # slice 2
            _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid, thinking=True)
        self.assertEqual(len(_continuings(events)), 1)
        self.assertEqual(len(_dones(events)), 1)
        # slice-2 calls (index 3+) must run thinking-OFF.
        self.assertEqual(chat.captured[0][0].get("chat_template_kwargs"),
                         {"enable_thinking": True})
        for options, _ in chat.captured[3:]:
            self.assertNotIn("chat_template_kwargs", options)


class ResumeContextTest(unittest.TestCase):
    def _seed_run(self, tmp, rid, chat=None):
        chat = chat or _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
        return events

    def test_continuation_kwargs_carry_checklist_and_identity(self):
        """(8)+(10) the server-owned resume context lists completed/open items
        and the continuation preserves project_root/permission_mode/thinking."""
        rid = "dlv-resume-1"
        from app.application.task_planner.service import todo_update
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            self._seed_run(tmp, rid)
            todo_update(run_id=rid, items=[
                {"id": "m1", "text": "обследование проекта", "status": "completed", "position": 0},
                {"id": "m2", "text": "каркас и модули", "status": "pending", "position": 1},
            ])
            kwargs = build_continuation_kwargs(rid, approval_wait_seconds=0)
            self.assertEqual(kwargs["run_id"], rid)
            self.assertEqual(str(kwargs["project_root"]), str(Path(tmp).resolve()))
            self.assertEqual(kwargs["permission_mode"], "bypass")
            self.assertTrue(kwargs["resume"])
            self.assertIn("Продолжи", kwargs["user_message"])
            self.assertIn("каркас и модули", kwargs["user_message"])  # first open item
            facts = kwargs["conversation_history"][-1]
            self.assertEqual(facts["role"], "assistant")
            self.assertTrue(facts["content"].startswith("[ПРОВЕРЕННЫЕ ФАКТЫ]"))
            self.assertIn("обследование проекта", facts["content"])
            self.assertIn("completed", facts["content"])
            self.assertIn("a.txt", facts["content"])

    def test_resume_slice_does_not_redo_completed_item(self):
        """(8) integration: slice 1 plans the checklist and completes one item;
        the continuation slice receives completed/open state in its context and
        continues the OPEN item without re-writing the completed one."""
        rid = "dlv-resume-2"
        chat = _ScriptChat([
            _call("todo_update", items=[
                {"id": "m1", "text": "создан a.txt", "status": "pending", "position": 0},
                {"id": "m2", "text": "создан out.txt", "status": "pending", "position": 1},
            ]),
            _call("write_file", path="a.txt", content="A"),
            _call("todo_update", updates=[{"id": "m1", "status": "completed"}]),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),                    # slice 1 → timeout
            _call("write_file", path="out.txt", content="done"),  # slice 2: open milestone only
            _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            self.assertTrue((Path(tmp) / "out.txt").is_file())
        self.assertEqual(len(_continuings(events)), 1)
        writes = [e for e in events if e.get("type") == "tool_call"
                  and e.get("tool") == "write_file"
                  and (e.get("arguments") or {}).get("path") == "a.txt"]
        self.assertEqual(len(writes), 1, "the completed work must not be redone in slice 2")
        # The continuation message names the first OPEN item and the slice-2
        # context carries the server-owned digest with the completed one.
        slice2_messages = chat.captured[4][1]
        flat = "\n".join(str(m.get("content") or "") for m in slice2_messages)
        self.assertIn("создан out.txt", flat)   # open item offered to continue
        self.assertIn("создан a.txt", flat)     # completed item listed as done
        # The facts block was re-tagged to a SYSTEM message by _coerce_history
        # (prefix stripped, authoritative framing added) — assert its payload.
        self.assertIn("Чеклист", flat)
        self.assertTrue(any(m.get("role") == "system" and "Затронутые файлы" in str(m.get("content"))
                            for m in slice2_messages))

    def test_resume_checklist_guard_blocks_replacement_allows_updates(self):
        rid = "dlv-guard-1"
        from app.application.task_planner.service import todo_update
        todo_update(run_id=rid, items=[
            {"id": "m1", "text": "обследование", "status": "completed", "position": 0},
            {"id": "m2", "text": "каркас", "status": "pending", "position": 1},
        ])
        # Replacement (same id, different text) → redirect with the real state.
        blocked = resume_checklist_guard(rid, {"items": [{"id": "m1", "text": "совсем другой план"}]})
        self.assertIsNotNone(blocked)
        self.assertIn("план уже существует", blocked)
        self.assertIn("каркас", blocked)
        # Same-text re-send, status flip → allowed.
        self.assertIsNone(resume_checklist_guard(
            rid, {"items": [{"id": "m2", "text": "каркас", "status": "in_progress"}]}))
        # Pure extension (fresh slot) → allowed.
        self.assertIsNone(resume_checklist_guard(
            rid, {"items": [{"text": "новый шаг: проверка"}]}))
        # Status-only updates → allowed (the advertised legitimate channel).
        self.assertIsNone(resume_checklist_guard(
            rid, {"updates": [{"id": "m2", "status": "completed"}]}))
        # P1 fix: text replacement THROUGH updates is the same forbidden
        # re-planning — blocked like the items route.
        blocked_upd = resume_checklist_guard(
            rid, {"updates": [{"id": "m1", "text": "другой план", "status": "pending"}]})
        self.assertIsNotNone(blocked_upd)
        self.assertIn("план уже существует", blocked_upd)
        # Same-text update (status flip with text echoed) → allowed.
        self.assertIsNone(resume_checklist_guard(
            rid, {"updates": [{"id": "m1", "text": "обследование", "status": "completed"}]}))
        # Unknown id in updates → not a replacement (service will reject it
        # itself with item_not_found); the guard stays silent.
        self.assertIsNone(resume_checklist_guard(
            rid, {"updates": [{"id": "nope", "text": "другой план"}]}))
        self.assertIsNone(resume_checklist_guard(rid, {}))
        # No checklist yet → anything goes.
        self.assertIsNone(resume_checklist_guard(
            "dlv-guard-empty", {"items": [{"text": "план"}]}))

    def test_resumed_slice_redirects_plan_replacement_in_loop(self):
        rid = "dlv-guard-2"
        from app.application.task_planner.service import list_checklist, todo_update
        todo_update(run_id=rid, items=[
            {"id": "m1", "text": "обследование", "status": "completed", "position": 0},
        ])
        chat = _ScriptChat([
            _call("todo_update", items=[{"id": "m1", "text": "другой план", "status": "pending"}]),
            _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            # Seed the journal: resume=True requires a persisted first run.
            seed_chat = _ScriptChat([_final("первый прогон")])
            list(stream_code_agent(
                user_message="подготовка", project_root=tmp, model="test-model",
                max_steps=3, chat_fn=seed_chat, run_id=rid,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            ))
            events = list(stream_code_agent(
                user_message="Продолжи незавершённую задачу.", project_root=tmp,
                model="test-model", max_steps=6, chat_fn=chat, run_id=rid,
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", resume=True,
            ))
        guard_calls = [e for e in events if e.get("type") == "tool_call"
                       and e.get("tool") == "todo_update"
                       and "план уже существует" in str(e.get("result") or "")]
        self.assertEqual(len(guard_calls), 1)
        rows = list_checklist(rid)["items"]
        self.assertEqual(rows[0]["text"], "обследование", "plan must stay untouched")
        self.assertEqual(rows[0]["status"], "completed")

    def test_resumed_slice_redirects_plan_replacement_via_updates(self):
        """P1 fix: the updates-channel rewrite (id + different text) is blocked
        in the loop too, and the durable checklist stays byte-identical."""
        rid = "dlv-guard-3"
        from app.application.task_planner.service import list_checklist, todo_update
        todo_update(run_id=rid, items=[
            {"id": "m1", "text": "обследование", "status": "completed", "position": 0},
            {"id": "m2", "text": "каркас", "status": "pending", "position": 1},
        ])
        before = list_checklist(rid)["items"]
        chat = _ScriptChat([
            _call("todo_update", updates=[
                {"id": "m1", "text": "совсем другой план шаг 1", "status": "pending"},
                {"id": "m2", "text": "другой план шаг 2"},
            ]),
            _final(),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            seed_chat = _ScriptChat([_final("первый прогон")])
            list(stream_code_agent(
                user_message="подготовка", project_root=tmp, model="test-model",
                max_steps=3, chat_fn=seed_chat, run_id=rid,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            ))
            events = list(stream_code_agent(
                user_message="Продолжи незавершённую задачу.", project_root=tmp,
                model="test-model", max_steps=6, chat_fn=chat, run_id=rid,
                approval_wait_seconds=0, auto_remember=False,
                permission_mode="bypass", resume=True,
            ))
        guard_calls = [e for e in events if e.get("type") == "tool_call"
                       and e.get("tool") == "todo_update"
                       and "план уже существует" in str(e.get("result") or "")]
        self.assertEqual(len(guard_calls), 1)
        after = list_checklist(rid)["items"]
        self.assertEqual(
            [(r["id"], r["text"], r["status"]) for r in after],
            [(r["id"], r["text"], r["status"]) for r in before],
            "blocked call must not change the durable checklist",
        )


class ApprovalPolicyTest(unittest.TestCase):
    def test_ask_mode_still_pauses_and_rejection_never_autoresumes(self):
        """(10) approval flow inside a delivery slice is byte-for-byte the
        existing one; a rejected approval ends without continuation."""
        rid = "dlv-approve-1"
        approval = SimpleNamespace(
            status="waiting_approval",
            output={"ok": False, "approval_id": "ap-1", "text": "нужно подтверждение"},
            error="waiting_approval:ap-1",
        )
        chat = _ScriptChat([
            _call("run_bash", command="echo hi"),
            _final("остановился после отказа"),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env(), \
             patch.object(agent_loop, "_kernel_exec", return_value=approval), \
             patch.object(agent_loop, "_approval_status", return_value="rejected"), \
             patch.object(agent_loop, "_APPROVAL_POLL_INTERVAL", 0.01):
            events = list(stream_delivery_session(
                user_message=_STRUCTURED_TASK, project_root=tmp, model="test-model",
                max_steps=6, chat_fn=chat, run_id=rid,
                execution_timeout_seconds=_SLICE_TIMEOUT_S, approval_wait_seconds=5,
                auto_remember=False, permission_mode="ask",
            ))
        self.assertTrue(any(e.get("type") == "approval_pending" for e in events),
                        "ask mode must still pause on approvals")
        self.assertEqual(_continuings(events), [])
        self.assertEqual(len(_dones(events)), 1)


class ContractNoteTest(unittest.TestCase):
    def test_contract_note_does_not_change_taskspec(self):
        """The [delivery-контракт] prefix must never alter criteria/verifier
        derivation — the TaskSpec heuristic sees the same task. (Appending is
        forbidden: trailing text after «Критерии готовности:» would become an
        extra criterion.)"""
        base = derive_task_spec(_STRUCTURED_TASK)
        with_note = derive_task_spec(DELIVERY_CONTRACT_NOTE + _STRUCTURED_TASK)
        self.assertIsNotNone(base)
        self.assertIsNotNone(with_note)
        self.assertEqual(base.success_criteria, with_note.success_criteria)
        self.assertEqual(base.verifiers, with_note.verifiers)
        self.assertIsNone(derive_task_spec(DELIVERY_CONTRACT_NOTE + _SIMPLE_TASK))

    def test_contract_note_is_task_neutral(self):
        """P1/P2 fix: the server-owned contract must not impose project-build
        milestones — a structured BUGFIX gets no scaffold/packaging/deploy
        steps from the server; the checklist is derived from the task itself."""
        low = DELIVERY_CONTRACT_NOTE.lower()
        for imposed in ("архитектур", "каркас", "вертикальн", "упаковк",
                        "scaffold", "readme", "вехам"):
            self.assertNotIn(imposed, low,
                             f"contract note must not impose '{imposed}'")
        # deploy is mentioned ONLY as explicitly-requested-by-the-user
        self.assertIn("деплой", low)
        self.assertIn("явно", low)
        # the steps must be derived from the actual task
        self.assertIn("самой задачи", low)
        bugfix = (
            "Исправь parser.py.\n\n"
            "Цель:\nПочинить парсер конфигурации.\n\n"
            "Критерии готовности:\n1. pytest проходит\n"
        )
        spec = derive_task_spec(DELIVERY_CONTRACT_NOTE + bugfix)
        self.assertIsNotNone(spec, "structured bugfix stays delivery-shaped")
        # the injected message carries zero project-build milestone vocabulary
        effective = (DELIVERY_CONTRACT_NOTE + bugfix).lower()
        for imposed in ("архитектур", "каркас", "упаковк", "scaffold"):
            self.assertNotIn(imposed, effective)


class BoundaryJournalWriteTest(unittest.TestCase):
    def test_boundary_event_write_is_append_only(self):
        """P2 fix: the delivery_continuing boundary record goes to events.jsonl
        ONLY — state.json (status/resumable/…) stays byte-identical, so the
        unlocked observer write can never race a concurrent manual resume."""
        rid = "dlv-boundary-1"
        from app.application.code_agent.run_journal import RunJournal
        chat = _ScriptChat([_call("write_file", path="a.txt", content="A"), _final()])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            list(stream_code_agent(
                user_message="подготовка", project_root=tmp, model="test-model",
                max_steps=5, chat_fn=chat, run_id=rid,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            ))
            journal = RunJournal.load(rid)
            state_before = journal.state_path.read_bytes()
            events_before = journal.events_path.read_bytes()
            journal.append_external_event({
                "type": "delivery_continuing", "run_id": rid,
                "slice": 1, "next_slice": 2, "stop_reason": "timeout",
                "progress": {"new_touched_paths": 1},
            })
            self.assertEqual(journal.state_path.read_bytes(), state_before,
                             "state.json must not change on a boundary write")
            events_after = journal.events_path.read_bytes()
            self.assertGreater(len(events_after), len(events_before))
            import json as _json
            last = _json.loads(events_after.decode("utf-8").strip().splitlines()[-1])
            self.assertEqual(last["type"], "delivery_continuing")
            self.assertEqual(last["run_id"], rid)


class ManualResumeTest(unittest.TestCase):
    def test_manual_resume_streams_run_resumed_and_finishes(self):
        """(14) the Resume path drives the same session machinery: run_resumed
        first, enriched context, single done."""
        rid = "dlv-manual-1"
        chat = _ScriptChat([
            _call("write_file", path="a.txt", content="A"),
            (_TIMEOUT_BUMP, _call("glob", pattern="*")),
        ])
        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            first = _run_session(_STRUCTURED_TASK, chat, tmp, run_id=rid)
            # exhausted: slice 2 had no progress → honest partial, resumable
            self.assertTrue(_dones(first)[0].get("resumable"))
            resume_chat = _ScriptChat([
                _call("write_file", path="out.txt", content="done"),
                _final(),
            ])
            events = list(stream_resume_session(
                rid, approval_wait_seconds=0, chat_fn=resume_chat,
            ))
            self.assertTrue((Path(tmp) / "out.txt").is_file())
        self.assertEqual(events[0]["type"], "run_resumed")
        self.assertEqual(len(_dones(events)), 1)
        self.assertEqual(_dones(events)[0]["stop_reason"], "answer")


if __name__ == "__main__":
    unittest.main()
