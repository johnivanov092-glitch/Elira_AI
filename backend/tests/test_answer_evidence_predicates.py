from copy import deepcopy

import pytest

from app.application.code_agent.task_outcomes import _answer_checks
from test_readonly_search_answer_replay import URL, VERIFICATION, _saved_declaration, _saved_web_flow


def _checked(tmp_path, alter=lambda value: value, *, extra_fetch=False):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    if extra_fetch:
        evidence.record_tool_result(
            tool_name="web_fetch", arguments={"url": URL}, execution_status="error",
            output={"ok": False}, text_result="timeout", state_changed=False,
        )
    receipt = outcome.verify_answer(alter(answer), evidence, 0,
                                    persistence_policy={"rag": False, "direct_memory": False, "learning": False})
    return {row["requirement_id"]: row["passed"] for row in receipt["checks"]}


def test_failed_repeat_read_counts_toward_declared_limit(tmp_path):
    assert _checked(tmp_path)["limits"] is True
    assert _checked(tmp_path, extra_fetch=True)["limits"] is False


def test_answer_keywords_only_in_fenced_code_do_not_meet_visible_format(tmp_path):
    def hide_keywords(answer):
        _, remainder = answer.split("\n", 1)
        return ("Настройка ограничивает набор поисковых систем.\n" + remainder
                + "\n```\nkeep_only оставляет перечисленные\n```")

    assert _checked(tmp_path, hide_keywords)["answer"] is False


def test_inline_code_setting_name_remains_visible_answer_content(tmp_path):
    assert _checked(tmp_path, lambda answer: answer.replace("keep_only", "`keep_only`"))["answer"] is True


def test_malformed_candidate_markdown_url_fails_without_parser_exception(tmp_path):
    assert _checked(tmp_path, lambda answer: answer.replace(f"({URL})", "(https://[invalid#x)"))["answer"] is False


@pytest.mark.parametrize("closing", ["\n```", ""])
def test_only_markdown_link_inside_closed_or_unclosed_code_fence_is_not_delivery(tmp_path, closing):
    def hide_link(answer):
        start = answer.index("[settings.yml")
        end = answer.index(") ", start) + 1
        return answer[:start] + answer[end:] + f"\n```\n[Documentation]({URL})" + closing

    assert _checked(tmp_path, hide_link)["answer"] is False


@pytest.mark.parametrize("kind,field,value", [
    ("answer", "language", ["ru"]),
    ("answer", "language", True),
    ("answer", "max_chars", True),
    ("limits", "max_search_queries", False),
    ("limits", "max_read_urls", -1),
    ("search", "url", "ftp://example.org/settings"),
    ("search", "url", "https://[invalid"),
    ("search", "query", []),
    ("answer", "contains", [True]),
    ("limits", "allowed", "web_search"),
])
def test_answer_check_schema_rejects_invalid_types_and_urls(kind, field, value):
    declaration = deepcopy(VERIFICATION[kind])
    declaration["checks"][0][field] = value
    with pytest.raises(ValueError):
        _answer_checks(declaration)


@pytest.mark.parametrize("value", [
    {"checks": [{"kind": "semantic_truth", "passed": True}]},
    {"checks": [{"kind": ["source_read"], "url": URL}]},
    {"checks": []},
    {"checks": {}, "passed": True},
    {"checks": [{"kind": "source_read", "url": URL, "unexpected": True}]},
])
def test_answer_check_schema_rejects_unknown_and_malformed_checks(value):
    with pytest.raises(ValueError):
        _answer_checks(value)
