from copy import deepcopy

import pytest

from app.application.code_agent.task_outcomes import _answer_checks, task_decide


URL = "https://docs.searxng.org/admin/settings/index.html"


def _captured_invalid_decision():
    # The five requirement/check shapes come from the first task_decide in
    # qwen-smoke-592ba176b8af49bab71440d9bc91cecb. Only unrelated prose is reduced.
    checks = [
        [{"kind": "web_search", "query": "SearXNG keep_only engines documentation", "url": URL}],
        [{"kind": "source_read", "url": URL, "contains": "keep_only"}],
        [{"kind": "answer_format", "language": "ru", "contains": ["keep_only", "docs.searxng.org", "[[source:"],
          "max_chars": 1200, "markdown_url": True}],
        [{"kind": "cited_quote", "url": URL, "count": 1, "max_words": 20,
          "text": "The engine list can be restricted with the `keep_only` setting."}],
        [{"kind": "tool_policy", "allowed": ["web_search", "web_fetch", "runtime_control:task_decide"],
          "max_search_queries": 1, "max_read_urls": 1}, {"kind": "no_persistence"}],
    ]
    return {
        "disposition": "one_off", "reason": "Read-only web answer", "inputs": [], "targets": [],
        "delivery": {"mode": "none", "targets": []},
        "requirements": [{"id": str(index), "text": "Explicit requirement", "mandatory": True,
                          "verification": {"checks": items}} for index, items in enumerate(checks)],
    }


def test_captured_invalid_decision_reports_both_requirement_check_errors(tmp_path):
    with pytest.raises(ValueError) as failure:
        task_decide(tmp_path, _captured_invalid_decision())
    message = str(failure.value)
    assert "config.requirements[2].verification.checks[0]" in message
    assert "answer_format" in message and "markdown_url" in message and "HTTP(S) URL string" in message
    assert "config.requirements[3].verification.checks[0]" in message
    assert "cited_quote" in message and "unknown fields: text" in message
    assert "required fields: count, kind, max_words, url" in message
    assert "The engine list" not in message


def test_corrected_capture_returns_identical_checks_without_coercion(tmp_path):
    config = _captured_invalid_decision()
    config["requirements"][2]["verification"]["checks"][0]["markdown_url"] = URL
    del config["requirements"][3]["verification"]["checks"][0]["text"]
    original = deepcopy(config)
    result = task_decide(tmp_path, config)
    assert result["task_decision"]["requirements"] == config["requirements"]
    assert config == original


def test_all_check_errors_include_missing_unknown_and_expected_field_types():
    value = {"checks": [
        {"kind": "cited_quote", "count": True, "text": "PRIVATE_QUOTE_VALUE"},
        {"kind": "answer_format", "language": ["PRIVATE_LANGUAGE_VALUE"], "contains": [True],
         "max_chars": False, "markdown_url": "PRIVATE_URL_VALUE"},
    ]}
    with pytest.raises(ValueError) as failure:
        _answer_checks(value, path="config.requirements[7].verification")
    message = str(failure.value)
    assert "config.requirements[7].verification.checks[0]" in message
    assert "missing fields: max_words, url" in message
    assert "unknown fields: text" in message
    assert "required fields: count, kind, max_words, url" in message
    assert "count=integer 1..100000" in message
    assert ".count must be an integer" in message
    assert "config.requirements[7].verification.checks[1]" in message
    for field in ("language", "contains", "max_chars", "markdown_url"):
        assert f".{field} must be" in message
    assert "PRIVATE_" not in message


@pytest.mark.parametrize("value", [
    None,
    {"checks": []},
    {"checks": [{}]},
    {"checks": [{"kind": "PRIVATE_UNKNOWN_KIND"}]},
    {"checks": [{"kind": ["PRIVATE_UNKNOWN_KIND"]}]},
    {"checks": ["PRIVATE_CHECK_VALUE"]},
    {"checks": [{"kind": "no_persistence"}] * 21},
    {"checks": [{"kind": "no_persistence"}], "unknown": "PRIVATE_ROOT_VALUE"},
])
def test_shape_and_kind_errors_are_indexed_without_echoing_values(value):
    with pytest.raises(ValueError) as failure:
        _answer_checks(value, path="config.requirements[4].verification")
    message = str(failure.value)
    assert "config.requirements[4].verification" in message
    assert "PRIVATE_" not in message


def test_invalid_url_parser_failure_has_schema_path_and_expected_type():
    with pytest.raises(ValueError) as failure:
        _answer_checks({"checks": [{"kind": "source_read", "url": "https://[PRIVATE_HOST"}]})
    message = str(failure.value)
    assert "verification.checks[0] (kind=source_read).url" in message
    assert "HTTP(S) URL string" in message
    assert "PRIVATE_HOST" not in message
