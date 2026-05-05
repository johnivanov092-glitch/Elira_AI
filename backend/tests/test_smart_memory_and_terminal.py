"""Tests for application/smart_memory (extraction + store pure functions)
and application/terminal (runtime helpers)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.smart_memory.store as sm_store        # noqa: E402
from app.application.smart_memory.extraction import (        # noqa: E402
    is_memory_command,
    classify_memory_text,
)
import app.application.terminal.runtime as term_rt           # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# smart_memory/extraction.py — pure functions
# ─────────────────────────────────────────────────────────────────────────────

class IsMemoryCommandTest(unittest.TestCase):
    def test_remember_english(self) -> None:
        self.assertTrue(is_memory_command("remember that I love Python"))

    def test_save_english(self) -> None:
        self.assertTrue(is_memory_command("save this for later"))

    def test_zapomni_russian(self) -> None:
        self.assertTrue(is_memory_command("запомни, что мой сервер 1.2.3.4"))

    def test_sohrani_russian(self) -> None:
        self.assertTrue(is_memory_command("сохрани мой пароль"))

    def test_non_command_returns_false(self) -> None:
        self.assertFalse(is_memory_command("what is Python"))

    def test_empty_returns_false(self) -> None:
        self.assertFalse(is_memory_command(""))

    def test_none_returns_false(self) -> None:
        self.assertFalse(is_memory_command(None))  # type: ignore[arg-type]

    def test_word_boundary_respected(self) -> None:
        # "remembered" doesn't start with "remember\b"
        self.assertFalse(is_memory_command("remembered the answer"))


class ClassifyMemoryTextTest(unittest.TestCase):
    def test_instruction_always(self) -> None:
        result = classify_memory_text("always respond in English")
        self.assertEqual(result, "instruction")

    def test_instruction_never(self) -> None:
        result = classify_memory_text("never use markdown")
        self.assertEqual(result, "instruction")

    def test_instruction_russian_always(self) -> None:
        result = classify_memory_text("всегда отвечай на русском")
        self.assertEqual(result, "instruction")

    def test_instruction_respond_keyword(self) -> None:
        result = classify_memory_text("отвечай кратко и по существу")
        self.assertEqual(result, "instruction")

    def test_preference_love(self) -> None:
        result = classify_memory_text("я люблю программирование")
        self.assertEqual(result, "preference")

    def test_preference_want(self) -> None:
        # "хочу" triggers preference classification
        result = classify_memory_text("я хочу изучить Python")
        self.assertEqual(result, "preference")

    def test_fact_default(self) -> None:
        result = classify_memory_text("the capital of France is Paris")
        self.assertEqual(result, "fact")

    def test_empty_returns_fact(self) -> None:
        result = classify_memory_text("")
        self.assertEqual(result, "fact")

    def test_none_returns_fact(self) -> None:
        result = classify_memory_text(None)  # type: ignore[arg-type]
        self.assertEqual(result, "fact")

    def test_return_type_is_string(self) -> None:
        result = classify_memory_text("some random text here")
        self.assertIsInstance(result, str)

    def test_result_is_known_category(self) -> None:
        valid = {"fact", "instruction", "preference"}
        for text in ("plain text", "always do this", "я люблю"):
            self.assertIn(classify_memory_text(text), valid)


# ─────────────────────────────────────────────────────────────────────────────
# smart_memory/store.py — with patched DB_PATH
# ─────────────────────────────────────────────────────────────────────────────

class SmartMemoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = sm_store.DB_PATH
        sm_store.DB_PATH = Path(self._tmpdir.name) / "smart_memory.db"
        sm_store.init_memory_db()

    def tearDown(self) -> None:
        sm_store.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    # ── normalize_profile ─────────────────────────────────────────────────────

    def test_normalize_profile_none(self) -> None:
        self.assertEqual(sm_store.normalize_profile(None), "default")

    def test_normalize_profile_empty(self) -> None:
        self.assertEqual(sm_store.normalize_profile(""), "default")

    def test_normalize_profile_valid(self) -> None:
        self.assertEqual(sm_store.normalize_profile("alice"), "alice")

    def test_normalize_profile_strips_whitespace(self) -> None:
        self.assertEqual(sm_store.normalize_profile("  bob  "), "bob")

    # ── add_memory ────────────────────────────────────────────────────────────

    def test_add_memory_ok(self) -> None:
        result = sm_store.add_memory("Python is a great language")
        self.assertTrue(result["ok"])
        self.assertIn("id", result)

    def test_add_memory_short_rejected(self) -> None:
        result = sm_store.add_memory("hi")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_add_memory_action_created(self) -> None:
        result = sm_store.add_memory("Machine learning is fascinating")
        self.assertEqual(result["action"], "created")

    def test_add_memory_stores_text(self) -> None:
        sm_store.add_memory("Django is a web framework xq1")
        items = sm_store.list_memories()["items"]
        texts = [i["text"] for i in items]
        self.assertIn("Django is a web framework xq1", texts)

    def test_add_memory_duplicate_updates(self) -> None:
        sm_store.add_memory("Python is a great programming language")
        result = sm_store.add_memory("Python is a great programming language")
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "updated")

    def test_add_memory_category_stored(self) -> None:
        sm_store.add_memory("always use dark mode uq2", category="instruction")
        items = sm_store.list_memories(category="instruction")["items"]
        self.assertGreater(items[0]["importance"] if items else 0, 0)

    # ── list_memories ─────────────────────────────────────────────────────────

    def test_list_memories_empty_initially(self) -> None:
        result = sm_store.list_memories()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_list_memories_after_add(self) -> None:
        sm_store.add_memory("Flask is lightweight xq3")
        result = sm_store.list_memories()
        self.assertGreater(result["count"], 0)

    def test_list_memories_category_filter(self) -> None:
        sm_store.add_memory("always use dark mode xq4", category="instruction")
        sm_store.add_memory("simple fact here xq5")
        result = sm_store.list_memories(category="instruction")
        for item in result["items"]:
            self.assertEqual(item["category"], "instruction")

    def test_list_memories_profile_filter(self) -> None:
        sm_store.add_memory("Alice note xq6", profile_name="alice")
        sm_store.add_memory("Bob note xq7", profile_name="bob")
        alice = sm_store.list_memories(profile_name="alice")
        self.assertEqual(alice["count"], 1)
        self.assertEqual(alice["items"][0]["profile_name"], "alice")

    def test_list_memories_respects_limit(self) -> None:
        for i in range(5):
            sm_store.add_memory(f"memory number {i} for limit test xq{i+10}")
        result = sm_store.list_memories(limit=3)
        self.assertLessEqual(result["count"], 3)

    def test_list_memories_has_ok_key(self) -> None:
        result = sm_store.list_memories()
        self.assertIn("ok", result)

    # ── delete_memory ─────────────────────────────────────────────────────────

    def test_delete_memory_ok(self) -> None:
        result = sm_store.add_memory("delete me soon please xq20")
        mem_id = result["id"]
        del_result = sm_store.delete_memory(mem_id)
        self.assertTrue(del_result["ok"])
        self.assertEqual(del_result["deleted"], 1)

    def test_delete_memory_not_found(self) -> None:
        del_result = sm_store.delete_memory(99999)
        self.assertFalse(del_result["ok"])
        self.assertEqual(del_result["deleted"], 0)

    def test_delete_memory_removes_from_list(self) -> None:
        result = sm_store.add_memory("goodbye memory xq21")
        sm_store.delete_memory(result["id"])
        self.assertEqual(sm_store.list_memories()["count"], 0)

    # ── clear_all_memories ────────────────────────────────────────────────────

    def test_clear_all_memories(self) -> None:
        sm_store.add_memory("some memory to clear xq30")
        result = sm_store.clear_all_memories()
        self.assertTrue(result["ok"])
        self.assertGreater(result["deleted"], 0)
        self.assertEqual(sm_store.list_memories()["count"], 0)

    def test_clear_by_profile_only(self) -> None:
        sm_store.add_memory("alice data xq31", profile_name="alice")
        sm_store.add_memory("bob data xq32", profile_name="bob")
        sm_store.clear_all_memories(profile_name="alice")
        bob = sm_store.list_memories(profile_name="bob")
        self.assertEqual(bob["count"], 1)

    def test_clear_empty_db_ok(self) -> None:
        result = sm_store.clear_all_memories()
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], 0)

    # ── get_stats ─────────────────────────────────────────────────────────────

    def test_get_stats_empty(self) -> None:
        result = sm_store.get_stats()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 0)

    def test_get_stats_after_add(self) -> None:
        sm_store.add_memory("test stats memory item xq40", category="fact")
        result = sm_store.get_stats()
        self.assertGreater(result["total"], 0)
        self.assertIn("fact", result["by_category"])

    def test_get_stats_by_profile(self) -> None:
        sm_store.add_memory("profiled memory item xq41", profile_name="zed")
        result = sm_store.get_stats(profile_name="zed")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["profile_name"], "zed")

    def test_get_stats_has_by_source(self) -> None:
        sm_store.add_memory("source test memory xq42", source="user_command")
        result = sm_store.get_stats()
        self.assertIn("by_source", result)

    # ── list_profiles ─────────────────────────────────────────────────────────

    def test_list_profiles_empty(self) -> None:
        result = sm_store.list_profiles()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_list_profiles_after_add(self) -> None:
        sm_store.add_memory("data for profile listing xq50", profile_name="demo")
        result = sm_store.list_profiles()
        names = [p["name"] for p in result["profiles"]]
        self.assertIn("demo", names)

    def test_list_profiles_has_count(self) -> None:
        result = sm_store.list_profiles()
        self.assertIn("count", result)
        self.assertIn("profiles", result)


# ─────────────────────────────────────────────────────────────────────────────
# terminal/runtime.py — pure helpers and exec_command
# ─────────────────────────────────────────────────────────────────────────────

class DecodeWinTest(unittest.TestCase):
    def test_empty_bytes_returns_empty(self) -> None:
        self.assertEqual(term_rt.decode_win(b""), "")

    def test_utf8_bytes_decoded(self) -> None:
        self.assertEqual(term_rt.decode_win(b"hello"), "hello")

    def test_utf8_with_spaces(self) -> None:
        self.assertEqual(term_rt.decode_win(b"hello world"), "hello world")

    def test_returns_string(self) -> None:
        self.assertIsInstance(term_rt.decode_win(b"test"), str)


class GetCwdTest(unittest.TestCase):
    def test_get_cwd_returns_string(self) -> None:
        self.assertIsInstance(term_rt.get_cwd(), str)

    def test_get_cwd_is_not_empty(self) -> None:
        self.assertGreater(len(term_rt.get_cwd()), 0)

    def test_get_cwd_matches_module_state(self) -> None:
        self.assertEqual(term_rt.get_cwd(), term_rt._cwd)


class ChangeDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_cwd = term_rt._cwd

    def tearDown(self) -> None:
        term_rt._cwd = self._orig_cwd

    def test_empty_target_returns_current(self) -> None:
        result = term_rt.change_dir("")
        self.assertTrue(result["ok"])
        self.assertIn("cwd", result)

    def test_nonexistent_dir_fails(self) -> None:
        result = term_rt.change_dir("/this/does/not/exist/xyzzy_nope")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_valid_dir_changes_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = term_rt.change_dir(tmpdir)
            self.assertTrue(result["ok"])
            self.assertEqual(
                Path(result["cwd"]).resolve(),
                Path(tmpdir).resolve(),
            )

    def test_change_dir_ok_has_cwd_key(self) -> None:
        result = term_rt.change_dir("")
        self.assertIn("cwd", result)


class ExecCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_cwd = term_rt._cwd

    def tearDown(self) -> None:
        term_rt._cwd = self._orig_cwd

    def test_empty_command_returns_error(self) -> None:
        result = term_rt.exec_command("")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Empty command")

    def test_whitespace_only_returns_error(self) -> None:
        result = term_rt.exec_command("   ")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Empty command")

    def test_blocked_rm_rf(self) -> None:
        result = term_rt.exec_command("rm -rf /")
        self.assertFalse(result["ok"])
        self.assertIn("blocked", result["error"].lower())

    def test_blocked_mkfs(self) -> None:
        result = term_rt.exec_command("mkfs.ext4 /dev/sda")
        self.assertFalse(result["ok"])

    def test_blocked_format_c(self) -> None:
        result = term_rt.exec_command("format c:")
        self.assertFalse(result["ok"])

    def test_blocked_shutdown(self) -> None:
        result = term_rt.exec_command("shutdown -h now")
        self.assertFalse(result["ok"])

    def test_cd_delegates_to_change_dir_nonexistent(self) -> None:
        result = term_rt.exec_command("cd /nonexistent/path/xyz_nope")
        self.assertFalse(result["ok"])

    def test_exec_ok_has_required_keys(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        if term_rt._IS_WINDOWS:
            mock_result.stdout = b"hello"
            mock_result.stderr = b""
        else:
            mock_result.stdout = "hello"
            mock_result.stderr = ""

        with patch("app.application.terminal.runtime.subprocess.run",
                   return_value=mock_result):
            result = term_rt.exec_command("echo hello")

        self.assertTrue(result["ok"])
        for key in ("stdout", "stderr", "returncode", "cwd"):
            self.assertIn(key, result)

    def test_exec_timeout_returns_error(self) -> None:
        import subprocess
        with patch("app.application.terminal.runtime.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("echo", 15)):
            result = term_rt.exec_command("sleep 999")
        self.assertFalse(result["ok"])
        self.assertIn("Timeout", result["error"])

    def test_exec_exception_returns_error(self) -> None:
        with patch("app.application.terminal.runtime.subprocess.run",
                   side_effect=OSError("no such file")):
            result = term_rt.exec_command("nonexistent_command_xyz")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_blocked_list_has_expected_entries(self) -> None:
        # Verify that the BLOCKED list is non-empty and has known entries
        self.assertIn("rm -rf /", term_rt.BLOCKED)
        self.assertIn("shutdown", term_rt.BLOCKED)


if __name__ == "__main__":
    unittest.main()
