"""Search recovery through the real coordinator, native tool and evidence ledger."""
from copy import deepcopy

import pytest

from _runtime_roles import runtime_text
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from test_web_search_engine_warnings import URL, _http








def test_exhausted_search_never_accepts_an_unfinished_background_job():
    acceptance = AnswerAcceptance()
    outcome, evidence = TaskOutcome(), RunEvidence()
    decision = acceptance.evaluate(final_text="Готово.", raw_user_message="Проверь сервер и найди документацию.",
        pending_redirected_jobs=[19], active_capability_groups=["web"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None,
        step=8, run_id="pending")
    assert decision.action == "retry" and decision.reason == "background"
