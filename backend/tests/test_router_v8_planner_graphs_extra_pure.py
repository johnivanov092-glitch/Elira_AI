"""Tests for pure helpers across five modules.

  domain/agents/router.py          — choose_v8_strategy
  domain/agents/planner_graphs.py  — planner_default_steps, task_graph_default
  application/rag_memory_service/runtime.py — _cosine_sim
  application/persona/store.py     — row_to_version
  application/skills_extra/runtime.py — encrypt_text, decrypt_text

All functions are pure (no DB, no HTTP; the one DB call in choose_v8_strategy
is wrapped in try/except and falls back gracefully to an empty list).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.agents.router import choose_v8_strategy  # noqa: E402
from app.domain.agents.planner_graphs import (  # noqa: E402
    planner_default_steps,
    task_graph_default,
)
from app.application.rag_memory.service import _cosine_sim  # noqa: E402
from app.application.persona.store import row_to_version  # noqa: E402
from app.application.skills_extra.runtime import (  # noqa: E402
    encrypt_text,
    decrypt_text,
)


# ─────────────────────────────────────────────────────────────────────────────
# domain/agents/router.py — choose_v8_strategy
# ─────────────────────────────────────────────────────────────────────────────

class ChooseV8StrategyTest(unittest.TestCase):

    def _call(self, task="hello", mode="chat", **kw) -> dict:
        return choose_v8_strategy(task, {"mode": mode}, "llama3", "default", **kw)

    # ── return type & structure ───────────────────────────────────────────────

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_required_keys_present(self) -> None:
        result = self._call()
        for key in ("strategy", "confidence", "source", "reason", "scores"):
            self.assertIn(key, result)

    def test_strategy_is_string(self) -> None:
        self.assertIsInstance(self._call()["strategy"], str)

    def test_confidence_is_float(self) -> None:
        self.assertIsInstance(self._call()["confidence"], float)

    def test_confidence_between_0_and_1(self) -> None:
        conf = self._call()["confidence"]
        self.assertGreaterEqual(conf, 0.0)
        self.assertLessEqual(conf, 1.0)

    def test_scores_is_dict(self) -> None:
        self.assertIsInstance(self._call()["scores"], dict)

    # ── force_strategy path (fully pure) ─────────────────────────────────────

    def test_force_strategy_returns_forced(self) -> None:
        result = self._call(force_strategy="planner")
        self.assertEqual(result["strategy"], "planner")

    def test_force_strategy_confidence_is_1(self) -> None:
        result = self._call(force_strategy="direct")
        self.assertEqual(result["confidence"], 1.0)

    def test_force_strategy_source_is_forced(self) -> None:
        result = self._call(force_strategy="multi_agent")
        self.assertEqual(result["source"], "forced")

    def test_force_strategy_learned_preferences_empty(self) -> None:
        result = self._call(force_strategy="direct")
        self.assertEqual(result["learned_preferences"], [])

    # ── mode-based routing ────────────────────────────────────────────────────

    def test_chat_mode_prefers_direct(self) -> None:
        result = self._call(task="hello", mode="chat")
        self.assertEqual(result["strategy"], "direct")

    def test_research_mode_prefers_task_graph(self) -> None:
        result = self._call(task="search for info", mode="research")
        self.assertIn(result["strategy"], ("task_graph", "planner", "multi_agent"))

    def test_multi_step_mode_prefers_planner(self) -> None:
        result = self._call(task="create a roadmap", mode="multi_step")
        self.assertIn(result["strategy"], ("planner", "task_graph", "multi_agent"))

    def test_known_strategy_in_scores(self) -> None:
        result = self._call()
        # All known strategies should be in scores
        for s in ("direct", "planner", "task_graph", "multi_agent", "self_improve"):
            self.assertIn(s, result["scores"])

    def test_empty_task_returns_dict(self) -> None:
        result = self._call(task="")
        self.assertIsInstance(result, dict)
        self.assertIn("strategy", result)


# ─────────────────────────────────────────────────────────────────────────────
# domain/agents/planner_graphs.py — planner_default_steps
# ─────────────────────────────────────────────────────────────────────────────

class PlannerDefaultStepsTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(planner_default_steps("task"), list)

    def test_nonempty(self) -> None:
        self.assertGreater(len(planner_default_steps("task")), 0)

    def test_max_4_steps(self) -> None:
        self.assertLessEqual(len(planner_default_steps("long task " * 50)), 4)

    def test_last_step_is_reasoning(self) -> None:
        result = planner_default_steps("simple task")
        self.assertEqual(result[-1]["tool"], "reasoning")

    def test_each_step_has_tool_key(self) -> None:
        for step in planner_default_steps("task"):
            self.assertIn("tool", step)

    def test_each_step_has_goal_key(self) -> None:
        for step in planner_default_steps("task"):
            self.assertIn("goal", step)

    def test_url_in_task_adds_browser_step(self) -> None:
        result = planner_default_steps("check https://example.com for data")
        tools = [s["tool"] for s in result]
        self.assertIn("browser", tools)

    def test_url_step_comes_before_reasoning(self) -> None:
        result = planner_default_steps("visit https://example.com")
        # browser should be first
        self.assertEqual(result[0]["tool"], "browser")

    def test_url_step_has_url_field(self) -> None:
        result = planner_default_steps("open https://example.com")
        browser_steps = [s for s in result if s["tool"] == "browser"]
        self.assertTrue(all("url" in s for s in browser_steps))

    def test_no_url_plain_task(self) -> None:
        result = planner_default_steps("explain python generators")
        # no browser step for plain task without search keywords
        tools = [s["tool"] for s in result]
        # reasoning is always present
        self.assertIn("reasoning", tools)


# ─────────────────────────────────────────────────────────────────────────────
# domain/agents/planner_graphs.py — task_graph_default
# ─────────────────────────────────────────────────────────────────────────────

class TaskGraphDefaultTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(task_graph_default("task"), list)

    def test_nonempty(self) -> None:
        self.assertGreater(len(task_graph_default("task")), 0)

    def test_max_6_nodes(self) -> None:
        self.assertLessEqual(len(task_graph_default("task " * 60)), 6)

    def test_last_node_is_reasoning(self) -> None:
        result = task_graph_default("simple task")
        self.assertEqual(result[-1]["tool"], "reasoning")

    def test_each_node_has_id_key(self) -> None:
        for node in task_graph_default("task"):
            self.assertIn("id", node)

    def test_each_node_has_tool_key(self) -> None:
        for node in task_graph_default("task"):
            self.assertIn("tool", node)

    def test_each_node_has_depends_on(self) -> None:
        for node in task_graph_default("task"):
            self.assertIn("depends_on", node)

    def test_url_in_task_adds_browser_node(self) -> None:
        result = task_graph_default("fetch https://example.com and analyze")
        tools = [n["tool"] for n in result]
        self.assertIn("browser", tools)

    def test_ids_are_unique(self) -> None:
        result = task_graph_default("complex task")
        ids = [n["id"] for n in result]
        self.assertEqual(len(ids), len(set(ids)))


# ─────────────────────────────────────────────────────────────────────────────
# application/rag_memory_service/runtime.py — _cosine_sim
# ─────────────────────────────────────────────────────────────────────────────

class CosineSimilarityTest(unittest.TestCase):

    def test_returns_float(self) -> None:
        self.assertIsInstance(_cosine_sim([1.0, 0.0], [1.0, 0.0]), float)

    def test_identical_vectors_is_1(self) -> None:
        self.assertAlmostEqual(_cosine_sim([1.0, 0.0], [1.0, 0.0]), 1.0, places=5)

    def test_orthogonal_vectors_is_0(self) -> None:
        self.assertAlmostEqual(_cosine_sim([1.0, 0.0], [0.0, 1.0]), 0.0, places=5)

    def test_opposite_vectors_is_neg1(self) -> None:
        self.assertAlmostEqual(_cosine_sim([1.0, 0.0], [-1.0, 0.0]), -1.0, places=5)

    def test_empty_vectors_is_0(self) -> None:
        self.assertEqual(_cosine_sim([], []), 0.0)

    def test_different_lengths_is_0(self) -> None:
        self.assertEqual(_cosine_sim([1.0, 2.0], [1.0]), 0.0)

    def test_zero_vector_is_0(self) -> None:
        self.assertEqual(_cosine_sim([0.0, 0.0], [1.0, 0.0]), 0.0)

    def test_partial_similarity_between_0_and_1(self) -> None:
        result = _cosine_sim([1.0, 1.0], [1.0, 0.0])
        self.assertGreater(result, 0.0)
        self.assertLess(result, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# application/persona/store.py — row_to_version
# ─────────────────────────────────────────────────────────────────────────────

class RowToVersionTest(unittest.TestCase):

    def _row(self, **kw) -> dict:
        defaults = {
            "version": 1,
            "status": "active",
            "payload_json": "{}",
            "source": "{}",
        }
        defaults.update(kw)
        return defaults

    def test_none_returns_none(self) -> None:
        self.assertIsNone(row_to_version(None))

    def test_returns_dict(self) -> None:
        self.assertIsInstance(row_to_version(self._row()), dict)

    def test_payload_json_parsed(self) -> None:
        result = row_to_version(self._row(payload_json='{"name": "Elira"}'))
        self.assertEqual(result["payload"], {"name": "Elira"})

    def test_source_parsed(self) -> None:
        result = row_to_version(self._row(source='{"type": "builtin"}'))
        self.assertEqual(result["source"], {"type": "builtin"})

    def test_other_fields_preserved(self) -> None:
        result = row_to_version(self._row(version=7, status="active"))
        self.assertEqual(result["version"], 7)
        self.assertEqual(result["status"], "active")

    def test_payload_json_key_removed(self) -> None:
        result = row_to_version(self._row())
        self.assertNotIn("payload_json", result)

    def test_empty_payload_json_becomes_empty_dict(self) -> None:
        result = row_to_version(self._row(payload_json="{}"))
        self.assertEqual(result["payload"], {})

    def test_invalid_payload_json_becomes_fallback(self) -> None:
        result = row_to_version(self._row(payload_json="{bad}"))
        # Falls back to {} default
        self.assertEqual(result["payload"], {})


# ─────────────────────────────────────────────────────────────────────────────
# application/skills_extra/runtime.py — encrypt_text / decrypt_text
# ─────────────────────────────────────────────────────────────────────────────

class EncryptDecryptTest(unittest.TestCase):

    def test_encrypt_returns_dict(self) -> None:
        self.assertIsInstance(encrypt_text("hello"), dict)

    def test_encrypt_ok_true_for_valid_input(self) -> None:
        result = encrypt_text("hello world")
        self.assertTrue(result["ok"])

    def test_encrypt_has_encrypted_key(self) -> None:
        result = encrypt_text("test")
        self.assertIn("encrypted", result)

    def test_encrypt_original_length_preserved(self) -> None:
        text = "hello"
        result = encrypt_text(text)
        self.assertEqual(result.get("original_length"), len(text))

    def test_encrypt_produces_nonempty_token(self) -> None:
        result = encrypt_text("test message")
        self.assertGreater(len(result.get("encrypted", "")), 0)

    def test_decrypt_returns_dict(self) -> None:
        token = encrypt_text("x")["encrypted"]
        self.assertIsInstance(decrypt_text(token), dict)

    def test_decrypt_ok_for_valid_token(self) -> None:
        token = encrypt_text("hello")["encrypted"]
        result = decrypt_text(token)
        self.assertTrue(result["ok"])

    def test_decrypt_roundtrip(self) -> None:
        original = "secret message 42"
        encrypted = encrypt_text(original)["encrypted"]
        decrypted = decrypt_text(encrypted)["decrypted"]
        self.assertEqual(decrypted, original)

    def test_decrypt_invalid_token_ok_false(self) -> None:
        result = decrypt_text("not-a-valid-fernet-token")
        self.assertFalse(result["ok"])

    def test_decrypt_invalid_token_has_error(self) -> None:
        result = decrypt_text("bad-token")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
