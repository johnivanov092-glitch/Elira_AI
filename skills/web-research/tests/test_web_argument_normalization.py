"""Regression coverage for JSON-encoded web batch arguments."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from webskill.application.code_agent.tools import _web  # noqa: E402
from webskill.infrastructure.search.web_runtime import PageFetchResult  # noqa: E402


def test_encoded_search_queries_execute_as_batch() -> None:
    with patch.object(_web, "_run_search", return_value=[]) as search:
        result = _web.tool_web_search(queries='[" emergency news ", "official notice"]')

    assert result["ok"] is True
    assert sorted(call.args[0] for call in search.call_args_list) == [
        "emergency news", "official notice",
    ]


def test_encoded_fetch_urls_never_execute_an_empty_url() -> None:
    def fetch(url: str, limit: int) -> PageFetchResult:
        return PageFetchResult(text="Source content", final_url=url) if url else PageFetchResult(error="url is empty")

    with patch.object(_web, "_fetch_one", side_effect=fetch) as fetch_page:
        result = _web.tool_web_fetch(urls='["https://example.org/a", "https://example.org/b"]')

    assert result["ok"] is True
    assert sorted(call.args[0] for call in fetch_page.call_args_list) == [
        "https://example.org/a", "https://example.org/b",
    ]


@pytest.mark.parametrize("tool, field", [(_web.tool_web_search, "queries"), (_web.tool_web_fetch, "urls")])
def test_malformed_encoded_batch_reports_format_error(tool, field: str) -> None:
    result = tool(**{field: '["unfinished"'})

    assert result["ok"] is False
    assert result["error"] == "argument_format"
    assert field in result["text"]
    assert "array of non-empty strings" in result["text"]
    assert "is empty" not in result["text"]


@pytest.mark.parametrize("tool_name, field", [("web_search", "queries"), ("web_fetch", "urls")])
def test_native_and_encoded_batches_normalize_identically_without_mutation(tool_name: str, field: str) -> None:
    import json

    values = [" ЧП сегодня ", "official notice", "official notice"]
    native = {field: values, "top_k": 3}
    encoded = {field: json.dumps(values, ensure_ascii=False), "top_k": 3}
    expected = {field: ["ЧП сегодня", "official notice", "official notice"], "top_k": 3}

    assert _web.normalize_web_tool_arguments(tool_name, native) == expected
    assert _web.normalize_web_tool_arguments(tool_name, encoded) == expected
    assert _web.normalize_web_tool_arguments(tool_name, expected) == expected
    assert native[field] is values
    assert values == [" ЧП сегодня ", "official notice", "official notice"]
    assert isinstance(encoded[field], str)


@pytest.mark.parametrize("tool, field, primary", [
    (_web.tool_web_search, "queries", {"query": "valid query"}),
    (_web.tool_web_fetch, "urls", {"url": "https://example.org/valid"}),
])
@pytest.mark.parametrize("bad_batch", ["", "null", '"one target"', '{}', '[null]', '[4]', '[""]',
                                         {}, 1, [None], [4], [" "]])
def test_invalid_plural_never_executes_or_silently_falls_back(tool, field: str, primary: dict, bad_batch) -> None:
    with patch.object(_web, "_run_search") as search, patch.object(_web, "_fetch_one") as fetch:
        result = tool(**primary, **{field: bad_batch})

    assert result["ok"] is False
    assert result["error"] == "argument_format"
    assert field in result["text"]
    search.assert_not_called()
    fetch.assert_not_called()


def test_empty_optional_batches_keep_singular_fallback() -> None:
    with patch("webskill.infrastructure.search.web_search.search_web", return_value={"sources": []}) as search:
        result = _web.tool_web_search(query="valid query", queries="[]")
    assert result["ok"] is True
    assert search.call_args.args[0] == "valid query"

    with patch.object(_web, "_fetch_one", return_value=PageFetchResult(text="Source", final_url="https://example.org/a")) as fetch:
        result = _web.tool_web_fetch(url="https://example.org/a", urls=[])
    assert result["ok"] is True
    assert fetch.call_args.args[0] == "https://example.org/a"


@pytest.mark.parametrize("tool_name, tool, field", [
    ("web_search", _web.tool_web_search, "queries"),
    ("web_fetch", _web.tool_web_fetch, "urls"),
])
@pytest.mark.parametrize("empty_batch", [[], "[]", None])
@pytest.mark.parametrize("fallback", [None, "", " ", 4])
def test_empty_plural_without_valid_singular_is_rejected_before_io(tool_name: str, tool, field: str,
                                                                  empty_batch, fallback) -> None:
    singular = "query" if field == "queries" else "url"
    arguments = {field: empty_batch, singular: fallback}
    with pytest.raises(_web.WebArgumentFormatError, match=f"{field} must contain"):
        _web.normalize_web_tool_arguments(tool_name, arguments)
    with patch.object(_web, "_run_search") as search, patch.object(_web, "_fetch_one") as fetch:
        result = tool(**arguments)
    assert result["error"] == "argument_format"
    assert "is empty" not in result["text"]
    search.assert_not_called()
    fetch.assert_not_called()


@pytest.mark.parametrize("tool_name, field, singular", [
    ("web_search", "queries", "query"), ("web_fetch", "urls", "url"),
])
@pytest.mark.parametrize("empty_batch", [[], "[]", None])
def test_empty_optional_plural_with_singular_preserves_fallback(tool_name: str, field: str,
                                                               singular: str, empty_batch) -> None:
    arguments = {field: empty_batch, singular: "valid target"}
    assert _web.normalize_web_tool_arguments(tool_name, arguments) == {field: [], singular: "valid target"}


def test_encoded_fetch_batch_uses_existing_store_path() -> None:
    stored = {"ok": True, "text": "stored"}
    with patch.object(_web, "_web_corpus_on", return_value=True), \
            patch.object(_web, "_fetch_into_corpus", return_value=stored) as corpus, \
            patch.object(_web, "_fetch_one") as fetch:
        result = _web.tool_web_fetch(urls='["https://example.org/a", "https://example.org/b"]', store=True)

    assert result == stored
    assert corpus.call_args.args == (["https://example.org/a", "https://example.org/b"],)
    fetch.assert_not_called()


def test_encoded_fetch_batch_respects_single_document_find_constraint() -> None:
    with patch.object(_web, "_fetch_one") as fetch:
        result = _web.tool_web_fetch(urls='["https://example.org/a"]', find="phrase")
    assert result["ok"] is False
    assert "find reads one document" in result["text"]
    fetch.assert_not_called()


def test_unrelated_tool_arguments_are_not_interpreted_as_web_batches() -> None:
    arguments = {"urls": "not web tool input"}
    normalized = _web.normalize_web_tool_arguments("run_python", arguments)

    assert normalized == arguments
    assert normalized is not arguments
