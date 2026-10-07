"""Unread Markdown targets cannot become ordinary Web answer evidence."""
import hashlib

import pytest

from _runtime_roles import runtime_text, user_texts
from app.application.code_agent.answer_acceptance import AcceptanceDecision, AnswerAcceptance
from app.application.code_agent.answer_contracts import web_source_citation_violations
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.web_evidence.receipts import format_source, make_source


READ_URL = "https://example.org/read"
UNREAD_URL = "https://example.org/discovered"
QUOTE = "3 октября в регионе ожидается сильный ветер."


def _evidence(*, tool="web_fetch", status="excerpt", presented=True, verified=True):
    evidence = RunEvidence()
    discovered = make_source(run_id="source-gate", tool="web_search", url=UNREAD_URL,
                             status="discovered", title="Найденная новость")
    evidence.record_tool_result(tool_name="web_search", arguments={"query": "новости"},
                                execution_status="ok", output={"ok": True, "sources": [discovered]},
                                text_result="", state_changed=False)
    source = make_source(run_id="source-gate", tool=tool, url=READ_URL, status=status,
                         quote=QUOTE if status == "excerpt" else "", content_hash="a" * 64,
                         offset=0, fetched_at=1791030000.0, quote_verified=verified)
    evidence.record_tool_result(tool_name=tool, arguments={"url": READ_URL},
                                execution_status="error" if status == "failed" else "ok",
                                output={"ok": status != "failed", "sources": [source]},
                                text_result="", state_changed=False)
    if presented:
        evidence.mark_sources_presented([{"role": "tool", "content": format_source(source)}])
    return evidence


def _evaluate(answer, evidence, *, owner=None, outcome=None):
    return (owner or AnswerAcceptance()).evaluate(
        final_text=answer, raw_user_message="Что по новостям ЧП?",
        pending_redirected_jobs=[], active_capability_groups=["web"],
        task_outcome=outcome or TaskOutcome(), run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=None, persistence_policy={},
        step=4, run_id="source-gate", criteria_rows=[],
    )


def test_unread_factual_citation_gets_one_correction_then_keeps_answer_without_unread_address():
    evidence, owner, outcome = _evidence(), AnswerAcceptance(), TaskOutcome()
    unsafe = f"Погибли 14 человек. [Источник]({UNREAD_URL})."
    first = _evaluate(unsafe, evidence, owner=owner, outcome=outcome)
    assert first.action == "retry"
    assert first.reason == "web_source"
    assert first.event["contract"] == "web_source_citation"
    assert first.messages == ({"role": "user", "content": first.correction, "_runtime_block": "answer_correction"},)
    assert not first.retain_rejected_answer
    assert unsafe not in str(first.messages)
    assert not outcome.answer_verification
    owner.commit(first)
    second = _evaluate(unsafe, evidence, owner=owner, outcome=outcome)
    assert second.action == "accept"
    assert second.answer_status == "degraded"
    # John 2026-10-06: the model's answer is kept; the unread page loses its address.
    assert second.text == "Погибли 14 человек. Источник (ссылка убрана: страница не прочитана)."
    assert UNREAD_URL not in second.text
    assert outcome.answer_verification["answer_sha256"] == hashlib.sha256(
        second.text.encode("utf-8")).hexdigest()


def test_corrected_read_citation_can_complete():
    evidence, owner, outcome = _evidence(), AnswerAcceptance(), TaskOutcome()
    first = _evaluate(f"В регионе ожидается ветер. [Источник]({UNREAD_URL}).",
                      evidence, owner=owner, outcome=outcome)
    owner.commit(first)
    corrected = f"{QUOTE} [Источник]({READ_URL})."
    result = _evaluate(corrected, evidence, owner=owner, outcome=outcome)
    assert result.action == "accept" and result.answer_status == "complete"
    assert result.text == corrected


def test_presented_browser_excerpt_is_read_evidence():
    answer = f"{QUOTE} [Источник]({READ_URL})."
    result = _evaluate(answer, _evidence(tool="browser"))
    assert result.action == "accept" and result.answer_status == "complete"


@pytest.mark.parametrize("kwargs", [
    {"status": "failed"}, {"status": "fetched"}, {"presented": False}, {"verified": False},
])
def test_successful_fetch_or_existing_receipt_alone_does_not_prove_reading(kwargs):
    result = _evaluate(f"{QUOTE} [Источник]({READ_URL}).", _evidence(**kwargs))
    assert result.action == "retry" and result.reason == "web_source"


@pytest.mark.parametrize("text", [
    "Найдена документация: [Источник]({url}). Содержимое страницы пока не проверено.",
    "Documentation: [Source]({url})",
    "Найдены ссылки для проверки: [Материал]({url}).",
    "Другой запрос нашёл документацию: [Источник]({url}).",
    "Another query found documentation: [Source]({url}).",
    "The changed query found links: [Source]({url}).",
])
def test_neutral_discovered_pointer_does_not_require_reading(text):
    answer = text.format(url=UNREAD_URL)
    result = _evaluate(answer, _evidence())
    assert result.action == "accept" and result.answer_status == "complete"
    assert result.text == answer


@pytest.mark.parametrize("text", [
    "Погибли 14 человек. [Источник]({url}). Содержимое страницы пока не проверено.",
    "Sources: [Source]({url}). Fourteen people died; page not read.",
    "Другой запрос нашёл документацию: [Источник]({url}). Погибли 14 человек.",
])
def test_disclaimer_or_discovery_prefix_does_not_exempt_factual_body(text):
    result = _evaluate(text.format(url=UNREAD_URL), _evidence())
    assert result.action == "retry" and result.reason == "web_source"


def test_unknown_pointer_is_not_a_discovery_receipt():
    result = _evaluate("Documentation: [Source](https://example.org/invented)", _evidence())
    assert result.action == "retry"
    assert "unknown_source" in result.correction


def test_balanced_url_parentheses_and_fragment_bind_but_query_must_match():
    url = "https://example.org/a(b)?edition=2"
    assert not web_source_citation_violations(
        f"Fact. [Source]({url}#section).", read_source_urls=[url], known_source_urls=[url])
    problem = web_source_citation_violations(
        "Fact. [Source](https://example.org/a(b)?edition=3).",
        read_source_urls=[url], known_source_urls=[url])
    assert problem[0].url.endswith("edition=3")


def test_adjacent_links_and_angle_target_with_title_are_individual_citations():
    problems = web_source_citation_violations(
        f'Fact. [Read]({READ_URL}) [Unread](<{UNREAD_URL}> "News title").',
        read_source_urls=[READ_URL], known_source_urls=[READ_URL, UNREAD_URL])
    assert len(problems) == 1 and problems[0].url == UNREAD_URL


def test_code_examples_are_not_web_citations():
    assert not web_source_citation_violations(
        f"Use `[Source]({UNREAD_URL})`.\n```markdown\n[Source]({UNREAD_URL})\n```",
        read_source_urls=[], known_source_urls=[UNREAD_URL])


def test_malformed_url_is_unknown_instead_of_crashing():
    problems = web_source_citation_violations(
        "Fact. [Source](https://[invalid/path).", read_source_urls=[], known_source_urls=[])
    assert problems[0].reason == "unknown_source"


def test_ordinary_work_correction_retains_draft_by_default():
    decision = AcceptanceDecision("retry", "Result needing a code fix", reason="outcome", correction="Fix the file")
    assert decision.messages == (
        {"role": "assistant", "content": decision.text, "_runtime_block": "rejected_answer"},
        {"role": "user", "content": decision.correction, "_runtime_block": "answer_correction"},
    )


def test_rejected_web_draft_does_not_reenter_model_context(tmp_path, monkeypatch):
    from copy import deepcopy
    from app.application.code_agent.agent_loop import stream_code_agent
    from app.application.code_agent.tools import _web
    from app.infrastructure.search.web_runtime import PageFetchResult

    monkeypatch.setattr(_web, "_fetch_one", lambda *args: PageFetchResult(
        text=QUOTE, final_url=READ_URL, status_code=200,
    ))
    unsafe = f"Погибли 14 человек. [Источник]({UNREAD_URL})."
    turns = []

    def chat(**kwargs):
        turns.append(deepcopy(kwargs["messages"]))
        assert len(turns) <= 3
        if len(turns) == 1:
            return {"message": {"tool_calls": [{"id": "read", "function": {
                "name": "web_fetch", "arguments": {"url": READ_URL},
            }}]}}
        if len(turns) == 2:
            return {"message": {"content": unsafe}}
        assert not any(row.get("role") == "assistant" and row.get("content") == unsafe
                       for row in kwargs["messages"])
        assert "Проверка прочитанных источников" in runtime_text(kwargs["messages"])
        assert not any("Проверка прочитанных источников" in text for text in user_texts(kwargs["messages"]))
        assert unsafe not in runtime_text(kwargs["messages"])
        assert any(row.get("role") == "tool" and QUOTE in row.get("content", "")
                   for row in kwargs["messages"])
        return {"message": {"content": f"{QUOTE} [Источник]({READ_URL})."}}

    events = list(stream_code_agent(user_message="Прочитай источник и сообщи предупреждение.",
        project_root=tmp_path, chat_fn=chat, permission_mode="bypass", auto_remember=False,
        num_ctx=65536, base_tools=["web_search", "web_fetch"]))
    final = next(row for row in events if row["type"] == "final_response")
    assert len(turns) == 3 and final["answer_status"] == "complete"
    assert "Погибли" not in final["text"] and "14" not in final["text"]
    assert len([row for row in events if row["type"] == "tool_call"]) == 1


def test_unread_links_keep_text_and_name_the_failed_read():
    from app.application.code_agent.answer_contracts import (
        WebSourceCitationViolation, mark_unread_web_links)

    answer = ("Таблица CVE с [nginx.org](https://nginx.org/en/security_advisories.html). "
              "Детали: [CVE-2026-1](https://my.f5.com/k1), [https://my.f5.com/k2](https://my.f5.com/k2), "
              "[ветка](https://www.reddit.com/r/x).")
    violations = [WebSourceCitationViolation(1, "https://my.f5.com/k1", "unknown_source"),
                  WebSourceCitationViolation(1, "https://my.f5.com/k2", "unknown_source"),
                  WebSourceCitationViolation(1, "https://www.reddit.com/r/x", "unread_source")]
    marked = mark_unread_web_links(answer, violations, failed_errors={
        "https://www.reddit.com/r/x": "ERROR: не открылась: проверка от ботов (https://www.reddit.com/r/x) — возьми другой"})
    assert "[nginx.org](https://nginx.org/en/security_advisories.html)" in marked  # read link untouched
    assert "CVE-2026-1 (ссылка убрана: страница не прочитана)" in marked
    assert "my.f5.com (ссылка убрана: страница не прочитана)" in marked and "my.f5.com/k2" not in marked
    assert "ветка (ссылка убрана: не открылась — проверка от ботов)" in marked
    assert "reddit.com/r/x" not in marked and "f5.com/k1" not in marked
