"""User constraints stay binding without any model declaration (John 2026-10-07).

A format condition the user wrote (required phrase, length) gets one rewrite; a spent
search budget cannot be undone and only marks the answer partial. The model's answer
text is never replaced.
"""
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from webskill.application.code_agent.answer_contracts import explicit_web_answer_constraints
from app.application.code_agent.task_outcomes import TaskOutcome
import pytest
from test_readonly_search_answer_replay import _saved_web_flow

NO_PERSISTENCE = {"rag": False, "direct_memory": False, "learning": False}


def _evaluate(acceptance, outcome, evidence, answer, request, step=4):
    return acceptance.evaluate(final_text=answer, raw_user_message=request,
        pending_redirected_jobs=[], active_capability_groups=["web"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None, step=step, run_id="constraints",
        persistence_policy=NO_PERSISTENCE, criteria_rows=[])


def test_spent_search_budget_marks_the_answer_partial_without_a_retry():
    evidence, answer = _saved_web_flow()
    evidence.record_tool_result(tool_name="web_search", arguments={"query": "second"},
        execution_status="ok", output={"ok": True}, text_result="No results", state_changed=False)
    outcome = TaskOutcome()
    decision = _evaluate(AnswerAcceptance(), outcome, evidence, answer, "Используй только один поисковый запрос.")
    assert decision.action == "accept" and decision.answer_status == "degraded"
    assert answer in decision.text and "Не удалось подтвердить" not in decision.text
    assert any(check["field"] == "max_search_queries" and not check["passed"]
               for check in outcome.answer_verification["user_constraint_checks"])


def test_user_format_condition_gets_one_rewrite_then_a_partial_answer():
    evidence, answer = _saved_web_flow()
    outcome, acceptance = TaskOutcome(), AnswerAcceptance()
    request = "Объясни keep_only. Добавь фразу «готово»."
    retry = _evaluate(acceptance, outcome, evidence, answer, request)
    assert retry.action == "retry" and retry.reason == "user_constraint"
    assert "готово" in retry.correction and "task_decide" not in retry.correction
    acceptance.commit(retry)
    final = _evaluate(acceptance, outcome, evidence, answer, request, step=5)
    assert final.action == "accept" and final.answer_status == "degraded" and answer in final.text
    fixed = _evaluate(AnswerAcceptance(), TaskOutcome(), evidence, answer + "\n\nготово", request)
    assert fixed.action == "accept" and fixed.answer_status == "complete"


def test_sourced_answer_without_user_conditions_is_complete():
    evidence, answer = _saved_web_flow()
    decision = _evaluate(AnswerAcceptance(), TaskOutcome(), evidence, answer,
                         "Объясни keep_only по официальной документации.")
    assert decision.action == "accept" and decision.answer_status == "complete" and answer in decision.text


@pytest.mark.parametrize("user_text", [
    "Не используй только один поисковый запрос, ищи столько, сколько нужно.",
    "Do not use only one search query; investigate deeply.",
    "Не включи фразу «ошибка» в ответ.",
    "Don't use only web_search; read the sources too.",
])
def test_negated_directive_never_creates_a_limit(user_text):
    assert explicit_web_answer_constraints(user_text) == {}


def test_negation_in_previous_sentence_does_not_cancel_positive_instruction():
    assert explicit_web_answer_constraints("Не включай фразу «ошибка». Добавь фразу «готово».") == {"contains": ["готово"]}
    assert explicit_web_answer_constraints("Don't use code. Use only web_search.") == {"allowed": ["web_search"]}
