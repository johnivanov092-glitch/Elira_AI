"""Explicit quote limits at the accepted-answer boundary, using the audit answer."""

from types import SimpleNamespace

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.answer_contracts import (
    infer_quote_word_limit,
    quote_word_limit_correction,
    quote_word_limit_violations,
)
from app.application.code_agent.delivery_session import build_continuation_kwargs


SQLITE_REQUEST = (
    "Проверь официальную документацию SQLite: внешние ключи включены по умолчанию "
    "или их нужно включать для каждого соединения? "
    "Дай короткую цитату до 15 слов и ссылку."
)
AUDIT_ANSWER = (
    "**Цитата (14 слов):**\n"
    '> "Section 2 describes the steps an application must take in order to enable '
    'foreign key constraints in SQLite (it is disabled by default)."\n\n'
    "[SQLite Foreign Key Support](https://sqlite.org/foreignkeys.html)"
)
CORRECTED_ANSWER = (
    "> Foreign key constraints are disabled by default (for backwards compatibility).\n\n"
    "[SQLite Foreign Key Support](https://sqlite.org/foreignkeys.html)"
)


@pytest.fixture
def sqlite_source(monkeypatch):
    """Exercise quote correction after a real tool read of a controlled source."""
    requested = []
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)

    def get(url, **kwargs):
        requested.append(url)
        return SimpleNamespace(
            status_code=200, url=url, text=AUDIT_ANSWER + "\n" + CORRECTED_ANSWER,
            encoding="utf-8", headers={"Content-Type": "text/plain"}, close=lambda: None,
        )

    monkeypatch.setattr("requests.get", get)
    return requested


def _read_sqlite():
    return {"message": {"tool_calls": [{"function": {
        "name": "web_fetch", "arguments": {"url": "https://sqlite.org/foreignkeys.html"},
    }}]}}


def test_audit_quote_count_is_measured_instead_of_trusting_its_label():
    limit = infer_quote_word_limit(SQLITE_REQUEST)
    assert limit == 15
    violations = quote_word_limit_violations(AUDIT_ANSWER, max_words=limit)
    assert len(violations) == 1
    assert violations[0].word_count == 23
    assert violations[0].claimed_words == 14
    assert "23" in quote_word_limit_correction(violations)
    assert "15" in quote_word_limit_correction(violations)
    assert quote_word_limit_violations(CORRECTED_ANSWER, max_words=limit) == ()


def test_examples_and_ambiguous_or_negated_limits_do_not_become_contracts():
    assert infer_quote_word_limit('Что означает "цитату до 15 слов"?') is None
    assert infer_quote_word_limit("Пример: `цитату до 15 слов`.") is None
    assert infer_quote_word_limit("Объясни правило:\n> цитата до 15 слов") is None
    assert infer_quote_word_limit("Не давай цитату до 15 слов, просто объясни правило.") is None
    assert infer_quote_word_limit("Дай цитату до 15 слов и цитату до 20 слов.") is None
    assert infer_quote_word_limit("Слово «цитата» есть в тексте; ответ до 15 слов.") is None


def test_quote_words_keep_inline_code_and_ignore_source_metadata():
    text = '> Use `PRAGMA foreign_keys` for every connection. [[source:abc]]'
    violations = quote_word_limit_violations(text, max_words=5)
    assert len(violations) == 1
    assert violations[0].word_count == 6
    assert quote_word_limit_violations("```markdown\n" + text + "\n```", max_words=5) == ()
    assert quote_word_limit_violations('Пример кода: `echo "one two three four"`.', max_words=2) == ()
    paired = '«Enable `PRAGMA foreign_keys` for every SQLite connection.»'
    violations = quote_word_limit_violations(paired, max_words=6)
    assert len(violations) == 1
    assert violations[0].word_count == 7


def test_count_claim_is_checked_even_when_quote_is_within_limit():
    text = '**Цитата (14 слов):** «Foreign key constraints are disabled by default.»'
    violations = quote_word_limit_violations(text, max_words=15)
    assert len(violations) == 1
    assert violations[0].word_count == 7
    assert violations[0].claimed_words == 14
    assert quote_word_limit_violations(text, max_words=None) == ()


def test_count_normalization_changes_only_recognized_label_digits():
    from app.application.code_agent.answer_contracts import normalize_quote_word_counts

    text = (
        "11 версий.\n\n**Цитата (11 слов):**\n"
        "> «Foreign key constraints are disabled by default (for backwards "
        "compatibility), so must be enabled»\n\n"
        "[SQLite](https://sqlite.org/foreignkeys.html#fk_enable)\n\n"
        "```markdown\nЦитата (11 слов):\n> Two words.\n```"
    )
    normalized = normalize_quote_word_counts(text)
    assert normalized == text.replace("**Цитата (11 слов):**", "**Цитата (14 слов):**", 1)
    assert quote_word_limit_violations(normalized, max_words=15) == ()
    assert normalize_quote_word_counts(normalized) == normalized
    overlong = normalize_quote_word_counts(AUDIT_ANSWER)
    assert overlong == AUDIT_ANSWER.replace("(14 слов)", "(23 слов)", 1)
    violations = quote_word_limit_violations(overlong, max_words=15)
    assert len(violations) == 1
    assert violations[0].word_count == 23


def test_overlong_audit_quote_is_corrected_before_answer_acceptance(tmp_path, monkeypatch, sqlite_source):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    calls = []

    def chat(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return _read_sqlite()
        return {"message": {
            "content": AUDIT_ANSWER if len(calls) == 2 else "**Цитата (4 слов):**\n" + CORRECTED_ANSWER,
            "tool_calls": [],
        }}

    events = list(stream_code_agent(
        user_message=SQLITE_REQUEST, project_root=tmp_path,
        model="test-model", chat_fn=chat, auto_remember=False,
    ))
    finals = [event for event in events if event["type"] == "final_response"]
    assert sqlite_source == ["https://sqlite.org/foreignkeys.html"]
    assert len(calls) == 3
    assert len([event for event in events if event["type"] == "answer_format_correction"]) == 1
    assert len(finals) == 1
    assert finals[0]["text"] == "**Цитата (10 слов):**\n" + CORRECTED_ANSWER
    assert finals[0]["answer_status"] == "complete"


@pytest.mark.parametrize("memory_query", [None, SQLITE_REQUEST])
def test_persistent_quote_failure_stays_degraded_after_resume_without_retry(
    tmp_path, monkeypatch, memory_query, sqlite_source,
):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    calls = 0

    def chat(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _read_sqlite()
        return {"message": {"content": AUDIT_ANSWER, "tool_calls": []}}

    events = list(stream_code_agent(
        user_message=SQLITE_REQUEST, memory_query=memory_query,
        project_root=tmp_path, run_id="quote-limit-persistent",
        model="test-model", chat_fn=chat, auto_remember=False,
    ))
    assert sqlite_source == ["https://sqlite.org/foreignkeys.html"]
    assert calls == 3
    assert len([event for event in events if event["type"] == "answer_format_correction"]) == 1
    final = next(event for event in events if event["type"] == "final_response")
    assert final["answer_status"] == "degraded"
    assert "Section 2 describes" not in final["text"]

    resumed = list(stream_code_agent(**build_continuation_kwargs(
        "quote-limit-persistent", chat_fn=chat,
    )))
    assert calls == 4
    assert sqlite_source == ["https://sqlite.org/foreignkeys.html"]
    assert not any(event["type"] == "answer_format_correction" for event in resumed)
    resumed_final = next(event for event in resumed if event["type"] == "final_response")
    assert resumed_final["answer_status"] == "degraded"
    assert "Section 2 describes" not in resumed_final["text"]
