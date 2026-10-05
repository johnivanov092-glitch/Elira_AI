from app.application.code_agent.answer_acceptance import AnswerAcceptance
from test_readonly_search_answer_replay import (
    GOAL, NO_PERSISTENCE, RUN_ID, _saved_declaration, _saved_web_flow,
)


def test_failed_predicate_feedback_names_checks_without_changing_retry_identity(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    acceptance = AnswerAcceptance()
    kwargs = dict(
        final_text=answer, raw_user_message=GOAL, pending_redirected_jobs=[],
        active_capability_groups=["web", "runtime"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None,
        criteria_rows=[], persistence_policy={**NO_PERSISTENCE, "rag": True},
        step=5, run_id=RUN_ID,
    )
    first = acceptance.evaluate(**kwargs)
    assert first.action == "retry" and first.reason == "outcome"
    assert '"failed_checks": ["no_persistence"]' in first.correction
    assert '"requirement_id": "limits"' in first.correction
    assert "failed_checks" not in first.outcome_correction
    # Changing the failed predicates must not buy another correction attempt.
    outcome.correction = first.outcome_correction
    acceptance.commit(first)
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"url": "https://example.org/failed"},
        execution_status="error", output={"ok": False}, text_result="Unavailable",
        state_changed=False,
    )
    second = acceptance.evaluate(**{**kwargs, "step": 6})
    assert second.action == "accept" and second.answer_status == "degraded"
    assert outcome.verifications == []
