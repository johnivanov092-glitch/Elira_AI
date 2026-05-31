"""Tests for pure helpers across five modules.

  application/project_brain/chat.py     - clean_html_text, build_code_prompt
  application/project_brain/files.py    - normalize_relative_path,
                                           looks_text_file, hash_bytes
  application/project_brain/uploads.py  - safe_filename, attachment_summary
  application/workflows/execution.py    - parse_workflow_datetime,
                                           workflow_step_index
  application/chat/identity_guard.py - _contains_model_identity,
                                           _still_drifting,
                                           _rewrite_identity_drift

All functions are pure (no DB, no HTTP, no FS side-effects).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.project_brain.chat import (  # noqa: E402
    clean_html_text,
    build_code_prompt,
)
from app.application.project_brain.files import (  # noqa: E402
    normalize_relative_path,
    looks_text_file,
    hash_bytes,
)
from app.application.project_brain.uploads import (  # noqa: E402
    safe_filename,
    attachment_summary,
)
from app.application.workflows.execution import (  # noqa: E402
    parse_workflow_datetime,
    workflow_step_index,
)
from app.application.chat.identity_guard import (  # noqa: E402
    _contains_model_identity,
    _still_drifting,
    _rewrite_identity_drift,
)


# project_brain/chat.py - clean_html_text

class CleanHtmlTextTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(clean_html_text("hello"), str)

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(clean_html_text(""), "")

    def test_plain_text_unchanged(self) -> None:
        self.assertEqual(clean_html_text("hello world"), "hello world")

    def test_strips_bold_tags(self) -> None:
        result = clean_html_text("<b>hello</b>")
        self.assertNotIn("<b>", result)
        self.assertIn("hello", result)

    def test_strips_italic_tags(self) -> None:
        result = clean_html_text("<i>world</i>")
        self.assertNotIn("<i>", result)
        self.assertIn("world", result)

    def test_strips_script_block(self) -> None:
        result = clean_html_text("<script>alert('xss')</script>safe")
        self.assertNotIn("script", result)
        self.assertNotIn("alert", result)
        self.assertIn("safe", result)

    def test_strips_style_block(self) -> None:
        result = clean_html_text("<style>.a{color:red}</style>body")
        self.assertNotIn("style", result)
        self.assertIn("body", result)

    def test_decodes_html_entities(self) -> None:
        result = clean_html_text("&lt;br&gt; &amp; &quot;")
        self.assertIn("<br>", result)
        self.assertIn("&", result)
        self.assertIn('"', result)

    def test_collapses_whitespace(self) -> None:
        result = clean_html_text("  too   many   spaces  ")
        self.assertNotIn("  ", result)

    def test_nested_tags_stripped(self) -> None:
        result = clean_html_text("<div><p>text</p></div>")
        self.assertNotIn("<", result)
        self.assertIn("text", result)

    def test_multiline_script_stripped(self) -> None:
        html = "<script>\nvar x = 1;\nalert(x);\n</script>content"
        result = clean_html_text(html)
        self.assertNotIn("var x", result)
        self.assertIn("content", result)

    def test_attribute_containing_tags_stripped(self) -> None:
        result = clean_html_text('<a href="url">link</a>')
        self.assertNotIn("<a", result)
        self.assertIn("link", result)


# project_brain/chat.py - build_code_prompt

class BuildCodePromptTest(unittest.TestCase):

    def _call(self, goal="do stuff", path="src/main.py", content="print(1)", refs=None):
        return build_code_prompt(goal, path, content, refs or [])

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call(), str)

    def test_contains_goal(self) -> None:
        result = self._call(goal="refactor this function")
        self.assertIn("refactor this function", result)

    def test_contains_file_path(self) -> None:
        result = self._call(path="app/models.py")
        self.assertIn("app/models.py", result)

    def test_contains_file_content(self) -> None:
        result = self._call(content="def foo(): pass")
        self.assertIn("def foo(): pass", result)

    def test_empty_refs_no_crash(self) -> None:
        result = self._call(refs=[])
        self.assertIsInstance(result, str)

    def test_single_ref_file_included(self) -> None:
        refs = [{"path": "ref.py", "content": "REF_CONTENT"}]
        result = self._call(refs=refs)
        self.assertIn("ref.py", result)
        self.assertIn("REF_CONTENT", result)

    def test_multiple_refs_all_included(self) -> None:
        refs = [
            {"path": "a.py", "content": "AAA"},
            {"path": "b.py", "content": "BBB"},
        ]
        result = self._call(refs=refs)
        self.assertIn("a.py", result)
        self.assertIn("b.py", result)
        self.assertIn("AAA", result)
        self.assertIn("BBB", result)

    def test_nonempty_result(self) -> None:
        result = self._call()
        self.assertGreater(len(result), 20)


# project_brain/files.py - normalize_relative_path

class NormalizeRelativePathTest(unittest.TestCase):

    def _ok(self, raw: str) -> Path:
        return normalize_relative_path(raw)

    def _err(self, raw: str) -> None:
        """Assert HTTPException is raised."""
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            normalize_relative_path(raw)

    def test_returns_path_object(self) -> None:
        result = self._ok("src/main.py")
        self.assertIsInstance(result, Path)

    def test_simple_relative_path(self) -> None:
        result = self._ok("src/main.py")
        self.assertIn("main.py", str(result))

    def test_backslash_normalised(self) -> None:
        result = self._ok("src\\main.py")
        self.assertIn("main.py", str(result))

    def test_empty_string_raises(self) -> None:
        self._err("")

    def test_whitespace_only_raises(self) -> None:
        self._err("   ")

    def test_parent_traversal_raises(self) -> None:
        self._err("../etc/passwd")

    def test_nested_parent_traversal_raises(self) -> None:
        self._err("src/../../etc/passwd")

    def test_leading_slash_stripped(self) -> None:
        # On all platforms, leading slash is stripped so result is relative
        result = self._ok("/src/file.py")
        self.assertFalse(result.is_absolute())


# project_brain/files.py - looks_text_file

class LooksTextFileTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(looks_text_file(Path("file.py")), bool)

    def test_python_file_is_text(self) -> None:
        self.assertTrue(looks_text_file(Path("script.py")))

    def test_js_file_is_text(self) -> None:
        self.assertTrue(looks_text_file(Path("app.js")))

    def test_txt_file_is_text(self) -> None:
        self.assertTrue(looks_text_file(Path("readme.txt")))

    def test_md_file_is_text(self) -> None:
        self.assertTrue(looks_text_file(Path("README.md")))

    def test_json_file_is_text(self) -> None:
        self.assertTrue(looks_text_file(Path("config.json")))

    def test_jpg_is_not_text(self) -> None:
        self.assertFalse(looks_text_file(Path("image.jpg")))

    def test_png_is_not_text(self) -> None:
        self.assertFalse(looks_text_file(Path("photo.png")))

    def test_pdf_is_not_text(self) -> None:
        self.assertFalse(looks_text_file(Path("doc.pdf")))

    def test_zip_is_not_text(self) -> None:
        self.assertFalse(looks_text_file(Path("archive.zip")))

    def test_dockerfile_is_text(self) -> None:
        # TEXT_NAMES includes 'Dockerfile'
        self.assertTrue(looks_text_file(Path("Dockerfile")))

    def test_gitignore_is_text(self) -> None:
        self.assertTrue(looks_text_file(Path(".gitignore")))

    def test_case_insensitive_suffix(self) -> None:
        self.assertTrue(looks_text_file(Path("FILE.PY")))


# project_brain/files.py - hash_bytes

class HashBytesTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(hash_bytes(b"hello"), str)

    def test_deterministic(self) -> None:
        self.assertEqual(hash_bytes(b"hello"), hash_bytes(b"hello"))

    def test_different_input_different_hash(self) -> None:
        self.assertNotEqual(hash_bytes(b"hello"), hash_bytes(b"world"))

    def test_empty_bytes_returns_sha256_of_empty(self) -> None:
        result = hash_bytes(b"")
        # SHA256 of empty is known
        self.assertEqual(result, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_result_is_hex_string(self) -> None:
        result = hash_bytes(b"test")
        self.assertRegex(result, r"^[0-9a-f]+$")

    def test_result_length_is_64(self) -> None:
        result = hash_bytes(b"any bytes here")
        self.assertEqual(len(result), 64)


# project_brain/uploads.py - safe_filename

class SafeFilenameTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(safe_filename("file.txt"), str)

    def test_clean_name_unchanged(self) -> None:
        result = safe_filename("my-file_name.txt")
        self.assertEqual(result, "my-file_name.txt")

    def test_spaces_replaced(self) -> None:
        result = safe_filename("my file.txt")
        self.assertNotIn(" ", result)

    def test_special_chars_replaced(self) -> None:
        result = safe_filename("hello@world#.py")
        self.assertNotIn("@", result)
        self.assertNotIn("#", result)

    def test_empty_string_returns_attachment(self) -> None:
        result = safe_filename("")
        self.assertEqual(result, "attachment")

    def test_none_returns_attachment(self) -> None:
        result = safe_filename(None)  # type: ignore[arg-type]
        self.assertEqual(result, "attachment")

    def test_long_name_truncated(self) -> None:
        result = safe_filename("a" * 200 + ".txt")
        self.assertLessEqual(len(result), 120)

    def test_dot_preserved(self) -> None:
        result = safe_filename("file.txt")
        self.assertIn(".", result)

    def test_dash_preserved(self) -> None:
        result = safe_filename("my-file")
        self.assertIn("-", result)


# project_brain/uploads.py - attachment_summary

class AttachmentSummaryTest(unittest.TestCase):

    def _item(self, **kwargs):
        defaults = {
            "id": "abc123",
            "name": "document.pdf",
            "size": 12345,
            "suffix": ".pdf",
            "source": "upload",
            "text_available": True,
            "text": "some content here",
        }
        defaults.update(kwargs)
        return defaults

    def test_returns_dict(self) -> None:
        self.assertIsInstance(attachment_summary(self._item()), dict)

    def test_required_keys_present(self) -> None:
        result = attachment_summary(self._item())
        for key in ("id", "name", "size", "suffix", "source", "text_available", "preview"):
            self.assertIn(key, result)

    def test_id_preserved(self) -> None:
        result = attachment_summary(self._item(id="xyz"))
        self.assertEqual(result["id"], "xyz")

    def test_name_preserved(self) -> None:
        result = attachment_summary(self._item(name="my_doc.pdf"))
        self.assertEqual(result["name"], "my_doc.pdf")

    def test_size_preserved(self) -> None:
        result = attachment_summary(self._item(size=99999))
        self.assertEqual(result["size"], 99999)

    def test_suffix_preserved(self) -> None:
        result = attachment_summary(self._item(suffix=".docx"))
        self.assertEqual(result["suffix"], ".docx")

    def test_text_available_preserved(self) -> None:
        result = attachment_summary(self._item(text_available=False))
        self.assertFalse(result["text_available"])

    def test_preview_from_text(self) -> None:
        result = attachment_summary(self._item(text="preview content"))
        self.assertEqual(result["preview"], "preview content")

    def test_preview_truncated_at_1200(self) -> None:
        result = attachment_summary(self._item(text="x" * 2000))
        self.assertLessEqual(len(result["preview"]), 1200)

    def test_no_text_key_preview_empty(self) -> None:
        item = self._item()
        del item["text"]
        result = attachment_summary(item)
        self.assertEqual(result["preview"], "")


# workflows/execution.py - parse_workflow_datetime

class ParseWorkflowDatetimeTest(unittest.TestCase):

    def test_returns_datetime_or_none(self) -> None:
        result = parse_workflow_datetime("2024-01-01T00:00:00")
        self.assertIsInstance(result, datetime)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(parse_workflow_datetime(None))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(parse_workflow_datetime(""))

    def test_invalid_string_returns_none(self) -> None:
        self.assertIsNone(parse_workflow_datetime("not-a-date"))

    def test_naive_datetime_gets_utc(self) -> None:
        result = parse_workflow_datetime("2024-06-15T12:00:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_tz_aware_datetime_converted_to_utc(self) -> None:
        # +05:00 -> UTC, 12:00 - 5:00 = 07:00
        result = parse_workflow_datetime("2024-06-15T12:00:00+05:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 7)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_utc_datetime_unchanged(self) -> None:
        result = parse_workflow_datetime("2024-06-15T10:30:00+00:00")
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.minute, 30)

    def test_result_always_utc(self) -> None:
        result = parse_workflow_datetime("2024-01-01T00:00:00+03:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_random_invalid_text_returns_none(self) -> None:
        self.assertIsNone(parse_workflow_datetime("hello world"))


# workflows/execution.py - workflow_step_index

class WorkflowStepIndexTest(unittest.TestCase):

    def test_returns_int(self) -> None:
        self.assertIsInstance(workflow_step_index("a", ["a", "b"], {}), int)

    def test_first_step_returns_1(self) -> None:
        self.assertEqual(workflow_step_index("a", ["a", "b", "c"], {}), 1)

    def test_second_step_returns_2(self) -> None:
        self.assertEqual(workflow_step_index("b", ["a", "b", "c"], {}), 2)

    def test_last_step_returns_count(self) -> None:
        self.assertEqual(workflow_step_index("c", ["a", "b", "c"], {}), 3)

    def test_not_found_uses_step_results_len(self) -> None:
        # current_step_id not in ordered_ids -> len(step_results) + 1
        self.assertEqual(workflow_step_index("x", ["a", "b"], {}), 1)

    def test_not_found_two_step_results(self) -> None:
        result = workflow_step_index("x", ["a", "b", "c"], {"a": "ok", "b": "ok"})
        self.assertEqual(result, 3)

    def test_empty_ordered_ids_not_found(self) -> None:
        result = workflow_step_index("a", [], {})
        self.assertEqual(result, 1)

    def test_empty_step_results_not_found_returns_1(self) -> None:
        result = workflow_step_index("missing", ["a", "b"], {})
        self.assertEqual(result, 1)


# application/chat/identity_guard.py - _contains_model_identity

class ContainsModelIdentityTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(_contains_model_identity("text"), bool)

    def test_empty_string_false(self) -> None:
        self.assertFalse(_contains_model_identity(""))

    def test_none_false(self) -> None:
        self.assertFalse(_contains_model_identity(None))  # type: ignore[arg-type]

    def test_plain_text_false(self) -> None:
        self.assertFalse(_contains_model_identity("The weather is sunny today"))

    def test_model_name_only_false(self) -> None:
        # Model name without first-person pronoun -> False
        self.assertFalse(_contains_model_identity("llama is a great model"))

    def test_first_person_only_false(self) -> None:
        # No model name -> False
        self.assertFalse(_contains_model_identity("я думаю о погоде"))

    def test_model_and_first_person_true(self) -> None:
        # First-person token plus model name -> True
        self.assertTrue(_contains_model_identity("я llama"))

    def test_gpt_with_first_person_true(self) -> None:
        self.assertTrue(_contains_model_identity("я gpt"))

    def test_gemma_with_first_person_true(self) -> None:
        self.assertTrue(_contains_model_identity("я gemma"))

    def test_case_insensitive_model(self) -> None:
        # The regex is case-insensitive
        self.assertTrue(_contains_model_identity("я LLAMA"))


# application/chat/identity_guard.py - _still_drifting

class StillDriftingTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(_still_drifting("text", "Elira"), bool)

    def test_empty_string_false(self) -> None:
        self.assertFalse(_still_drifting("", "Elira"))

    def test_none_false(self) -> None:
        self.assertFalse(_still_drifting(None, "Elira"))  # type: ignore[arg-type]

    def test_plain_text_false(self) -> None:
        self.assertFalse(_still_drifting("The answer is 42", "Elira"))

    def test_model_name_without_pronoun_false(self) -> None:
        self.assertFalse(_still_drifting("llama is popular", "Elira"))

    def test_drift_detected_true(self) -> None:
        self.assertTrue(_still_drifting("я llama, твой помощник", "Elira"))

    def test_gpt_drift_true(self) -> None:
        self.assertTrue(_still_drifting("я gpt", "Elira"))


# application/chat/identity_guard.py - _rewrite_identity_drift

class RewriteIdentityDriftTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_rewrite_identity_drift("text", "Elira"), str)

    def test_empty_string_returns_string(self) -> None:
        result = _rewrite_identity_drift("", "Elira")
        self.assertIsInstance(result, str)

    def test_clean_text_preserved(self) -> None:
        result = _rewrite_identity_drift("The weather is nice.", "Elira")
        self.assertIn("weather", result)

    def test_drift_sentence_removed(self) -> None:
        # Drifting sentence with model name + first person should be removed
        result = _rewrite_identity_drift("я llama. Good day.", "Elira")
        # The drifting part should not appear in the result
        self.assertNotIn("llama", result)

    def test_non_drifting_sentence_kept(self) -> None:
        # Non-drifting part should survive
        result = _rewrite_identity_drift("я llama. The answer is 42.", "Elira")
        self.assertIn("42", result)

    def test_result_not_empty_for_all_drift(self) -> None:
        # If everything drifts, a fallback reply is returned (non-empty)
        result = _rewrite_identity_drift("я llama", "Elira")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_persona_name_in_fallback(self) -> None:
        result = _rewrite_identity_drift("я gpt", "MyBot")
        # Fallback should include the persona name
        self.assertIn("MyBot", result)


if __name__ == "__main__":
    unittest.main()
