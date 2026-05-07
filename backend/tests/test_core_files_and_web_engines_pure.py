"""Tests for pure helpers in app.core.files and app.core.web_engines.

All functions under test are pure (no real DB/network/FS side-effects):
  core/files.py:
    truncate_text, normalize_path, should_auto_save_memory,
    extract_imports_from_python, sanitize_chat_name,
    export_chat_as_markdown, get_chat_rel_label, now_stamp
  core/web_engines.py:
    Constants: SUPPORTED_SEARCH_ENGINES, ENGINE_LABELS, ENGINE_PRIORITY,
               CURRENT_WORLD_ENGINES, KZ_LOCAL_NEWS_DOMAINS
    clean_url, extract_domain, domain_matches, re_sub_html,
    engine_available (duckduckgo/wikipedia branch only),
    resolve_search_engines (no-tavily branch)
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.files import (  # noqa: E402
    truncate_text,
    normalize_path,
    should_auto_save_memory,
    extract_imports_from_python,
    sanitize_chat_name,
    export_chat_as_markdown,
    get_chat_rel_label,
    now_stamp,
)
from app.core.web_engines import (  # noqa: E402
    SUPPORTED_SEARCH_ENGINES,
    ENGINE_LABELS,
    ENGINE_PRIORITY,
    CURRENT_WORLD_ENGINES,
    KZ_LOCAL_NEWS_DOMAINS,
    clean_url,
    extract_domain,
    domain_matches,
    re_sub_html,
    engine_available,
    resolve_search_engines,
)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — now_stamp
# ─────────────────────────────────────────────────────────────────────────────

class NowStampTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(now_stamp(), str)

    def test_nonempty(self) -> None:
        self.assertTrue(len(now_stamp()) > 0)

    def test_contains_date_separator(self) -> None:
        self.assertIn("-", now_stamp())

    def test_contains_time_separator(self) -> None:
        self.assertIn("_", now_stamp())

    def test_format_length_reasonable(self) -> None:
        # "2026-05-07_14-30-00" = 19 chars
        stamp = now_stamp()
        self.assertGreaterEqual(len(stamp), 17)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — truncate_text
# ─────────────────────────────────────────────────────────────────────────────

class TruncateTextTest(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        self.assertEqual(truncate_text("hello", 100), "hello")

    def test_exact_limit_unchanged(self) -> None:
        text = "A" * 12000
        self.assertEqual(truncate_text(text), text)

    def test_over_limit_truncated(self) -> None:
        text = "B" * 13000
        result = truncate_text(text)
        self.assertLess(len(result), len(text))

    def test_truncated_ends_with_marker(self) -> None:
        text = "C" * 15000
        result = truncate_text(text)
        self.assertIn("обрезан", result)

    def test_custom_max_chars(self) -> None:
        result = truncate_text("hello world", max_chars=5)
        self.assertTrue(result.startswith("hello"))

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(truncate_text(""), "")

    def test_none_treated_as_empty(self) -> None:
        self.assertEqual(truncate_text(None), "")  # type: ignore[arg-type]

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = truncate_text("  hi  ", 100)
        self.assertEqual(result, "hi")

    def test_returns_string(self) -> None:
        self.assertIsInstance(truncate_text("test"), str)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — normalize_path
# ─────────────────────────────────────────────────────────────────────────────

class NormalizePathTest(unittest.TestCase):
    def test_returns_path(self) -> None:
        self.assertIsInstance(normalize_path("backend/app"), Path)

    def test_strips_quotes(self) -> None:
        result = normalize_path('"my file.txt"')
        self.assertEqual(result.name, "my file.txt")

    def test_strips_whitespace(self) -> None:
        result = normalize_path("  backend/app  ")
        self.assertIn("backend", str(result))

    def test_simple_path(self) -> None:
        result = normalize_path("foo/bar.py")
        self.assertEqual(result.name, "bar.py")


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — should_auto_save_memory
# ─────────────────────────────────────────────────────────────────────────────

class ShouldAutoSaveMemoryTest(unittest.TestCase):
    def test_returns_bool(self) -> None:
        self.assertIsInstance(should_auto_save_memory(""), bool)

    def test_short_text_false(self) -> None:
        self.assertFalse(should_auto_save_memory("итог — всё готово"))

    def test_long_text_with_trigger_true(self) -> None:
        text = "итог " + "A" * 200
        self.assertTrue(should_auto_save_memory(text))

    def test_long_text_without_trigger_false(self) -> None:
        # no trigger words: triggers = ["итог","вывод","важно","рекоменд","план","шаг","решение","ключев","summary"]
        text = "обычный разговор о погоде и природе без специальных терминов " + "A" * 180
        self.assertFalse(should_auto_save_memory(text))

    def test_summary_trigger_word_true(self) -> None:
        text = "summary of everything " + "x" * 180
        self.assertTrue(should_auto_save_memory(text))

    def test_plan_trigger_word_true(self) -> None:
        text = "план действий на неделю " + "y" * 180
        self.assertTrue(should_auto_save_memory(text))

    def test_empty_string_false(self) -> None:
        self.assertFalse(should_auto_save_memory(""))

    def test_none_false(self) -> None:
        self.assertFalse(should_auto_save_memory(None))  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — extract_imports_from_python
# ─────────────────────────────────────────────────────────────────────────────

class ExtractImportsFromPythonTest(unittest.TestCase):
    def test_returns_list(self) -> None:
        self.assertIsInstance(extract_imports_from_python(""), list)

    def test_simple_import(self) -> None:
        result = extract_imports_from_python("import os")
        self.assertIn("os", result)

    def test_from_import(self) -> None:
        result = extract_imports_from_python("from pathlib import Path")
        self.assertIn("pathlib", result)

    def test_aliased_import_stripped(self) -> None:
        result = extract_imports_from_python("import numpy as np")
        self.assertIn("numpy", result)
        self.assertNotIn("np", result)

    def test_multiple_imports(self) -> None:
        code = "import os\nimport sys\nfrom pathlib import Path"
        result = extract_imports_from_python(code)
        self.assertIn("os", result)
        self.assertIn("sys", result)
        self.assertIn("pathlib", result)

    def test_empty_code_empty_list(self) -> None:
        self.assertEqual(extract_imports_from_python(""), [])

    def test_non_import_lines_ignored(self) -> None:
        result = extract_imports_from_python("x = 1\nprint('hello')")
        self.assertEqual(result, [])

    def test_mixed_code(self) -> None:
        code = "x = 1\nimport json\nprint(x)"
        result = extract_imports_from_python(code)
        self.assertIn("json", result)
        self.assertEqual(len(result), 1)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — sanitize_chat_name
# ─────────────────────────────────────────────────────────────────────────────

class SanitizeChatNameTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(sanitize_chat_name("hello"), str)

    def test_plain_name_unchanged(self) -> None:
        self.assertEqual(sanitize_chat_name("my chat"), "my chat")

    def test_slash_replaced(self) -> None:
        result = sanitize_chat_name("chat/name")
        self.assertNotIn("/", result)

    def test_backslash_replaced(self) -> None:
        result = sanitize_chat_name("chat\\name")
        self.assertNotIn("\\", result)

    def test_colon_replaced(self) -> None:
        result = sanitize_chat_name("chat:name")
        self.assertNotIn(":", result)

    def test_asterisk_replaced(self) -> None:
        result = sanitize_chat_name("chat*name")
        self.assertNotIn("*", result)

    def test_question_mark_replaced(self) -> None:
        result = sanitize_chat_name("chat?name")
        self.assertNotIn("?", result)

    def test_multiple_spaces_collapsed(self) -> None:
        result = sanitize_chat_name("hello   world")
        self.assertNotIn("  ", result)

    def test_empty_string_gets_fallback(self) -> None:
        result = sanitize_chat_name("")
        self.assertGreater(len(result), 0)

    def test_none_gets_fallback(self) -> None:
        result = sanitize_chat_name(None)  # type: ignore[arg-type]
        self.assertGreater(len(result), 0)

    def test_trailing_dots_stripped(self) -> None:
        result = sanitize_chat_name("chat...")
        self.assertFalse(result.endswith("."))


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — export_chat_as_markdown
# ─────────────────────────────────────────────────────────────────────────────

class ExportChatAsMarkdownTest(unittest.TestCase):
    def _messages(self):
        return [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

    def test_returns_string(self) -> None:
        self.assertIsInstance(export_chat_as_markdown(self._messages(), "gemma3:4b"), str)

    def test_contains_model_name(self) -> None:
        result = export_chat_as_markdown(self._messages(), "gemma3:4b")
        self.assertIn("gemma3:4b", result)

    def test_contains_user_content(self) -> None:
        result = export_chat_as_markdown(self._messages(), "model")
        self.assertIn("Hello", result)

    def test_contains_assistant_content(self) -> None:
        result = export_chat_as_markdown(self._messages(), "model")
        self.assertIn("Hi there", result)

    def test_has_markdown_heading(self) -> None:
        result = export_chat_as_markdown(self._messages(), "model")
        self.assertIn("#", result)

    def test_empty_messages_no_content(self) -> None:
        result = export_chat_as_markdown([], "model")
        self.assertIsInstance(result, str)
        self.assertIn("model", result)

    def test_user_role_label_present(self) -> None:
        result = export_chat_as_markdown(self._messages(), "model")
        self.assertIn("Пользователь", result)

    def test_assistant_role_label_present(self) -> None:
        result = export_chat_as_markdown(self._messages(), "model")
        self.assertIn("Ассистент", result)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — get_chat_rel_label
# ─────────────────────────────────────────────────────────────────────────────

class GetChatRelLabelTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(get_chat_rel_label(Path("/some/path/chat.json")), str)

    def test_fallback_returns_name(self) -> None:
        # Path not under CHAT_DIR → fallback to path.name
        result = get_chat_rel_label(Path("/totally/unrelated/myfile.json"))
        self.assertEqual(result, "myfile.json")

    def test_fallback_does_not_raise(self) -> None:
        # Should never raise even for weird paths
        try:
            get_chat_rel_label(Path("x.json"))
        except Exception as e:
            self.fail(f"get_chat_rel_label raised {e}")

    def test_uses_forward_slash(self) -> None:
        from app.core.config import CHAT_DIR
        path = CHAT_DIR / "subdir" / "myfile.json"
        result = get_chat_rel_label(path)
        self.assertNotIn("\\", result)


# ─────────────────────────────────────────────────────────────────────────────
# core/web_engines.py — Constants
# ─────────────────────────────────────────────────────────────────────────────

class WebEngineConstantsTest(unittest.TestCase):
    def test_supported_engines_is_tuple_or_collection(self) -> None:
        self.assertIn("duckduckgo", SUPPORTED_SEARCH_ENGINES)

    def test_supported_engines_has_wikipedia(self) -> None:
        self.assertIn("wikipedia", SUPPORTED_SEARCH_ENGINES)

    def test_supported_engines_has_tavily(self) -> None:
        self.assertIn("tavily", SUPPORTED_SEARCH_ENGINES)

    def test_engine_labels_is_dict(self) -> None:
        self.assertIsInstance(ENGINE_LABELS, dict)

    def test_engine_labels_has_duckduckgo(self) -> None:
        self.assertIn("duckduckgo", ENGINE_LABELS)

    def test_engine_labels_has_wikipedia(self) -> None:
        self.assertIn("wikipedia", ENGINE_LABELS)

    def test_engine_labels_values_are_strings(self) -> None:
        for k, v in ENGINE_LABELS.items():
            self.assertIsInstance(v, str, f"Label for {k} is not a string")

    def test_engine_priority_is_dict(self) -> None:
        self.assertIsInstance(ENGINE_PRIORITY, dict)

    def test_engine_priority_tavily_is_lowest_number(self) -> None:
        self.assertEqual(ENGINE_PRIORITY["tavily"], 0)

    def test_current_world_engines_is_set_like(self) -> None:
        self.assertIn("duckduckgo", CURRENT_WORLD_ENGINES)

    def test_kz_local_news_domains_nonempty(self) -> None:
        self.assertGreater(len(KZ_LOCAL_NEWS_DOMAINS), 0)

    def test_kz_local_news_domains_contains_tengri(self) -> None:
        self.assertIn("tengrinews.kz", KZ_LOCAL_NEWS_DOMAINS)


# ─────────────────────────────────────────────────────────────────────────────
# core/web_engines.py — clean_url
# ─────────────────────────────────────────────────────────────────────────────

class CleanUrlTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(clean_url("https://example.com"), str)

    def test_plain_url_unchanged(self) -> None:
        url = "https://example.com/path"
        self.assertEqual(clean_url(url), url)

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(clean_url(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(clean_url(None), "")  # type: ignore[arg-type]

    def test_google_redirect_unwrapped(self) -> None:
        google_url = "/url?q=https%3A%2F%2Fexample.com&sa=U"
        result = clean_url(google_url)
        self.assertIn("example.com", result)

    def test_percent_encoded_url_decoded(self) -> None:
        url = "https://example.com/path%20with%20spaces"
        result = clean_url(url)
        self.assertIn("path with spaces", result)

    def test_strips_leading_whitespace(self) -> None:
        result = clean_url("  https://example.com  ")
        self.assertFalse(result.startswith(" "))


# ─────────────────────────────────────────────────────────────────────────────
# core/web_engines.py — extract_domain
# ─────────────────────────────────────────────────────────────────────────────

class ExtractDomainTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(extract_domain("https://example.com"), str)

    def test_simple_domain(self) -> None:
        self.assertEqual(extract_domain("https://example.com/page"), "example.com")

    def test_www_prefix_stripped(self) -> None:
        self.assertEqual(extract_domain("https://www.example.com"), "example.com")

    def test_subdomain_preserved(self) -> None:
        result = extract_domain("https://news.bbc.co.uk/article")
        self.assertIn("bbc.co.uk", result)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(extract_domain(""), "")

    def test_returns_lowercase(self) -> None:
        result = extract_domain("https://Example.COM/Path")
        self.assertEqual(result, result.lower())

    def test_with_port_number(self) -> None:
        result = extract_domain("http://localhost:8080/api")
        self.assertIn("localhost", result)

    def test_tengrinews_domain(self) -> None:
        result = extract_domain("https://tengrinews.kz/story/123")
        self.assertEqual(result, "tengrinews.kz")


# ─────────────────────────────────────────────────────────────────────────────
# core/web_engines.py — domain_matches
# ─────────────────────────────────────────────────────────────────────────────

class DomainMatchesTest(unittest.TestCase):
    def test_returns_bool(self) -> None:
        self.assertIsInstance(domain_matches("example.com", ["example.com"]), bool)

    def test_exact_match(self) -> None:
        self.assertTrue(domain_matches("example.com", ["example.com"]))

    def test_subdomain_match(self) -> None:
        self.assertTrue(domain_matches("news.example.com", ["example.com"]))

    def test_no_match(self) -> None:
        self.assertFalse(domain_matches("other.com", ["example.com"]))

    def test_empty_expected_no_match(self) -> None:
        self.assertFalse(domain_matches("example.com", []))

    def test_multiple_expected_one_matches(self) -> None:
        self.assertTrue(domain_matches("tengrinews.kz", ["nur.kz", "tengrinews.kz"]))

    def test_partial_suffix_not_matched(self) -> None:
        # "notexample.com" should NOT match "example.com"
        self.assertFalse(domain_matches("notexample.com", ["example.com"]))

    def test_kz_domain_in_local_news_list(self) -> None:
        self.assertTrue(domain_matches("tengrinews.kz", KZ_LOCAL_NEWS_DOMAINS))

    def test_foreign_domain_not_in_kz_list(self) -> None:
        self.assertFalse(domain_matches("bbc.co.uk", KZ_LOCAL_NEWS_DOMAINS))


# ─────────────────────────────────────────────────────────────────────────────
# core/web_engines.py — re_sub_html
# ─────────────────────────────────────────────────────────────────────────────

class ReSubHtmlTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(re_sub_html("hello"), str)

    def test_plain_text_unchanged(self) -> None:
        self.assertEqual(re_sub_html("hello world"), "hello world")

    def test_removes_simple_tag(self) -> None:
        result = re_sub_html("<b>bold</b>")
        self.assertNotIn("<b>", result)
        self.assertNotIn("</b>", result)
        self.assertIn("bold", result)

    def test_removes_anchor_tag(self) -> None:
        result = re_sub_html('<a href="http://x.com">link</a>')
        self.assertNotIn("<a", result)
        self.assertIn("link", result)

    def test_removes_multiple_tags(self) -> None:
        result = re_sub_html("<p><em>text</em></p>")
        self.assertNotIn("<", result)
        self.assertIn("text", result)

    def test_empty_string_stays_empty(self) -> None:
        self.assertEqual(re_sub_html(""), "")

    def test_self_closing_tag_removed(self) -> None:
        result = re_sub_html("before<br/>after")
        self.assertNotIn("<br/>", result)
        self.assertIn("before", result)
        self.assertIn("after", result)


# ─────────────────────────────────────────────────────────────────────────────
# core/web_engines.py — engine_available
# ─────────────────────────────────────────────────────────────────────────────

class EngineAvailableTest(unittest.TestCase):
    def test_returns_bool(self) -> None:
        self.assertIsInstance(engine_available("duckduckgo"), bool)

    def test_duckduckgo_always_available(self) -> None:
        self.assertTrue(engine_available("duckduckgo"))

    def test_wikipedia_always_available(self) -> None:
        self.assertTrue(engine_available("wikipedia"))

    def test_tavily_false_without_key(self) -> None:
        # Remove key if present to test the False branch
        old = os.environ.pop("TAVILY_API_KEY", None)
        try:
            self.assertFalse(engine_available("tavily"))
        finally:
            if old is not None:
                os.environ["TAVILY_API_KEY"] = old

    def test_unknown_engine_false(self) -> None:
        self.assertFalse(engine_available("google_custom_search"))


# ─────────────────────────────────────────────────────────────────────────────
# core/web_engines.py — resolve_search_engines
# ─────────────────────────────────────────────────────────────────────────────

class ResolveSearchEnginesTest(unittest.TestCase):
    def _no_tavily(self):
        """Ensure TAVILY_API_KEY is absent for deterministic results."""
        return os.environ.pop("TAVILY_API_KEY", None)

    def _restore(self, old):
        if old is not None:
            os.environ["TAVILY_API_KEY"] = old

    def test_returns_tuple(self) -> None:
        old = self._no_tavily()
        try:
            self.assertIsInstance(resolve_search_engines(), tuple)
        finally:
            self._restore(old)

    def test_always_includes_duckduckgo(self) -> None:
        old = self._no_tavily()
        try:
            self.assertIn("duckduckgo", resolve_search_engines())
        finally:
            self._restore(old)

    def test_always_includes_wikipedia(self) -> None:
        old = self._no_tavily()
        try:
            self.assertIn("wikipedia", resolve_search_engines())
        finally:
            self._restore(old)

    def test_no_duplicates(self) -> None:
        old = self._no_tavily()
        try:
            result = resolve_search_engines()
            self.assertEqual(len(result), len(set(result)))
        finally:
            self._restore(old)

    def test_unknown_engine_filtered_out(self) -> None:
        old = self._no_tavily()
        try:
            result = resolve_search_engines(["nonexistent_engine"])
            self.assertNotIn("nonexistent_engine", result)
        finally:
            self._restore(old)

    def test_explicit_engines_subset_used(self) -> None:
        old = self._no_tavily()
        try:
            result = resolve_search_engines(["wikipedia"])
            self.assertIn("wikipedia", result)
        finally:
            self._restore(old)

    def test_none_uses_defaults(self) -> None:
        old = self._no_tavily()
        try:
            result = resolve_search_engines(None)
            self.assertGreater(len(result), 0)
        finally:
            self._restore(old)


if __name__ == "__main__":
    unittest.main()
