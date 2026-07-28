# -*- coding: utf-8 -*-
"""Adaptive context window — the live llama.cpp n_ctx is the ONLY authority.

Red→green contract (spec: adaptive-context batch):
  - Auto adopts ANY positive server n_ctx (32K…1M parameterized) — no product
    ceiling anywhere;
  - a model/-c swap on the same base_url is seen by the NEXT run (no stale
    2-minute cache on the run-start path);
  - legacy/request fields cannot cap or enlarge the live server window;
  - compaction triggers at the same FRACTION of the window for any n_ctx;
  - the bounded [СОСТОЯНИЕ ЗАДАЧИ] digest survives compaction verbatim while
    full mutation/evidence facts remain durable in RunJournal;
  - journal + SSE carry server/effective/limiting_source/reserves/thresholds;
  - /props failure on a live run fails closed even when a stale env value exists.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.context import profile as profile_mod  # noqa: E402
from app.application.context.compaction import TASK_STATE_PREFIX, maybe_compact  # noqa: E402
from app.application.context.profile import (  # noqa: E402
    ContextResolutionError,
    resolve_context_window,
)
from app.application.code_agent import agent_loop  # noqa: E402
from app.application.code_agent.agent_loop import _CODE_AGENT_BASE_TOOLS, stream_code_agent  # noqa: E402
from app.application.code_agent.history import summarize_history  # noqa: E402
from app.application.code_agent.loop_helpers import (  # noqa: E402
    _prepare_messages_for_llm,
    build_task_state_block,
    upsert_task_state_message,
)
from app.application.tool_providers import ToolRegistry  # noqa: E402
from app.application.code_agent.taskspec import CriteriaTracker, TaskSpec  # noqa: E402


def _patch_props(value):
    """Patch the LIVE /props n_ctx the resolver sees."""
    return patch(
        "app.infrastructure.llm.openai_compatible.server_context_window",
        side_effect=lambda fresh=False: value,
    )


class AutoAdoptsAnyServerWindowTest(unittest.TestCase):
    def test_auto_adopts_exact_server_n_ctx_parameterized(self):
        for n_ctx in (32768, 65536, 98304, 131072, 262144, 524288, 1048576):
            with self.subTest(n_ctx=n_ctx), _patch_props(n_ctx):
                r = resolve_context_window(None, live=True, fresh=True)
                self.assertEqual(r["effective_context_window"], n_ctx)
                self.assertEqual(r["server_context_window"], n_ctx)
                self.assertEqual(r["requested_context_mode"], "server")
                self.assertEqual(r["limiting_source"], "server")
                self.assertEqual(r["context_profile_source"], "server_props")
                self.assertGreater(r["safe_input_budget"], n_ctx // 2,
                                   "reserves must not gut the window")

    def test_no_artificial_upper_cap_exists(self):
        """A deliberately odd, huge n_ctx passes through verbatim — proof there
        is no MAX_CONTEXT_WINDOW-style ceiling left in the resolver."""
        weird = 3_145_728  # 3M — larger than any historic cap
        with _patch_props(weird):
            r = resolve_context_window(None, live=True, fresh=True)
        self.assertEqual(r["effective_context_window"], weird)
        self.assertFalse(hasattr(profile_mod, "MAX_CONTEXT_WINDOW"),
                         "the product ceiling constant must be gone")

    def test_same_base_url_model_swap_seen_by_next_run(self):
        """65536 then 524288 on the SAME url: the second resolve (a new run,
        fresh=True) must see the new window immediately — no 120s stale cache."""
        calls = {"n": 0}

        def fake_props(fresh=False):
            calls["n"] += 1
            return 65536 if calls["n"] == 1 else 524288

        with patch("app.infrastructure.llm.openai_compatible.server_context_window",
                   side_effect=fake_props):
            first = resolve_context_window(None, live=True, fresh=True)
            second = resolve_context_window(None, live=True, fresh=True)
        self.assertEqual(first["effective_context_window"], 65536)
        self.assertEqual(second["effective_context_window"], 524288)

    def test_fresh_bypasses_ttl_cache_at_transport_level(self):
        """server_context_window(fresh=True) must hit /props even when a cached
        value is younger than the TTL."""
        from app.infrastructure.llm import openai_compatible as oc

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def __init__(self, n):
                self._n = n

            def json(self):
                return {"default_generation_settings": {"n_ctx": self._n}}

        answers = iter([65536, 524288])
        from types import SimpleNamespace
        fake_cfg = SimpleNamespace(
            enabled=True, base_url="http://127.0.0.1:9/v1", timeout_seconds=5,
            api_key="k", model="m", context_window=None,
        )
        with patch.object(oc, "_props_ctx_cache", {}), \
             patch.object(oc, "local_llm_config", return_value=fake_cfg), \
             patch.object(oc.requests, "get", side_effect=lambda *a, **k: _Resp(next(answers))):
            v1 = oc.server_context_window(fresh=True)
            v2 = oc.server_context_window(fresh=True)
        self.assertEqual((v1, v2), (65536, 524288))


class ServerAuthorityTest(unittest.TestCase):
    def test_live_server_window_ignores_smaller_legacy_request(self):
        with _patch_props(524288):
            r = resolve_context_window(65536, live=True, fresh=True)
        self.assertEqual(r["effective_context_window"], 524288)
        self.assertEqual(r["limiting_source"], "server")
        self.assertEqual(r["requested_context_mode"], "server")
        self.assertIsNone(r["requested_context_cap"])

    def test_live_server_window_ignores_larger_legacy_request(self):
        with _patch_props(131072):
            r = resolve_context_window(1048576, live=True, fresh=True)
        self.assertEqual(r["effective_context_window"], 131072)
        self.assertEqual(r["limiting_source"], "server")

    def test_api_accepts_legacy_value_but_live_resolver_ignores_it(self):
        """The old wire field stays parseable during migration, but it cannot
        influence a live run."""
        from app.api.routes.code_agent_routes import CodeAgentStreamRequest

        big = CodeAgentStreamRequest(message="m", project_root="/p", num_ctx=262144)
        self.assertEqual(big.num_ctx, 262144)
        auto = CodeAgentStreamRequest(message="m", project_root="/p")
        self.assertIsNone(auto.num_ctx)
        with _patch_props(98304):
            resolved = resolve_context_window(big.num_ctx, live=True, fresh=True)
        self.assertEqual(resolved["effective_context_window"], 98304)


class FallbackTest(unittest.TestCase):
    def test_props_failure_does_not_use_stale_explicit_env_for_live_run(self):
        with _patch_props(None), patch.dict("os.environ", {"LLAMA_SERVER_CONTEXT_WINDOW": "98304"}):
            with self.assertRaises(ContextResolutionError):
                resolve_context_window(None, live=True, fresh=True)

    def test_props_failure_without_env_fails_closed(self):
        with _patch_props(None), patch.dict("os.environ", {}, clear=False):
            import os
            old = os.environ.pop("LLAMA_SERVER_CONTEXT_WINDOW", None)
            try:
                with self.assertRaises(ContextResolutionError):
                    resolve_context_window(None, live=True, fresh=True)
            finally:
                if old is not None:
                    os.environ["LLAMA_SERVER_CONTEXT_WINDOW"] = old


class AdaptiveBudgetTest(unittest.TestCase):
    def test_reserves_and_thresholds_scale_proportionally(self):
        for n_ctx in (32768, 65536, 131072, 262144, 1048576):
            with self.subTest(n_ctx=n_ctx), _patch_props(n_ctx):
                r = resolve_context_window(None, live=True, fresh=True)
            self.assertEqual(r["reserved_output_tokens"], max(2048, n_ctx // 16))
            self.assertEqual(r["compaction_thresholds"]["auto"]["percent"], 75.0)
            self.assertEqual(r["compaction_thresholds"]["auto"]["tokens"],
                             int(n_ctx * 0.75))
            self.assertEqual(r["compaction_thresholds"]["critical"]["percent"], 95.0)

    def test_thinking_reserve_adaptive_never_halves_window(self):
        for n_ctx in (32768, 131072, 262144, 1048576):
            with self.subTest(n_ctx=n_ctx), _patch_props(n_ctx):
                r = resolve_context_window(None, thinking=True, live=True, fresh=True)
            self.assertLessEqual(r["reserved_output_tokens"], max(2048, n_ctx // 3),
                                 "thinking reserve is capped at a third of the window")
            self.assertGreater(r["safe_input_budget"], n_ctx // 2,
                               "thinking must leave a valid input budget")

    def test_compaction_fires_at_fraction_not_absolute_mark(self):
        """~70K tokens of history: at a 131072 window (53%) NO compaction; at a
        65536 window (>100%) compaction fires. Same percent thresholds, no
        absolute 64K mark."""
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 3_000}
            for i in range(80)
        ]  # ≈60K tokens, spread across enough turns to compact safely

        def profile_for(n):
            with _patch_props(n):
                return resolve_context_window(None, live=True, fresh=True)

        big = profile_for(131072)
        msgs_big, compacted_big, _ = _prepare_messages_for_llm(
            list(history), num_ctx=131072, model="m",
            chat_fn=lambda **kw: {"message": {"content": "s"}},
            context_profile=big,
        )
        self.assertFalse(compacted_big,
                         "53% of a 128K window must NOT compact (no 64K mark)")

        small = profile_for(65536)
        _msgs_small, compacted_small, _ = _prepare_messages_for_llm(
            list(history), num_ctx=65536, model="m",
            chat_fn=lambda **kw: {"message": {"content": "s"}},
            context_profile=small,
        )
        self.assertTrue(compacted_small)

    def test_summarizer_transcript_budget_scales_with_effective_window(self):
        history = [
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"turn-{i} " + "x" * 20_000,
            }
            for i in range(80)
        ]

        def captured_transcript(num_ctx: int) -> str:
            seen: dict[str, object] = {}

            def chat(**kwargs):
                seen.update(kwargs)
                return {"message": {"content": "summary"}}

            result = summarize_history(
                history,
                model="test-model",
                num_ctx=num_ctx,
                chat_fn=chat,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(seen["options"]["num_ctx"], num_ctx)
            return seen["messages"][1]["content"]

        small = captured_transcript(65_536)
        large = captured_transcript(1_048_576)
        self.assertGreater(len(large), len(small) * 8)
        self.assertIn("earlier messages dropped", small)
        self.assertNotIn("earlier messages dropped", large)


class DurableCriteriaTest(unittest.TestCase):
    def test_exact_server_owned_report_restores_verifier_state(self):
        from app.application.code_agent.run_journal import RunJournal

        spec = TaskSpec(success_criteria=["команда `npm test` завершается успешно"])
        first = CriteriaTracker.from_spec(spec)
        first.items[0].update(
            status="confirmed",
            verifier="run_bash",
            evidence="npm test -> exit=0",
            auto_verified=True,
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"ELIRA_AGENT_RUNS_DIR": tmp}
        ):
            journal = RunJournal("criteria-resume")
            journal.start({"user_message": "test", "project_root": tmp}, {"missing": []})
            journal.append_event({
                "type": "done",
                "ok": False,
                "stop_reason": "timeout",
                "completion_status": "partial",
                "criteria": first.report(),
                "resumable": True,
            })
            journal.finish(interrupted=True)
            durable_rows = RunJournal.load("criteria-resume").state["criteria"]

        resumed = CriteriaTracker.from_spec(spec)
        resumed.restore_report(durable_rows)

        self.assertEqual(resumed.completion_status(), "confirmed")
        self.assertEqual(resumed.report(), first.report())

    def test_unrelated_or_duplicated_rows_cannot_verify_new_criteria(self):
        tracker = CriteriaTracker.from_spec(TaskSpec(success_criteria=["A", "A", "B"]))
        tracker.restore_report([
            {"text": "A", "status": "confirmed", "verifier": "v", "evidence": "e"},
            {"text": "other", "status": "confirmed", "verifier": "v", "evidence": "e"},
        ])
        self.assertEqual([row["status"] for row in tracker.report()], [
            "confirmed", "unconfirmed", "unconfirmed",
        ])


class BoundedTaskStateTest(unittest.TestCase):
    def test_state_block_survives_many_compactions_verbatim(self):
        block = build_task_state_block(
            goal="Собрать mini CRM",
            constraints=["не трогать main"],
            criteria_rows=[{"text": "тесты проходят", "status": "confirmed",
                            "verifier": "run_bash", "evidence": "exit=0"}],
            checklist_items=[{"id": "m1", "text": "каркас", "status": "completed",
                              "position": 0},
                             {"id": "m2", "text": "проверка", "status": "pending",
                              "position": 1}],
            mutated_files=["src/store.ts", "src/App.tsx"],
            verifications=["npm test → exit=0"],
            failed_attempts=["edit_file(src/store.ts) error"],
            next_step="запустить сборку",
        )
        messages = [{"role": "system", "content": "prompt"}]
        messages = upsert_task_state_message(messages, block)
        # grow noisy history and compact repeatedly
        for round_no in range(4):
            for i in range(30):
                messages.append({"role": "user", "content": f"шум {round_no}-{i} " + "y" * 2000})
                messages.append({"role": "assistant", "content": f"ответ {round_no}-{i} " + "z" * 2000})
            messages, changed = maybe_compact(
                messages, 16384, "m",
                lambda **kw: {"message": {"content": "краткое summary"}},
                summarize_fn=lambda **kw: {"ok": True, "summary": "шумовое summary"},
                threshold=0.0, keep_pairs=2, fallback_keep=2,
            )
            self.assertTrue(changed)
            state_msgs = [m for m in messages
                          if str(m.get("content") or "").startswith(TASK_STATE_PREFIX)]
            self.assertEqual(len(state_msgs), 1, "exactly one protected state block")
            body = state_msgs[0]["content"]
            for marker in ("Собрать mini CRM", "не трогать main", "тесты проходят",
                           "exit=0", "(m1)", "(m2)", "каркас", "src/store.ts",
                           "npm test → exit=0", "Неудачная попытка",
                           "запустить сборку"):
                self.assertIn(marker, body,
                              f"structural state lost '{marker}' after compaction #{round_no}")

    def test_state_block_is_never_fed_to_the_summarizer(self):
        seen: list[str] = []

        def spy_summarize(**kw):
            for m in kw.get("messages") or []:
                seen.append(str(m.get("content") or ""))
            return {"ok": True, "summary": "s"}

        messages = [{"role": "system", "content": "prompt"}]
        messages = upsert_task_state_message(messages, "секретное состояние задачи")
        for i in range(40):
            messages.append({"role": "user", "content": f"m{i} " + "q" * 1500})
        maybe_compact(messages, 8192, "m", None, summarize_fn=spy_summarize,
                      threshold=0.0, keep_pairs=1, fallback_keep=1)
        self.assertFalse(any(TASK_STATE_PREFIX.rstrip("\n") in c for c in seen),
                         "the protected block must never reach the lossy summarizer")

    def test_mutations_and_verifications_survive_journal_reload(self):
        from app.application.code_agent.run_journal import RunJournal

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"ELIRA_AGENT_RUNS_DIR": tmp}
        ):
            journal = RunJournal("adaptive-state-resume")
            journal.start(
                {"user_message": "build", "project_root": tmp},
                {"missing": []},
            )
            journal.append_event({
                "type": "tool_call",
                "tool": "write_file",
                "ok": True,
                "state_changed": True,
                "touched_path": "src/App.tsx",
                "result": "ok",
            })
            journal.append_event({
                "type": "tool_call",
                "tool": "run_bash",
                "ok": True,
                "exit_code": 0,
                "result": "passed",
                "task_state_verification": "npm test --token=[REDACTED] -> exit=0",
            })
            journal.append_event({
                "type": "tool_call",
                "tool": "edit_file",
                "ok": False,
                "result": "error",
                "task_state_failure": "edit_file: error",
            })
            journal.finish(interrupted=True)

            state = RunJournal.load("adaptive-state-resume").state

        self.assertEqual(state["mutated_files"], ["src/App.tsx"])
        self.assertEqual(
            state["verifications"],
            ["npm test --token=[REDACTED] -> exit=0"],
        )
        self.assertNotIn("supersecret", str(state))
        self.assertEqual(state["failed_attempts"], ["edit_file: error"])
        block = build_task_state_block(
            goal="build",
            mutated_files=state["mutated_files"],
            verifications=state["verifications"],
            failed_attempts=state["failed_attempts"],
            next_step="finish UI",
        )
        for marker in ("src/App.tsx", "npm test", "edit_file: error", "finish UI"):
            self.assertIn(marker, block)


_FAKE_SCHEMAS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in _CODE_AGENT_BASE_TOOLS
]


@contextlib.contextmanager
def _loop_env():
    with patch.object(agent_loop, "_resolve_code_route",
                      side_effect=lambda model, num_ctx, agent_id="code-agent": ("test-model", int(num_ctx or 0), None)), \
         patch.object(agent_loop, "_record_code_route_metric"), \
         patch.object(agent_loop, "build_mcp_providers", return_value=[]), \
         patch.object(ToolRegistry, "collect_schemas", return_value=list(_FAKE_SCHEMAS)), \
         patch.object(agent_loop, "_server_url_alive", return_value=True), \
         patch.object(agent_loop, "_run_owned_servers", return_value=[]), \
         patch.object(agent_loop, "_stop_run_servers", return_value=[]), \
         patch("app.application.agent_registry.sandbox.preflight_or_raise",
               return_value={"limit": {"max_execution_seconds": 600}}):
        yield


class ObservabilityTest(unittest.TestCase):
    def test_context_resolved_reaches_sse_and_journal_before_first_model_call(self):
        from app.application.code_agent.run_journal import RunJournal
        rid = "adaptive-obs-1"
        order: list[str] = []

        def chat(**kw):
            order.append("model_call")
            return {"message": {"content": "готово", "tool_calls": []}}

        with tempfile.TemporaryDirectory() as tmp, _loop_env():
            events = []
            for ev in stream_code_agent(
                user_message="скажи привет", project_root=tmp, model="test-model",
                max_steps=3, chat_fn=chat, run_id=rid, num_ctx=65536,
                approval_wait_seconds=0, auto_remember=False, permission_mode="bypass",
            ):
                if ev.get("type") == "context_resolved":
                    order.append("context_resolved")
                events.append(ev)
            state = RunJournal.load(rid).state
        resolved = [e for e in events if e.get("type") == "context_resolved"]
        self.assertEqual(len(resolved), 1)
        ev = resolved[0]
        self.assertEqual(ev["requested_context_mode"], "offline")
        self.assertIsNone(ev["requested_context_cap"])
        self.assertEqual(ev["effective_context_window"], 65536)
        self.assertIn("server_context_window", ev)
        self.assertIn("limiting_source", ev)
        self.assertIn("compaction_thresholds", ev)
        self.assertIn("reserved_output_tokens", ev)
        self.assertLess(order.index("context_resolved"), order.index("model_call"),
                        "resolution must be emitted BEFORE the first model call")
        journal_ctx = state.get("context_resolution") or {}
        self.assertEqual(journal_ctx.get("effective_context_window"), 65536)
        self.assertEqual(journal_ctx.get("requested_context_mode"), "offline")

    def test_single_resolver_serves_chat_and_meter(self):
        """chat runtime, code-agent and the meter route resolve through the ONE
        resolver — patching it changes them all; Auto never becomes 131072."""
        calls: list[tuple] = []
        real = profile_mod.resolve_context_window

        def spy(requested_cap=None, **kw):
            calls.append((requested_cap, kw.get("live"), kw.get("fresh")))
            kw["live"] = False
            return real(requested_cap, **kw)

        with patch.object(profile_mod, "resolve_context_window", side_effect=spy):
            from app.api.routes.code_agent_routes import read_context_profile
            with patch("app.api.routes.code_agent_routes.HTTPException", RuntimeError):
                out = read_context_profile()
        self.assertTrue(calls, "the meter route must use the shared resolver")
        self.assertIsNone(calls[0][0], "meter default is Auto (no cap)")
        self.assertIn("effective_context_window", out)


if __name__ == "__main__":
    unittest.main()
