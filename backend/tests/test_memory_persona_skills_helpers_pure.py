"""Tests for memory and persona version helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.rag_memory.service import _cosine_sim  # noqa: E402
from app.application.persona.store import row_to_version  # noqa: E402


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



if __name__ == "__main__":
    unittest.main()
