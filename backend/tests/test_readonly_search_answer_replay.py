"""Replay the recovered declaration from the failed keep_only Qwen run.

The captured run is retained under .scratch/web-batch-30/qwen-smoke-cd55b8... .
These reduced snapshots are portable: no model, network, shell or journal I/O.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.answer_contracts import infer_quote_word_limit
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.task_outcomes import TaskOutcome
from app.application.web_evidence.receipts import format_source, make_source


RUN_ID = "web-qwen-cd55b8ced4a74129bcd66d8b70963f7c"
URL = "https://docs.searxng.org/admin/settings/settings"
QUOTE = "As an alternative, it is possible to specify the engines to keep."
GOAL = (
    "Найди через веб-поиск официальную документацию SearXNG о параметре keep_only "
    "для выбора поисковых движков. Открой найденную страницу на docs.searxng.org "
    "и прочитай соответствующий раздел. Ответь по-русски кратко: что делает "
    "keep_only; приведи одну дословную английскую цитату-предложение длиной "
    "не более 20 слов и Markdown-ссылку на прочитанную официальную страницу. "
    "Укажи штатную source-метку прочитанного отрывка. Достаточно одного "
    "поискового запроса и чтения одной страницы; если источник недоступен, "
    "явно сообщи об этом. Ответ нужен прямо в чате. Файлы не создавай и не меняй, "
    "команды не запускай, ничего не запоминай и не используй для обучения."
)
REQUIREMENTS = [
    {"id": "search", "text": "Найди официальную документацию SearXNG о keep_only через веб-поиск.", "mandatory": True},
    {"id": "read", "text": "Открой и прочитай соответствующий раздел на docs.searxng.org.", "mandatory": True},
    {"id": "answer", "text": "Ответь по-русски кратко: что делает keep_only; одна дословная английская цитата-предложение не более 20 слов; Markdown-ссылка на прочитанную официальную страницу; штатная source-метка отрывка.", "mandatory": True},
    {"id": "limits", "text": "Используй один поисковый запрос и чтение одной страницы; если источник недоступен, явно сообщи; ответ в чате; не создавай/не меняй файлы, не запускай команды, не запоминай и не используй для обучения.", "mandatory": True},
]
NO_PERSISTENCE = {"rag": False, "direct_memory": False, "learning": False}


def _saved_web_flow(evidence=None):
    if evidence is None:
        evidence = RunEvidence()
    discovered = make_source(run_id=RUN_ID, tool="web_search", url=URL,
                             status="discovered", title="settings.yml — SearXNG Documentation")
    evidence.record_tool_result(
        tool_name="web_search", arguments={"query": "SearXNG keep_only engines documentation site:docs.searxng.org"},
        execution_status="ok", output={"ok": True, "sources": [discovered]},
        text_result="Found official settings.yml documentation", state_changed=False,
    )
    sources = [make_source(
        run_id=RUN_ID, tool="web_fetch", url=URL, status="excerpt",
        quote=text, content_hash="0957480b7f60e06510d97b0757cd777f7bb46a8f1f1bdca59698a46c14b7c763",
        offset=offset, fetched_at=1790862001.5074444, quote_verified=True,
    ) for offset, text in [(0, "settings.yml\n¶\nThis page describe the options possibilities of the"), (1500, QUOTE)]]
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"url": URL, "max_chars": 30000},
        execution_status="ok", output={"ok": True, "sources": sources},
        text_result="\n\n".join(format_source(source) for source in sources), state_changed=False,
    )
    evidence.mark_sources_presented([{
        "role": "tool", "content": "\n\n".join(format_source(source) for source in sources),
    }])
    answer = (
        "keep_only оставляет только перечисленные поисковые движки.\n"
        f'Цитата: “{QUOTE}”\n'
        f"[settings.yml — SearXNG Documentation]({URL}) [[source:{sources[1]['id']}]]"
    )
    return evidence, answer


def test_saved_request_establishes_twenty_word_quote_limit():
    assert infer_quote_word_limit(GOAL) == 20


@pytest.mark.parametrize("message", [
    "Пример в коде: `приведи цитату-предложение длиной не более 20 слов`.",
    "Пример кода:\n```text\nприведи цитату-предложение длиной не более 20 слов\n```",
    'Пример запроса: "приведи цитату-предложение длиной не более 20 слов".',
    "Пример:\n> приведи цитату-предложение длиной не более 20 слов\n",
    "Не приводи цитату-предложение длиной не более 20 слов; просто объясни смысл.",
])
def test_quote_sentence_examples_and_negated_directive_do_not_establish_limit(message):
    assert infer_quote_word_limit(message) is None


def test_quote_source_correction_survives_journal_reload_and_does_not_retry(tmp_path):
    journal = RunJournal("resume-quote-source", runs_root=tmp_path / "runs")
    journal.start({"project_root": str(tmp_path / "project"), "user_message": GOAL, "auto_remember": False}, {})
    try:
        journal.append_event({"type": "answer_format_correction", "step": 5, "contract": "quote_source"})
    finally:
        journal.finish(interrupted=True)
    restored = RunJournal.load("resume-quote-source", runs_root=tmp_path / "runs")
    assert restored.state["quote_source_correction_sent"] is True
    evidence, answer = _saved_web_flow()
    excerpts = {source["offset"]: source for source in evidence.sources if source["tool"] == "web_fetch"}
    answer = answer.replace(excerpts[1500]["id"], excerpts[0]["id"])
    acceptance = AnswerAcceptance(quote_source_correction_sent=bool(restored.state.get("quote_source_correction_sent")))
    decision = acceptance.evaluate(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=TaskOutcome(), run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=20, criteria_rows=[], step=8, run_id=RUN_ID,
        persistence_policy=NO_PERSISTENCE,
    )
    assert decision.action == "accept"
    assert decision.answer_status == "degraded"
    assert QUOTE not in decision.text


def test_saved_wrong_chunk_gets_one_correction_then_plain_degraded_answer():
    evidence, answer = _saved_web_flow()
    excerpts = {source["offset"]: source for source in evidence.sources if source["tool"] == "web_fetch"}
    wrong, right = excerpts[0]["id"], excerpts[1500]["id"]
    answer = answer.replace(right, wrong)
    acceptance = AnswerAcceptance()
    kwargs = dict(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=TaskOutcome(), run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=20, criteria_rows=[], step=5, run_id=RUN_ID,
        persistence_policy=NO_PERSISTENCE,
    )
    first = acceptance.evaluate(**kwargs)
    assert first.action == "retry"
    assert first.reason == "quote_source"
    assert wrong in first.correction
    acceptance.commit(first)
    second = acceptance.evaluate(**{**kwargs, "step": 8})
    assert second.action == "accept"
    assert second.answer_status == "degraded"
    assert right not in second.text
    assert QUOTE not in second.text
    citation = evidence.citations(answer)[0]
    assert citation["source_id"] == wrong
    assert citation["status"] == "unresolved"


def test_plain_sourced_paraphrase_remains_complete_without_artifact_contract():
    evidence, answer = _saved_web_flow()
    source = next(source for source in evidence.sources if source.get("offset") == 1500)
    answer = (
        "keep_only оставляет только перечисленные поисковые движки. "
        f"[Официальная документация]({URL}) [[source:{source['id']}]]"
    )
    decision = AnswerAcceptance().evaluate(
        final_text=answer, raw_user_message="Объясни по официальной документации, что делает keep_only.",
        pending_redirected_jobs=[], active_capability_groups=["web", "runtime"],
        task_outcome=TaskOutcome(), run_evidence=evidence, code_input_epoch=0,
        quote_word_limit=None, criteria_rows=[], step=5, run_id=RUN_ID,
    )
    assert decision.action == "accept"
    assert decision.answer_status == "complete"
    assert decision.text == answer
    assert evidence.citations(answer)[0]["status"] == "matched"


def test_failed_quote_after_correction_keeps_the_rest_of_the_answer():
    from app.application.code_agent.run_evidence import UNVERIFIED_QUOTE_NOTE

    evidence, answer = _saved_web_flow()
    excerpts = {source["offset"]: source for source in evidence.sources if source["tool"] == "web_fetch"}
    answer = answer.replace(excerpts[1500]["id"], excerpts[0]["id"])
    acceptance = AnswerAcceptance(quote_source_correction_sent=True)
    decision = acceptance.evaluate(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=TaskOutcome(), run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=20, criteria_rows=[], step=8, run_id=RUN_ID,
        persistence_policy=NO_PERSISTENCE,
    )
    assert decision.action == "accept"
    assert decision.answer_status == "degraded"
    assert QUOTE not in decision.text
    assert decision.text.startswith("keep_only оставляет только перечисленные поисковые движки.")
    assert decision.text.endswith(UNVERIFIED_QUOTE_NOTE)
    assert "Ответ не прошёл проверку источника цитаты" not in decision.text
