from __future__ import annotations

import importlib
import copy
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.context.compaction import maybe_compact
from app.application.context.memory import (
    add_ledger_entry,
    include_pinned_in_context,
    pin_item,
    rollback_compression,
    unpin_item,
    validate_after_compression,
)
from app.application.context.packer import build_final_messages, pack_context, pack_message_context
from app.application.context.policy import prepare_compression, should_compress
from app.application.context.profile import get_active_context_profile
from app.application.context.rolling_summary import (
    ROLLING_SUMMARY_FIELDS,
    generate_rolling_summary,
    validate_rolling_summary,
)
from app.application.context.usage import calculate_budget, check_context_limit, get_context_usage


class ContextProfileAndBudgetTest(unittest.TestCase):
    def test_agent_entrypoint_defaults_are_128k(self) -> None:
        from app.application.chat.runtime import run_agent
        from app.application.code_agent.agent_loop import run_code_agent, stream_code_agent

        for function in (run_agent, run_code_agent, stream_code_agent):
            self.assertEqual(inspect.signature(function).parameters["num_ctx"].default, 131_072)

    def test_256k_server_profile_is_not_reduced_to_16k(self) -> None:
        cfg = SimpleNamespace(context_window=16_384, model="local-model", base_url="http://server/v1")
        with patch("app.infrastructure.llm.openai_compatible.local_llm_config", return_value=cfg), patch(
            "app.infrastructure.llm.openai_compatible.list_models",
            return_value=[{"name": "local-model", "n_ctx": 262_144}],
        ):
            profile = get_active_context_profile("local-model")
        self.assertEqual(profile["ctx_size"], 262_144)
        self.assertEqual(profile["mode"], "256k-stress")
        self.assertEqual(profile["timeout_policy"]["long_context"], 900)

    def test_budget_formula_reserves_output_system_and_margin(self) -> None:
        budget = calculate_budget(
            ctx_size=131_072,
            reserved_output_tokens=4096,
            reserved_system_tokens=4096,
            safety_margin_tokens=2048,
        )
        self.assertEqual(budget["available_input_tokens"], 120_832)

    def test_usage_has_every_required_category(self) -> None:
        usage = get_context_usage(
            [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "older"},
                {"role": "user", "content": "current"},
                {"role": "tool", "content": "result"},
            ],
            ctx_size=131_072,
            extra_categories={"rag": "chunk", "ocr": "scan", "vision": "image", "code": "def f(): pass"},
        )
        for category in ("system", "chat", "user_input", "tools", "rag", "ocr", "vision", "code"):
            self.assertGreater(usage["breakdown"][category], 0)


class CompressionPolicyTest(unittest.TestCase):
    def test_thresholds_match_acceptance_policy(self) -> None:
        expected = {
            59: ("normal", False, True),
            60: ("monitor", False, True),
            75: ("prepare", False, True),
            85: ("auto_compression", True, True),
            90: ("strong_compression", True, True),
            95: ("critical", True, False),
        }
        for percent, (status, compress, allowed) in expected.items():
            usage = {"percent": percent}
            self.assertEqual(check_context_limit(usage)["status"], status)
            self.assertEqual(should_compress(usage), compress)
            self.assertEqual(prepare_compression(usage)["allowed"], allowed)

    def test_compaction_audit_and_pinned_order(self) -> None:
        messages = [{"role": "system", "content": "rules"}]
        for index in range(8):
            messages.append({"role": "user", "content": f"u{index}", "_msg_id": f"u{index}"})
            messages.append({"role": "assistant", "content": f"a{index}", "_msg_id": f"a{index}"})
        events: list[dict] = []
        compacted, changed = maybe_compact(
            messages,
            100_000,
            "model",
            None,
            lambda **_: {"ok": True, "summary": "summary"},
            threshold=0,
            keep_pairs=2,
            pinned_message_ids={"u1"},
            audit_sink=events.append,
        )
        self.assertTrue(changed)
        # The rolling summary is now a leading assistant message with no _msg_id;
        # filter to the id-bearing turns to check pinned-first / recent-last order.
        non_system = [message for message in compacted if message["role"] != "system" and "_msg_id" in message]
        self.assertEqual(non_system[0]["_msg_id"], "u1")
        self.assertEqual(non_system[-1]["_msg_id"], "a7")
        self.assertEqual(events[0]["protected_count"], 1)
        for field in (
            "compression_id", "tokens_before", "tokens_after", "compression_ratio",
            "rolling_summary_before_hash", "rolling_summary_after_hash", "trigger_reason",
        ):
            self.assertIn(field, events[0])


class ContextPackerAndMemoryTest(unittest.TestCase):
    def test_packer_keeps_protected_and_prioritises_current_request(self) -> None:
        packed = pack_context([
            {"id": "rag", "category": "rag", "content": "R" * 4000},
            {"id": "system", "category": "system", "content": "rules", "protected": True},
            {"id": "user", "category": "user_input", "content": "fix it", "protected": True},
        ], safe_input_budget=20)
        ids = [block["id"] for block in packed["blocks"]]
        self.assertIn("system", ids)
        self.assertIn("user", ids)
        self.assertTrue(packed["compressed_blocks"] or packed["dropped_blocks"])
        self.assertEqual([message["content"] for message in build_final_messages(packed)][:2], ["rules", "fix it"])

    def test_message_packer_preserves_tool_order_and_artifact_reference(self) -> None:
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "user", "content": "finish"},
        ]
        packed = pack_message_context(messages, safe_input_budget=1000)
        final = build_final_messages(packed)
        self.assertEqual([message["role"] for message in final], ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(final[2]["tool_calls"][0]["id"], "c1")

        artifact = pack_context([{
            "id": "code", "category": "code", "content": "X" * 4000,
            "artifact_ref": "artifacts/large.py",
        }], safe_input_budget=100)
        self.assertIn("artifacts/large.py", artifact["referenced_artifacts"])
        self.assertIn("[Artifact: artifacts/large.py]", artifact["blocks"][0]["content"])

    def test_structured_summary_preserves_profile_files_errors_and_tests(self) -> None:
        profile = {
            "active_model": "local-model",
            "ctx_size": 131_072,
            "main_endpoint": "http://192.168.88.15:8000/v1",
        }
        ledger: list[dict] = []
        ledger = add_ledger_entry(ledger, entry_type="test", action="pytest", result="2944 passed", files=[r"D:\AIWork\Elira_AI\a.py"])
        ledger = add_ledger_entry(ledger, entry_type="error", action="endpoint", result="HTTP 404", errors=["HTTP 404"])
        summary = generate_rolling_summary(
            [{"role": "user", "content": "Закончи стабилизацию"}],
            active_context_profile=profile,
            task_ledger=ledger,
        )
        self.assertEqual(set(summary), set(ROLLING_SUMMARY_FIELDS))
        self.assertTrue(validate_rolling_summary(summary)["ok"])
        self.assertIn("2944 passed", summary["test_results"])
        self.assertIn("HTTP 404", summary["known_errors"])
        self.assertIn("http://192.168.88.15:8000/v1", summary["active_endpoints"])

    def test_pin_unpin_and_consistency_rollback(self) -> None:
        pins = pin_item([], content="pytest -q", kind="command", source_id="m1")
        self.assertIn("pytest -q", include_pinned_in_context(pins))
        self.assertEqual(unpin_item(pins, pins[0]["id"]), [])
        before = {
            "rolling_summary": {"task_goal": "ship"},
            "active_context_profile": {"active_model": "m", "ctx_size": 131072, "main_endpoint": "http://x/v1"},
            "pinned_items": pins,
            "task_ledger": [{"step_id": 1}],
        }
        after = {"rolling_summary": {}, "active_context_profile": {}, "pinned_items": [], "task_ledger": []}
        check = validate_after_compression(before, after)
        self.assertFalse(check["ok"])
        self.assertEqual(rollback_compression(before, after)["rolling_summary"]["task_goal"], "ship")

    def test_consistency_checks_endpoints_and_structured_memory(self) -> None:
        before = {
            "rolling_summary": {
                "task_goal": "ship", "known_errors": ["404"], "next_steps": ["test"],
                "important_files": ["a.py"], "test_results": ["passed"],
                "decisions": ["keep API"], "risks": ["timeout"],
            },
            "active_context_profile": {
                "active_model": "m", "ctx_size": 131072, "main_endpoint": "http://main/v1",
                "ocr_endpoint": "http://ocr", "vision_endpoint": "http://vision",
                "embedding_endpoint": "http://embed/v1",
            },
            "pinned_items": [], "task_ledger": [],
        }
        after = copy.deepcopy(before)
        after["rolling_summary"]["test_results"] = []
        after["active_context_profile"]["ocr_endpoint"] = ""
        check = validate_after_compression(before, after)
        self.assertFalse(check["ok"])
        self.assertIn("active_context_profile.ocr_endpoint", check["missing"])
        self.assertIn("rolling_summary.test_results", check["missing"])


class PersistentTaskContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self.tmp.name
        from app.core import data_files
        from app.application.code_agent import sessions

        importlib.reload(data_files)
        importlib.reload(sessions)
        self.sessions = sessions

    def tearDown(self) -> None:
        os.environ.pop("ELIRA_DATA_DIR", None)
        from app.core import data_files
        from app.application.code_agent import sessions

        # Restore module-level data paths so later tests do not inherit the
        # temporary directory after it is removed.
        importlib.reload(data_files)
        importlib.reload(sessions)
        self.tmp.cleanup()

    def test_manual_compression_persists_summary_ledger_and_audit(self) -> None:
        from app.application.context.task_state import compress_now, load_task_context

        session = self.sessions.create_session(title="task", model="local-model", num_ctx=131_072)
        turns = []
        for index in range(8):
            turns.extend([
                {"kind": "user", "id": f"u{index}", "text": f"task {index}"},
                {"kind": "agent", "id": f"a{index}", "text": f"result {index}"},
            ])
        self.sessions.update_session(session["id"], {"turns": turns})
        result = compress_now(session["id"])
        self.assertTrue(result["ok"])
        restored = load_task_context(session["id"])
        self.assertTrue(restored["rolling_summary"]["task_goal"])
        self.assertEqual(restored["task_ledger"][-1]["type"], "compression")
        self.assertTrue(restored["compression_events"][-1]["validation_ok"])
        self.assertEqual(restored["last_context_usage"]["current_tokens"], result["event"]["tokens_after"])
        self.assertEqual(restored["final_context_summary"], restored["rolling_summary"])

    def test_context_routes_read_compact_and_unpin_persisted_state(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.routes.code_agent_routes import router
        from app.application.context.memory import pin_item
        from app.application.context.task_state import load_task_context, save_task_context

        session = self.sessions.create_session(title="task", model="local-model", num_ctx=131_072)
        self.sessions.update_session(session["id"], {
            "turns": [
                {"kind": "user", "id": "u1", "text": "ship the fix"},
                {"kind": "agent", "id": "a1", "text": "working"},
            ],
        })
        state = load_task_context(session["id"])
        self.assertIsNotNone(state)
        assert state is not None
        state["pinned_items"] = pin_item([], content="keep this", source_id="u1")
        save_task_context(session["id"], state)
        pin_id = state["pinned_items"][0]["id"]

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        read = client.get(f"/api/code-agent/sessions/{session['id']}/context")
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["state"]["pinned_items"][0]["id"], pin_id)

        compact = client.post(f"/api/code-agent/sessions/{session['id']}/context/compact")
        self.assertEqual(compact.status_code, 200)
        self.assertTrue(compact.json()["ok"])

        unpin = client.delete(
            f"/api/code-agent/sessions/{session['id']}/context/pins/{pin_id}",
        )
        self.assertEqual(unpin.status_code, 200)
        self.assertEqual(load_task_context(session["id"])["pinned_items"], [])


if __name__ == "__main__":
    unittest.main()
