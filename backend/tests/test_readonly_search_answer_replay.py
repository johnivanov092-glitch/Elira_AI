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
from app.application.code_agent.task_outcomes import TaskOutcome, task_decide
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
VERIFICATION = {
    "search": {"checks": [{
        "kind": "web_search", "query": "SearXNG keep_only engines documentation site:docs.searxng.org", "url": URL,
    }]},
    "read": {"checks": [{"kind": "source_read", "url": URL, "contains": QUOTE}]},
    "answer": {"checks": [
        {"kind": "answer_format", "contains": ["keep_only", "оставляет", "перечисленные"],
         "max_chars": 1000, "language": "ru", "markdown_url": URL},
        {"kind": "cited_quote", "url": URL, "count": 1, "max_words": 20},
    ]},
    "limits": {"checks": [{
        "kind": "tool_policy", "allowed": ["web_search", "web_fetch", "runtime_control:task_decide"],
        "max_search_queries": 1, "max_read_urls": 1,
        "user_quote": "Достаточно одного поискового запроса и чтения одной страницы",
    }, {"kind": "no_persistence", "user_quote": "ничего не запоминай и не используй для обучения"}]},
}


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


def _saved_declaration(root, *, typed=False, evidence=None, targets=None, untyped_requirement=None):
    outcome = TaskOutcome()
    outcome.set_contract(GOAL, [])
    config = {
        "disposition": "one_off", "reason": "Разовый поиск и чтение одной официальной страницы документации.",
        "inputs": [], "targets": targets or [], "requirements": deepcopy(REQUIREMENTS),
        "delivery": {"mode": "none", "targets": []},
    }
    if typed:
        for requirement in config["requirements"]:
            if requirement["id"] != untyped_requirement:
                requirement["verification"] = deepcopy(VERIFICATION[requirement["id"]])
    invalid = deepcopy(config)
    invalid["delivery"]["reason"] = "Запрошен ответ прямо в чате"
    with pytest.raises(ValueError, match="config.delivery"):
        task_decide(root, invalid)
    if evidence is not None:
        evidence.record_tool_result(
            tool_name="runtime_control", arguments={"operation": "task_decide", "config": invalid},
            execution_status="error", output={"ok": False, "operation": "task_decide"},
            text_result="config.delivery needs mode none|chat_download and targets", state_changed=False,
        )
    result = task_decide(root, config)
    output = {"ok": True, "operation": "task_decide", "result": result}
    outcome.observe("runtime_control", {"operation": "task_decide"}, output,
                    project_root=root, input_epoch=0, execution_status="ok")
    if evidence is not None:
        evidence.record_tool_result(
            tool_name="runtime_control", arguments={"operation": "task_decide", "config": config},
            execution_status="ok", output=output, text_result="Task decision recorded", state_changed=False,
        )
    return outcome


def test_saved_search_fetch_declaration_does_not_require_checker_files(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    acceptance = AnswerAcceptance()
    kwargs = dict(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=outcome, run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=infer_quote_word_limit(GOAL), criteria_rows=[],
        persistence_policy=NO_PERSISTENCE, step=5, run_id=RUN_ID,
    )
    decision = acceptance.evaluate(**kwargs)
    if decision.action == "retry":
        if decision.outcome_correction is not None:
            outcome.correction = decision.outcome_correction
        acceptance.commit(decision)
        decision = acceptance.evaluate(**{**kwargs, "step": 8})
    assert not list(tmp_path.iterdir())
    assert outcome.verifications == []
    assert decision.action == "accept"
    assert decision.answer_status == "complete", decision.text
    assert QUOTE in decision.text


def test_original_untyped_saved_declaration_is_not_implicitly_confirmed(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, evidence=evidence)
    acceptance = AnswerAcceptance()
    kwargs = dict(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=outcome, run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=None, criteria_rows=[], step=5, run_id=RUN_ID,
    )
    first = acceptance.evaluate(**kwargs)
    assert first.action == "retry" and first.reason == "outcome"
    outcome.correction = first.outcome_correction
    acceptance.commit(first)
    second = acceptance.evaluate(**{**kwargs, "step": 8})
    assert second.answer_status == "degraded"
    assert all(row["status"] == "unconfirmed" for row in outcome.missing_requirements(0))
    assert outcome.verifications == []
    assert outcome.learning_evidence(0) is None


def test_typed_answer_checks_do_not_replace_a_declared_file_verifier(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence, targets=["result.json"])
    acceptance = AnswerAcceptance()
    kwargs = dict(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=outcome, run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=None, criteria_rows=[], step=5, run_id=RUN_ID,
    )
    first = acceptance.evaluate(**kwargs)
    assert first.action == "retry"
    outcome.correction = first.outcome_correction
    acceptance.commit(first)
    second = acceptance.evaluate(**{**kwargs, "step": 8})
    assert second.answer_status == "degraded"
    assert outcome.verifications == []
    assert outcome.learning_evidence(0) is None


def test_answer_evidence_is_bound_to_candidate_and_current_contract(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    receipt = outcome.verify_answer(answer, evidence, 0, persistence_policy=NO_PERSISTENCE)
    assert receipt["status"] == "passed"
    assert outcome.missing_requirements(0, answer_verification=receipt, answer=answer) == []
    assert {row["id"] for row in outcome.missing_requirements(0)} == {row["id"] for row in REQUIREMENTS}
    assert outcome.missing_requirements(0, answer_verification=receipt, answer=answer + "\nДругая версия ответа")
    assert outcome.missing_requirements(1, answer_verification=receipt, answer=answer)
    outcome.apply_user_clarification("Добавь, что список задаётся в use_default_settings.engines.")
    assert outcome.missing_requirements(0, answer_verification=receipt, answer=answer)
    assert outcome.verifications == []
    assert outcome.learning_evidence(0) is None
    restored = TaskOutcome(outcome.snapshot())
    assert restored.missing_requirements(0, answer_verification=receipt, answer=answer)


def test_clearing_original_targets_does_not_unlock_readonly_answer_evidence(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence, targets=["result.json"])
    config = deepcopy(outcome.decision)
    config["targets"] = []
    result = task_decide(tmp_path, config)
    outcome.observe("runtime_control", {"operation": "task_decide"},
                    {"ok": True, "operation": "task_decide", "result": result},
                    project_root=tmp_path, input_epoch=0, execution_status="ok")
    assert outcome.decision["targets"] == []
    assert outcome.verify_answer(answer, evidence, 0, persistence_policy=NO_PERSISTENCE) == {}
    assert outcome.missing_requirements(0, answer_verification={}, answer=answer)
    assert outcome.learning_evidence(0) is None


def test_partial_typed_coverage_keeps_original_mandatory_gap(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence, untyped_requirement="limits")
    receipt = outcome.verify_answer(answer, evidence, 0, persistence_policy=NO_PERSISTENCE)
    missing = outcome.missing_requirements(0, answer_verification=receipt, answer=answer)
    assert [row["id"] for row in missing] == ["limits"]
    assert missing[0]["status"] == "unconfirmed"
    assert outcome.learning_evidence(0) is None


def test_resumed_run_without_imported_sources_cannot_certify_complete_tool_history(tmp_path):
    observations = RunObservations(task_spec=None, durable_state={}, resume=True, initial_sources=[])
    evidence, answer = _saved_web_flow(observations.evidence)
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    assert evidence.tool_operations
    assert evidence.web_operations
    assert evidence.operations_complete is False
    assert outcome.verify_answer(answer, evidence, 0, persistence_policy=NO_PERSISTENCE) == {}
    assert {row["id"] for row in outcome.missing_requirements(0)} == {row["id"] for row in REQUIREMENTS}


@pytest.mark.parametrize("tool,arguments", [
    ("web_search", {"query": "second failed search query"}),
    ("web_fetch", {"url": "https://docs.searxng.org/other-page"}),
])
def test_failed_web_attempts_still_count_against_declared_policy(tmp_path, tool, arguments):
    evidence, answer = _saved_web_flow()
    evidence.record_tool_result(
        tool_name=tool, arguments=arguments, execution_status="error",
        output={"ok": False}, text_result="Provider unavailable", state_changed=False,
    )
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    receipt = outcome.verify_answer(answer, evidence, 0, persistence_policy=NO_PERSISTENCE)
    missing = outcome.missing_requirements(0, answer_verification=receipt, answer=answer)
    assert [row["id"] for row in missing] == ["limits"]
    assert missing[0]["status"] == "unconfirmed"


@pytest.mark.parametrize("policy", [
    None,
    {"rag": False, "learning": False},
    {**NO_PERSISTENCE, "rag": True},
    {**NO_PERSISTENCE, "direct_memory": True},
    {**NO_PERSISTENCE, "learning": True},
])
def test_no_persistence_requires_all_trusted_runtime_channels_disabled(tmp_path, policy):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    receipt = outcome.verify_answer(answer, evidence, 0, persistence_policy=policy)
    missing = outcome.missing_requirements(0, answer_verification=receipt, answer=answer)
    assert [row["id"] for row in missing] == ["limits"]
    assert receipt["status"] == "unconfirmed"
    first = AnswerAcceptance().evaluate(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=outcome, run_evidence=evidence,
        code_input_epoch=0, quote_word_limit=20, criteria_rows=[], persistence_policy=policy,
        step=5, run_id=RUN_ID,
    )
    assert first.action == "retry" and first.reason == "outcome"
    assert "limits" in first.correction
    assert outcome.verifications == []
    assert outcome.learning_evidence(0) is None


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


@pytest.mark.parametrize("verification", [
    {"checks": []},
    {"checks": [{"kind": "semantic_truth", "passed": True}]},
    {"checks": [{"kind": "cited_quote", "url": URL, "count": True, "max_words": 20}]},
    {"checks": [{"kind": "tool_policy", "allowed": ["web_search"], "max_search_queries": True}]},
    {"checks": [{"kind": "tool_policy", "allowed": ["web_search"], "max_search_queries": -1}]},
    {"checks": [{"kind": "web_search", "url": URL}]},
    {"checks": [{"kind": "source_read", "url": URL, "contains": []}]},
    {"checks": [{"kind": "source_read", "url": URL, "passed": True}]},
    {"checks": [{"kind": "source_read", "url": URL}], "passed": True},
])
def test_invalid_typed_checks_cannot_declare_coverage(tmp_path, verification):
    with pytest.raises(ValueError):
        task_decide(tmp_path, {
            "disposition": "one_off", "reason": "Явная проверка ответа", "targets": [], "inputs": [],
            "requirements": [{"id": "answer", "text": "Проверенный ответ", "verification": verification}],
            "delivery": {"mode": "none", "targets": []},
        })


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
