"""Tests for application/rag_memory, application/elira_phase21,
application/elira_phase20_queue, and application/elira_phase20_state runtimes."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.rag_memory import runtime as rag_rt             # noqa: E402
import app.application.elira_phase21.runtime as p21_rt               # noqa: E402
from app.application.elira_phase20_queue.runtime import build_preview_queue  # noqa: E402
import app.application.elira_phase20_state.runtime as p20s_rt        # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for rag_memory tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_rag_connect(db_path: Path):
    """Return a conn_factory that opens db_path with sqlite3.Row factory."""
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


def _no_embedding(text: str):
    """Simulate no embedding available."""
    return None


def _fake_embedding(text: str):
    """Return a fixed 3-d unit vector so cosine_sim is deterministic."""
    return [1.0, 0.0, 0.0]


def _fake_different_embedding(text: str):
    """Return a different 3-d vector."""
    return [0.0, 1.0, 0.0]


# ─────────────────────────────────────────────────────────────────────────────
# rag_memory — cosine_sim (pure)
# ─────────────────────────────────────────────────────────────────────────────

class CosineSimilarityTest(unittest.TestCase):
    def test_identical_vectors_score_one(self) -> None:
        v = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(rag_rt.cosine_sim(v, v), 1.0, places=5)

    def test_orthogonal_vectors_score_zero(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(rag_rt.cosine_sim(a, b), 0.0, places=5)

    def test_empty_vectors_return_zero(self) -> None:
        self.assertEqual(rag_rt.cosine_sim([], []), 0.0)

    def test_different_lengths_return_zero(self) -> None:
        self.assertEqual(rag_rt.cosine_sim([1.0], [1.0, 2.0]), 0.0)

    def test_zero_norm_returns_zero(self) -> None:
        self.assertEqual(rag_rt.cosine_sim([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_opposite_vectors_score_minus_one(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        self.assertAlmostEqual(rag_rt.cosine_sim(a, b), -1.0, places=5)


# ─────────────────────────────────────────────────────────────────────────────
# rag_memory — init_db + CRUD (temp file DB)
# ─────────────────────────────────────────────────────────────────────────────

class RagMemoryRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "rag.db"
        self._conn_factory = _make_rag_connect(self._db_path)
        rag_rt.init_db(conn_factory=self._conn_factory)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # ── add_to_rag ────────────────────────────────────────────────────────────

    def test_add_to_rag_success(self) -> None:
        r = rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            text="Python is a programming language",
        )
        self.assertTrue(r["ok"])
        self.assertIn("id", r)

    def test_add_to_rag_short_text_rejected(self) -> None:
        r = rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            text="ab",
        )
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_add_to_rag_empty_text_rejected(self) -> None:
        r = rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            text="",
        )
        self.assertFalse(r["ok"])

    def test_add_to_rag_with_embedding(self) -> None:
        r = rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_fake_embedding,
            text="Python has dynamic typing",
        )
        self.assertTrue(r["ok"])
        self.assertTrue(r["has_embedding"])

    def test_add_to_rag_without_embedding(self) -> None:
        r = rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            text="Python has dynamic typing",
        )
        self.assertTrue(r["ok"])
        self.assertFalse(r["has_embedding"])

    # ── list_rag ──────────────────────────────────────────────────────────────

    def test_list_rag_empty(self) -> None:
        r = rag_rt.list_rag(conn_factory=self._conn_factory)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)

    def test_list_rag_after_add(self) -> None:
        rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            text="Some interesting fact about programming",
        )
        r = rag_rt.list_rag(conn_factory=self._conn_factory)
        self.assertEqual(r["count"], 1)

    # ── delete_rag ────────────────────────────────────────────────────────────

    def test_delete_rag_item(self) -> None:
        added = rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            text="To be deleted fact here",
        )
        rag_rt.delete_rag(conn_factory=self._conn_factory, item_id=added["id"])
        r = rag_rt.list_rag(conn_factory=self._conn_factory)
        self.assertEqual(r["count"], 0)

    # ── rag_stats ─────────────────────────────────────────────────────────────

    def test_rag_stats_empty(self) -> None:
        r = rag_rt.rag_stats(conn_factory=self._conn_factory, embed_model="gemma")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["with_embeddings"], 0)
        self.assertEqual(r["model"], "gemma")

    def test_rag_stats_with_embedding(self) -> None:
        rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_fake_embedding,
            text="Python uses indentation for blocks",
        )
        r = rag_rt.rag_stats(conn_factory=self._conn_factory, embed_model="test")
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["with_embeddings"], 1)

    # ── search_rag ────────────────────────────────────────────────────────────

    def test_search_rag_empty_query_returns_empty(self) -> None:
        r = rag_rt.search_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            cosine_sim_func=rag_rt.cosine_sim,
            query="",
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)

    def test_search_rag_keyword_match(self) -> None:
        rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            text="Python uses indentation for code blocks",
        )
        r = rag_rt.search_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_no_embedding,
            cosine_sim_func=rag_rt.cosine_sim,
            query="python indentation",
            min_score=0.1,
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["method"], "keyword")
        self.assertGreater(r["count"], 0)

    def test_search_rag_embedding_match(self) -> None:
        rag_rt.add_to_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_fake_embedding,
            text="Python is great for data science",
        )
        # Search with same embedding → cosine_sim = 1.0 → score boosted
        r = rag_rt.search_rag(
            conn_factory=self._conn_factory,
            get_embedding_func=_fake_embedding,
            cosine_sim_func=rag_rt.cosine_sim,
            query="data science tools",
            min_score=0.1,
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["method"], "embedding")

    # ── get_rag_context ───────────────────────────────────────────────────────

    def test_get_rag_context_empty_returns_empty_string(self) -> None:
        def mock_search(query, limit):
            return {"items": [], "count": 0}
        ctx = rag_rt.get_rag_context(
            search_rag_func=mock_search, query="anything"
        )
        self.assertEqual(ctx, "")

    def test_get_rag_context_formats_items(self) -> None:
        def mock_search(query, limit):
            return {
                "items": [
                    {"text": "Python is interpreted", "score": 0.9},
                    {"text": "Python is dynamically typed", "score": 0.8},
                ],
                "count": 2,
            }
        ctx = rag_rt.get_rag_context(
            search_rag_func=mock_search, query="python"
        )
        self.assertIn("Context notes", ctx)
        self.assertIn("Python is interpreted", ctx)

    def test_get_rag_context_respects_max_chars(self) -> None:
        def mock_search(query, limit):
            return {
                "items": [{"text": "x" * 1000, "score": 0.9}],
                "count": 1,
            }
        ctx = rag_rt.get_rag_context(
            search_rag_func=mock_search, query="q", max_chars=100
        )
        # The item is larger than max_chars, so no lines are added → empty string
        self.assertEqual(ctx, "")


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase21 — build_controller (pure)
# ─────────────────────────────────────────────────────────────────────────────

class Phase21BuildControllerTest(unittest.TestCase):
    def test_build_controller_with_queue(self) -> None:
        queue = [{"path": "a.py", "order": 1}]
        state = {"mode": "ready"}
        ctrl = p21_rt.build_controller(queue, state)
        self.assertEqual(ctrl["mode"], "autonomous-controller")
        self.assertIn("steps", ctrl)
        self.assertIn("summary", ctrl)

    def test_build_controller_empty_queue(self) -> None:
        ctrl = p21_rt.build_controller([], {})
        self.assertFalse(ctrl["summary"]["apply_allowed"])
        self.assertFalse(ctrl["summary"]["verify_allowed"])

    def test_build_controller_steps_include_all_stages(self) -> None:
        ctrl = p21_rt.build_controller([{"path": "x.py"}], {"x": 1})
        step_names = {s["step"] for s in ctrl["steps"]}
        for step in ("load-queue", "consume-preview-queue", "batch-apply-controller"):
            self.assertIn(step, step_names)

    def test_build_controller_load_queue_is_always_done(self) -> None:
        ctrl = p21_rt.build_controller([], {})
        load = next(s for s in ctrl["steps"] if s["step"] == "load-queue")
        self.assertEqual(load["status"], "done")

    def test_build_controller_summary_queue_count(self) -> None:
        ctrl = p21_rt.build_controller(["a", "b", "c"], {})
        self.assertEqual(ctrl["summary"]["queue_count"], 3)


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase21 — DB persistence (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class Phase21PersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = p21_rt.DB_PATH
        p21_rt.DB_PATH = Path(self._tmpdir.name) / "p21.db"
        p21_rt.ensure_db()

    def tearDown(self) -> None:
        p21_rt.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_persist_and_list(self) -> None:
        queue = [{"path": "a.py", "order": 1}]
        state = {"checkpoint": True}
        ctrl = {"mode": "autonomous-controller", "steps": [], "summary": {}, "notes": []}
        run_id = p21_rt.persist("fix auth", queue, state, ctrl)
        self.assertIsNotNone(run_id)
        result = p21_rt.list_runs()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["goal"], "fix auth")

    def test_get_run_parsed(self) -> None:
        queue = [{"path": "x.py"}]
        state = {"x": 1}
        ctrl = {"mode": "test", "steps": [], "summary": {}, "notes": []}
        run_id = p21_rt.persist("goal", queue, state, ctrl)
        data = p21_rt.get_run(run_id)
        self.assertIsInstance(data["queue_items"], list)
        self.assertIsInstance(data["execution_state"], dict)
        self.assertIsInstance(data["controller"], dict)

    def test_get_run_not_found(self) -> None:
        self.assertEqual(p21_rt.get_run(9999)["status"], "not_found")

    def test_multiple_runs_newest_first(self) -> None:
        ctrl = {"mode": "x", "steps": [], "summary": {}, "notes": []}
        p21_rt.persist("first", [], {}, ctrl)
        p21_rt.persist("second", [], {}, ctrl)
        items = p21_rt.list_runs()["items"]
        self.assertEqual(items[0]["goal"], "second")

    def test_list_runs_limit(self) -> None:
        ctrl = {"mode": "x", "steps": [], "summary": {}, "notes": []}
        for _ in range(4):
            p21_rt.persist("run", [], {}, ctrl)
        self.assertLessEqual(len(p21_rt.list_runs(limit=2)["items"]), 2)


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase20_queue — build_preview_queue (pure, no DB)
# ─────────────────────────────────────────────────────────────────────────────

class BuildPreviewQueueTest(unittest.TestCase):
    def test_required_keys(self) -> None:
        r = build_preview_queue("refactor", ["a.py", "b.py"])
        for key in ("status", "goal", "count", "items", "created_at"):
            self.assertIn(key, r)

    def test_count_matches_targets(self) -> None:
        r = build_preview_queue("fix", ["x.py", "y.py", "z.py"])
        self.assertEqual(r["count"], 3)

    def test_items_have_order_and_status(self) -> None:
        r = build_preview_queue("refactor", ["a.py"])
        item = r["items"][0]
        self.assertEqual(item["order"], 1)
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["mode"], "preview")

    def test_empty_targets(self) -> None:
        r = build_preview_queue("fix", [])
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["items"], [])

    def test_goal_preserved(self) -> None:
        r = build_preview_queue("my specific goal", ["a.py"])
        self.assertEqual(r["goal"], "my specific goal")

    def test_order_is_sequential(self) -> None:
        r = build_preview_queue("fix", ["a.py", "b.py", "c.py"])
        orders = [item["order"] for item in r["items"]]
        self.assertEqual(orders, [1, 2, 3])


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase20_state — builders (pure) + persistence (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class Phase20StateBuildersTest(unittest.TestCase):
    def test_build_checkpoints_returns_list(self) -> None:
        checkpoints = p20s_rt.build_checkpoints()
        self.assertIsInstance(checkpoints, list)
        self.assertGreater(len(checkpoints), 0)

    def test_build_checkpoints_has_queue_built_done(self) -> None:
        checkpoints = p20s_rt.build_checkpoints()
        done = next(c for c in checkpoints if c["step"] == "queue-built")
        self.assertEqual(done["status"], "done")

    def test_build_rollback_has_strategy(self) -> None:
        rollback = p20s_rt.build_rollback(["a.py", "b.py"])
        self.assertIn("strategy", rollback)
        self.assertIn("targets", rollback)
        self.assertEqual(rollback["targets"], ["a.py", "b.py"])

    def test_build_rollback_empty_staged(self) -> None:
        rollback = p20s_rt.build_rollback([])
        self.assertEqual(rollback["targets"], [])


class Phase20StatePersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = p20s_rt.DB_PATH
        p20s_rt.DB_PATH = Path(self._tmpdir.name) / "p20state.db"
        p20s_rt.ensure_db()

    def tearDown(self) -> None:
        p20s_rt.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_persist_and_list(self) -> None:
        checkpoints = p20s_rt.build_checkpoints()
        rollback = p20s_rt.build_rollback(["a.py"])
        state_id = p20s_rt.persist_state("deploy fix", checkpoints, [{"path": "a.py"}], rollback)
        self.assertIsNotNone(state_id)
        result = p20s_rt.list_states()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["goal"], "deploy fix")

    def test_multiple_states_newest_first(self) -> None:
        for goal in ("first", "second"):
            p20s_rt.persist_state(goal, [], [], {})
        items = p20s_rt.list_states()["items"]
        self.assertEqual(items[0]["goal"], "second")

    def test_list_states_limit(self) -> None:
        for _ in range(4):
            p20s_rt.persist_state("run", [], [], {})
        self.assertLessEqual(len(p20s_rt.list_states(limit=2)["items"]), 2)

    def test_prepare_execution_state(self) -> None:
        result = p20s_rt.prepare_execution_state(
            "refactor login", [{"path": "auth.py"}], ["auth.py"]
        )
        for key in ("status", "goal", "checkpoints", "queue", "rollback", "state_id"):
            self.assertIn(key, result)
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
