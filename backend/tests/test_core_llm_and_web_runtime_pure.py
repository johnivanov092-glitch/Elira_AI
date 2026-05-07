"""Tests for pure helpers in app.core.llm and app.core.web_runtime.

All functions under test are pure (no Ollama calls, no HTTP, no FS):
  core/llm.py:
    _is_ctx_error, estimate_tokens, get_safe_ctx, _trim_history,
    budget_contexts, context_size_warning, clean_code_fence,
    safe_json_parse, split_models_by_type
  core/web_runtime.py:
    result_score, rerank_results, count_preferred_domain_hits,
    dedupe_results, format_search_results
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.llm import (  # noqa: E402
    _is_ctx_error,
    estimate_tokens,
    get_safe_ctx,
    _trim_history,
    budget_contexts,
    context_size_warning,
    clean_code_fence,
    safe_json_parse,
    split_models_by_type,
)
from app.core.web_runtime import (  # noqa: E402
    result_score,
    rerank_results,
    count_preferred_domain_hits,
    dedupe_results,
    format_search_results,
)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — _is_ctx_error
# ─────────────────────────────────────────────────────────────────────────────

class IsCtxErrorTest(unittest.TestCase):
    def test_returns_bool(self) -> None:
        self.assertIsInstance(_is_ctx_error(Exception("some error")), bool)

    def test_context_length_true(self) -> None:
        self.assertTrue(_is_ctx_error(Exception("context length exceeded")))

    def test_kv_cache_true(self) -> None:
        self.assertTrue(_is_ctx_error(Exception("kv cache error")))

    def test_oom_true(self) -> None:
        self.assertTrue(_is_ctx_error(Exception("out of memory")))

    def test_token_limit_true(self) -> None:
        self.assertTrue(_is_ctx_error(Exception("token limit reached")))

    def test_exceeds_true(self) -> None:
        self.assertTrue(_is_ctx_error(Exception("input exceeds the model context")))

    def test_generic_error_false(self) -> None:
        self.assertFalse(_is_ctx_error(Exception("connection refused")))

    def test_empty_message_false(self) -> None:
        self.assertFalse(_is_ctx_error(Exception("")))

    def test_case_insensitive(self) -> None:
        self.assertTrue(_is_ctx_error(Exception("Context Length Too Long")))


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — estimate_tokens
# ─────────────────────────────────────────────────────────────────────────────

class EstimateTokensTest(unittest.TestCase):
    def test_returns_int(self) -> None:
        self.assertIsInstance(estimate_tokens("hello"), int)

    def test_empty_string_at_least_one(self) -> None:
        self.assertGreaterEqual(estimate_tokens(""), 1)

    def test_100_chars_roughly_25_tokens(self) -> None:
        self.assertEqual(estimate_tokens("A" * 100), 25)

    def test_400_chars_100_tokens(self) -> None:
        self.assertEqual(estimate_tokens("B" * 400), 100)

    def test_always_positive(self) -> None:
        for text in ["", "x", "xy", "xyz"]:
            self.assertGreater(estimate_tokens(text), 0)

    def test_longer_text_more_tokens(self) -> None:
        self.assertGreater(estimate_tokens("A" * 1000), estimate_tokens("A" * 100))


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — get_safe_ctx
# ─────────────────────────────────────────────────────────────────────────────

class GetSafeCtxTest(unittest.TestCase):
    def test_returns_int(self) -> None:
        self.assertIsInstance(get_safe_ctx("gemma3:4b"), int)

    def test_unknown_model_returns_default(self) -> None:
        from app.core.config import DEFAULT_SAFE_CTX
        self.assertEqual(get_safe_ctx("nonexistent:model"), DEFAULT_SAFE_CTX)

    def test_known_model_returns_its_limit(self) -> None:
        from app.core.config import MODEL_SAFE_CTX
        if "qwen3:8b" in MODEL_SAFE_CTX:
            result = get_safe_ctx("qwen3:8b")
            self.assertEqual(result, MODEL_SAFE_CTX["qwen3:8b"])

    def test_requested_above_limit_is_capped(self) -> None:
        # Unknown model has DEFAULT_SAFE_CTX=4096; requesting 99999 gets capped
        result = get_safe_ctx("nonexistent:model", requested_ctx=99999)
        from app.core.config import DEFAULT_SAFE_CTX
        self.assertEqual(result, DEFAULT_SAFE_CTX)

    def test_requested_below_limit_returned_as_is(self) -> None:
        result = get_safe_ctx("nonexistent:model", requested_ctx=512)
        self.assertEqual(result, 512)

    def test_none_requested_returns_hw_limit(self) -> None:
        from app.core.config import DEFAULT_SAFE_CTX
        result = get_safe_ctx("nonexistent:model", requested_ctx=None)
        self.assertEqual(result, DEFAULT_SAFE_CTX)

    def test_result_positive(self) -> None:
        self.assertGreater(get_safe_ctx("gemma3:4b"), 0)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — _trim_history
# ─────────────────────────────────────────────────────────────────────────────

class TrimHistoryTest(unittest.TestCase):
    def _msgs(self, n: int):
        return [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
                for i in range(n)]

    def test_returns_list(self) -> None:
        self.assertIsInstance(_trim_history([]), list)

    def test_empty_history_stays_empty(self) -> None:
        self.assertEqual(_trim_history([]), [])

    def test_short_history_unchanged(self) -> None:
        msgs = self._msgs(4)
        self.assertEqual(_trim_history(msgs, keep=4), msgs)

    def test_long_history_trimmed(self) -> None:
        msgs = self._msgs(20)
        result = _trim_history(msgs, keep=4)
        # keep=4 → keep last 8 messages
        self.assertEqual(len(result), 8)

    def test_trimmed_keeps_latest_messages(self) -> None:
        msgs = self._msgs(10)
        result = _trim_history(msgs, keep=2)
        # Should be last 4 (2 pairs)
        self.assertEqual(result, msgs[-4:])

    def test_default_keep_is_four(self) -> None:
        msgs = self._msgs(20)
        result = _trim_history(msgs)
        self.assertEqual(len(result), 8)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — budget_contexts
# ─────────────────────────────────────────────────────────────────────────────

class BudgetContextsTest(unittest.TestCase):
    def _call(self, num_ctx=8192, **kwargs) -> dict:
        return budget_contexts(num_ctx, **kwargs)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_has_four_keys(self) -> None:
        result = self._call()
        self.assertSetEqual(set(result.keys()), {"file", "project", "web", "memory"})

    def test_all_empty_contexts_stay_empty(self) -> None:
        result = self._call()
        for v in result.values():
            self.assertEqual(v, "")

    def test_short_context_fits_unchanged(self) -> None:
        result = self._call(num_ctx=8192, memory_context="hello world")
        self.assertEqual(result["memory"], "hello world")

    def test_zero_ctx_all_empty(self) -> None:
        result = self._call(num_ctx=0)
        for v in result.values():
            self.assertEqual(v, "")

    def test_very_long_context_truncated(self) -> None:
        long_text = "A" * 100000
        result = self._call(num_ctx=4096, memory_context=long_text)
        self.assertLess(len(result["memory"]), len(long_text))

    def test_truncated_context_has_marker(self) -> None:
        long_text = "B" * 100000
        result = self._call(num_ctx=4096, file_context=long_text)
        if result["file"]:  # may be empty if no budget
            self.assertIn("обрезано", result["file"].lower())

    def test_values_are_strings(self) -> None:
        result = self._call(memory_context="some data")
        for v in result.values():
            self.assertIsInstance(v, str)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — context_size_warning
# ─────────────────────────────────────────────────────────────────────────────

class ContextSizeWarningTest(unittest.TestCase):
    def test_no_warning_when_small_contexts(self) -> None:
        result = context_size_warning(8192, memory_context="hello")
        self.assertIsNone(result)

    def test_warning_when_over_85_pct(self) -> None:
        # Fill 90%+ of 4096 tokens with large contexts
        big = "X" * 20000  # ~5000 tokens
        result = context_size_warning(4096, memory_context=big, file_context=big)
        self.assertIsNotNone(result)

    def test_warning_is_string(self) -> None:
        big = "X" * 20000
        result = context_size_warning(4096, memory_context=big, file_context=big)
        if result is not None:
            self.assertIsInstance(result, str)

    def test_warning_contains_percent(self) -> None:
        big = "X" * 20000
        result = context_size_warning(4096, memory_context=big, file_context=big)
        if result:
            self.assertIn("%", result)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — clean_code_fence
# ─────────────────────────────────────────────────────────────────────────────

class CleanCodeFenceTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(clean_code_fence("hello"), str)

    def test_plain_code_unchanged(self) -> None:
        self.assertEqual(clean_code_fence("x = 1"), "x = 1")

    def test_removes_python_fence(self) -> None:
        result = clean_code_fence("```python\nx = 1\n```")
        self.assertNotIn("```", result)
        self.assertIn("x = 1", result)

    def test_removes_generic_fence(self) -> None:
        result = clean_code_fence("```\ncode here\n```")
        self.assertNotIn("```", result)
        self.assertIn("code here", result)

    def test_strips_outer_whitespace(self) -> None:
        result = clean_code_fence("   code   ")
        self.assertEqual(result, "code")

    def test_empty_fence_empty_result(self) -> None:
        result = clean_code_fence("```python\n```")
        self.assertEqual(result.strip(), "")

    def test_no_fence_preserved(self) -> None:
        code = "def foo():\n    return 42"
        self.assertEqual(clean_code_fence(code), code)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — safe_json_parse
# ─────────────────────────────────────────────────────────────────────────────

class SafeJsonParseTest(unittest.TestCase):
    def test_valid_json_dict(self) -> None:
        self.assertEqual(safe_json_parse('{"key": 1}'), {"key": 1})

    def test_valid_json_list(self) -> None:
        self.assertEqual(safe_json_parse('[1, 2, 3]'), [1, 2, 3])

    def test_invalid_json_returns_none(self) -> None:
        self.assertIsNone(safe_json_parse("not json at all"))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(safe_json_parse(""))

    def test_embedded_json_extracted(self) -> None:
        text = 'some text {"a": 1} more text'
        result = safe_json_parse(text)
        self.assertEqual(result, {"a": 1})

    def test_embedded_list_extracted(self) -> None:
        text = 'prefix [1, 2, 3] suffix'
        result = safe_json_parse(text)
        self.assertEqual(result, [1, 2, 3])

    def test_nested_json(self) -> None:
        data = {"outer": {"inner": [1, 2]}}
        import json
        result = safe_json_parse(json.dumps(data))
        self.assertEqual(result, data)


# ─────────────────────────────────────────────────────────────────────────────
# core/llm.py — split_models_by_type
# ─────────────────────────────────────────────────────────────────────────────

class SplitModelsByTypeTest(unittest.TestCase):
    def test_returns_two_dicts(self) -> None:
        local, cloud = split_models_by_type({"gemma3:4b": "Local model"})
        self.assertIsInstance(local, dict)
        self.assertIsInstance(cloud, dict)

    def test_local_model_in_local(self) -> None:
        local, cloud = split_models_by_type({"gemma3:4b": "Local LLM"})
        self.assertIn("gemma3:4b", local)
        self.assertNotIn("gemma3:4b", cloud)

    def test_cloud_model_by_name_in_cloud(self) -> None:
        local, cloud = split_models_by_type({"gpt-cloud:latest": "Cloud LLM"})
        self.assertIn("gpt-cloud:latest", cloud)

    def test_cloud_model_by_triangle_desc_in_cloud(self) -> None:
        local, cloud = split_models_by_type({"some-model": "△ Cloud powered"})
        self.assertIn("some-model", cloud)

    def test_empty_models_empty_results(self) -> None:
        local, cloud = split_models_by_type({})
        self.assertEqual(local, {})
        self.assertEqual(cloud, {})

    def test_no_overlap_between_local_and_cloud(self) -> None:
        models = {"localA": "local", "cloud-B": "△ cloud"}
        local, cloud = split_models_by_type(models)
        self.assertTrue(set(local.keys()).isdisjoint(set(cloud.keys())))

    def test_all_models_accounted_for(self) -> None:
        models = {"m1": "local", "m2-cloud:x": "desc", "m3": "△ cloud"}
        local, cloud = split_models_by_type(models)
        total = len(local) + len(cloud)
        self.assertEqual(total, len(models))


# ─────────────────────────────────────────────────────────────────────────────
# core/web_runtime.py — result_score
# ─────────────────────────────────────────────────────────────────────────────

class ResultScoreTest(unittest.TestCase):
    def _item(self, href="https://example.com", engine="duckduckgo",
              title="", body=""):
        return {"href": href, "engine": engine, "title": title, "body": body}

    def test_returns_int(self) -> None:
        self.assertIsInstance(result_score(self._item()), int)

    def test_preferred_domain_boost(self) -> None:
        score_preferred = result_score(
            self._item(href="https://tengrinews.kz/story"),
            preferred_domains=["tengrinews.kz"],
        )
        score_other = result_score(self._item(href="https://other.com"))
        self.assertGreater(score_preferred, score_other)

    def test_tavily_engine_bonus(self) -> None:
        score_tavily = result_score(self._item(engine="tavily"))
        score_ddg = result_score(self._item(engine="duckduckgo"))
        self.assertGreater(score_tavily, score_ddg)

    def test_geo_news_wikipedia_penalty(self) -> None:
        score_wiki = result_score(
            self._item(href="https://wikipedia.org/x", engine="wikipedia"),
            intent_kind="geo_news",
        )
        score_ddg = result_score(self._item(engine="duckduckgo"), intent_kind="geo_news")
        self.assertLess(score_wiki, score_ddg)

    def test_finance_high_confidence_boost(self) -> None:
        score_finance = result_score(
            self._item(href="https://nationalbank.kz/rates"),
            intent_kind="finance",
        )
        score_other = result_score(self._item(), intent_kind="finance")
        self.assertGreater(score_finance, score_other)

    def test_historical_wikipedia_boost(self) -> None:
        score_wiki = result_score(
            self._item(engine="wikipedia"),
            intent_kind="historical",
        )
        score_ddg = result_score(self._item(engine="duckduckgo"), intent_kind="historical")
        self.assertGreater(score_wiki, score_ddg)

    def test_local_first_kz_boost(self) -> None:
        score_local = result_score(
            self._item(href="https://tengrinews.kz/story"),
            intent_kind="geo_news",
            local_first=True,
        )
        score_foreign = result_score(
            self._item(href="https://bbc.co.uk/news"),
            intent_kind="geo_news",
            local_first=True,
        )
        self.assertGreater(score_local, score_foreign)


# ─────────────────────────────────────────────────────────────────────────────
# core/web_runtime.py — rerank_results
# ─────────────────────────────────────────────────────────────────────────────

class RerankResultsTest(unittest.TestCase):
    def _items(self):
        return [
            {"href": "https://example.com", "engine": "duckduckgo", "title": "A", "body": ""},
            {"href": "https://tengrinews.kz/x", "engine": "duckduckgo", "title": "B", "body": ""},
            {"href": "https://nationalbank.kz/", "engine": "tavily", "title": "C", "body": ""},
        ]

    def test_returns_list(self) -> None:
        self.assertIsInstance(rerank_results(self._items()), list)

    def test_same_length(self) -> None:
        items = self._items()
        self.assertEqual(len(rerank_results(items)), len(items))

    def test_preferred_domain_rises_to_top(self) -> None:
        items = self._items()
        result = rerank_results(items, preferred_domains=["tengrinews.kz"])
        self.assertEqual(result[0]["href"], "https://tengrinews.kz/x")

    def test_empty_input_empty_output(self) -> None:
        self.assertEqual(rerank_results([]), [])

    def test_returns_dicts(self) -> None:
        for item in rerank_results(self._items()):
            self.assertIsInstance(item, dict)


# ─────────────────────────────────────────────────────────────────────────────
# core/web_runtime.py — count_preferred_domain_hits
# ─────────────────────────────────────────────────────────────────────────────

class CountPreferredDomainHitsTest(unittest.TestCase):
    def _items(self):
        return [
            {"href": "https://tengrinews.kz/story"},
            {"href": "https://nur.kz/news"},
            {"href": "https://bbc.co.uk/article"},
        ]

    def test_returns_int(self) -> None:
        self.assertIsInstance(count_preferred_domain_hits(self._items()), int)

    def test_no_preferred_returns_zero(self) -> None:
        self.assertEqual(count_preferred_domain_hits(self._items()), 0)

    def test_one_match(self) -> None:
        result = count_preferred_domain_hits(
            self._items(), preferred_domains=["tengrinews.kz"]
        )
        self.assertEqual(result, 1)

    def test_two_matches(self) -> None:
        result = count_preferred_domain_hits(
            self._items(), preferred_domains=["tengrinews.kz", "nur.kz"]
        )
        self.assertEqual(result, 2)

    def test_no_hits_returns_zero(self) -> None:
        result = count_preferred_domain_hits(
            self._items(), preferred_domains=["wikipedia.org"]
        )
        self.assertEqual(result, 0)

    def test_empty_results_zero(self) -> None:
        self.assertEqual(count_preferred_domain_hits([], preferred_domains=["tengrinews.kz"]), 0)


# ─────────────────────────────────────────────────────────────────────────────
# core/web_runtime.py — dedupe_results
# ─────────────────────────────────────────────────────────────────────────────

class DedupeResultsTest(unittest.TestCase):
    def _item(self, href, title="t", body="b", engine="ddg"):
        return {"href": href, "title": title, "body": body, "engine": engine}

    def test_returns_list(self) -> None:
        self.assertIsInstance(dedupe_results([]), list)

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(dedupe_results([]), [])

    def test_no_duplicates_unchanged_count(self) -> None:
        items = [self._item("https://a.com"), self._item("https://b.com")]
        self.assertEqual(len(dedupe_results(items)), 2)

    def test_duplicate_href_removed(self) -> None:
        items = [
            self._item("https://a.com"),
            self._item("https://a.com"),
        ]
        self.assertEqual(len(dedupe_results(items)), 1)

    def test_max_results_respected(self) -> None:
        items = [self._item(f"https://site{i}.com") for i in range(10)]
        result = dedupe_results(items, max_results=3)
        self.assertEqual(len(result), 3)

    def test_result_items_have_required_keys(self) -> None:
        items = [self._item("https://example.com", title="Title", body="Body")]
        result = dedupe_results(items)
        self.assertIn("title", result[0])
        self.assertIn("href", result[0])
        self.assertIn("body", result[0])
        self.assertIn("engine", result[0])

    def test_empty_href_dedupe_by_title_body(self) -> None:
        items = [
            {"href": "", "title": "same", "body": "same", "engine": "ddg"},
            {"href": "", "title": "same", "body": "same", "engine": "ddg"},
        ]
        self.assertEqual(len(dedupe_results(items)), 1)


# ─────────────────────────────────────────────────────────────────────────────
# core/web_runtime.py — format_search_results
# ─────────────────────────────────────────────────────────────────────────────

class FormatSearchResultsTest(unittest.TestCase):
    def _items(self):
        return [
            {"title": "First Result", "href": "https://a.com", "body": "Description A", "engine": "duckduckgo"},
            {"title": "Second Result", "href": "https://b.com", "body": "Description B", "engine": "wikipedia"},
        ]

    def test_returns_string(self) -> None:
        self.assertIsInstance(format_search_results(self._items()), str)

    def test_empty_input_empty_string(self) -> None:
        self.assertEqual(format_search_results([]), "")

    def test_contains_title(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("First Result", result)

    def test_contains_href(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("https://a.com", result)

    def test_contains_body(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("Description A", result)

    def test_numbered_from_one(self) -> None:
        result = format_search_results(self._items())
        self.assertIn("[1]", result)
        self.assertIn("[2]", result)

    def test_engine_label_shown(self) -> None:
        result = format_search_results(self._items())
        # ENGINE_LABELS["duckduckgo"] = "DuckDuckGo"
        self.assertIn("DuckDuckGo", result)

    def test_single_item_no_extra_separators(self) -> None:
        single = [{"title": "T", "href": "https://x.com", "body": "B", "engine": "ddg"}]
        result = format_search_results(single)
        self.assertIn("[1]", result)
        self.assertNotIn("[2]", result)


if __name__ == "__main__":
    unittest.main()
