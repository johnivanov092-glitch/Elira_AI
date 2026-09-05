"""Tests for memory, persona version and encryption helpers."""
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
from app.application.skills_extra.runtime import (  # noqa: E402
    encrypt_text,
    decrypt_text,
)


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
