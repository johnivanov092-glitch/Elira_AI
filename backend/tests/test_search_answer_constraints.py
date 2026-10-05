"""User constraints remain binding; self-imposed search budgets do not erase answers."""
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.answer_contracts import explicit_web_answer_constraints
from app.application.code_agent.task_outcomes import TaskOutcome
import pytest
from test_readonly_search_answer_replay import _saved_declaration, _saved_web_flow, URL


def test_explicit_user_constraints_need_no_model_declaration():
    evidence, answer = _saved_web_flow()
    outcome = TaskOutcome()
    evidence.record_tool_result(tool_name="web_search", arguments={"query": "second"},
        execution_status="ok", output={"ok": True}, text_result="No results", state_changed=False)
    decision = AnswerAcceptance().evaluate(final_text=answer, raw_user_message="Используй только один поисковый запрос.",
        pending_redirected_jobs=[], active_capability_groups=["web"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None, step=4, run_id="no-declaration",
        persistence_policy={"rag": False, "direct_memory": False, "learning": False}, criteria_rows=[])
    assert decision.action == "retry" and decision.reason == "outcome"
    assert any(check["field"] == "max_search_queries" and not check["passed"]
               for check in outcome.answer_verification["user_constraint_checks"])
    assert "используй task_decide" not in decision.correction


def test_unsolicited_limits_and_headings_do_not_block_a_sourced_answer(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    for requirement in outcome.contract["requirements"]:
        for check in requirement["verification"]["checks"]:
            check.pop("user_quote", None)
            if check["kind"] == "answer_format":
                check.update(contains=["источники", "ограничение"], max_chars=5)
            if check["kind"] == "tool_policy":
                check.update(max_search_queries=0, max_read_urls=0)
    request = "Объясни keep_only по официальной документации."
    decision = AnswerAcceptance().evaluate(final_text=answer, raw_user_message=request,
        pending_redirected_jobs=[], active_capability_groups=["web", "runtime"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None, step=4, run_id="constraints",
        persistence_policy={"rag": True, "direct_memory": True, "learning": True}, criteria_rows=[])
    assert decision.action == "accept" and decision.answer_status == "complete"
    assert answer in decision.text
    predicates = [check for row in outcome.answer_verification["checks"] for check in row["predicates"]]
    assert any("max_search_queries" in check.get("unrequested_constraints", []) for check in predicates)
    # Reading is still mandatory; a fabricated source cannot be made advisory.
    outcome.contract["requirements"][1]["verification"]["checks"][0]["url"] = URL + "/unread"
    retry = AnswerAcceptance().evaluate(final_text=answer, raw_user_message=request,
        pending_redirected_jobs=[], active_capability_groups=["web", "runtime"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None, step=5, run_id="constraints",
        persistence_policy={"rag": True, "direct_memory": True, "learning": True}, criteria_rows=[])
    assert retry.action == "retry" and "source_read" in retry.correction


def test_fabricated_user_quote_does_not_authorize_a_budget(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    check = outcome.contract["requirements"][3]["verification"]["checks"][0]
    check.update(max_search_queries=0, user_quote="Найди")
    receipt = outcome.verify_answer(answer, evidence, 0,
        user_request="Найди документацию.", persistence_policy={"rag": False, "direct_memory": False, "learning": False})
    assert receipt["checks"][3]["passed"]
    assert "unrequested_constraints" in receipt["checks"][3]["predicates"][0]


def test_model_cannot_require_unrequested_quotation_count(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    for requirement in outcome.contract["requirements"]:
        for check in requirement["verification"]["checks"]:
            if check["kind"] == "cited_quote":
                check["count"] = 3
    receipt = outcome.verify_answer(answer, evidence, 0, user_request="Объясни документацию со ссылками.",
        persistence_policy={"rag": False, "direct_memory": False, "learning": False})
    assert all(check["passed"] for row in receipt["checks"] for check in row["predicates"]
               if check["kind"] == "cited_quote")
    requested = outcome.verify_answer(answer, evidence, 0, user_request="Приведи три цитаты из документации.",
        persistence_policy={"rag": False, "direct_memory": False, "learning": False})
    assert any(not check["passed"] for row in requested["checks"] for check in row["predicates"]
               if check["kind"] == "cited_quote")


def test_user_query_limit_applies_even_when_model_omits_quote_or_invents_larger_budget(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    check = outcome.contract["requirements"][3]["verification"]["checks"][0]
    check.pop("user_quote", None)
    check["max_search_queries"] = 200
    evidence.record_tool_result(tool_name="web_search", arguments={"query": "second"},
        execution_status="ok", output={"ok": True}, text_result="No results", state_changed=False)
    receipt = outcome.verify_answer(answer, evidence, 0,
        user_request="Используй только один поисковый запрос.",
        persistence_policy={"rag": False, "direct_memory": False, "learning": False})
    assert receipt["user_constraints"]["max_search_queries"] == 1
    assert receipt["checks"][3]["passed"] is False


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


def test_explicit_constraint_is_checked_when_model_omits_its_predicate(tmp_path):
    evidence, answer = _saved_web_flow()
    outcome = _saved_declaration(tmp_path, typed=True, evidence=evidence)
    outcome.contract["requirements"][3]["verification"]["checks"] = [{"kind": "no_persistence"}]
    evidence.record_tool_result(tool_name="web_search", arguments={"query": "second"},
        execution_status="ok", output={"ok": True}, text_result="No results", state_changed=False)
    acceptance = AnswerAcceptance()
    decision = acceptance.evaluate(final_text=answer, raw_user_message="Используй только один поисковый запрос.",
        pending_redirected_jobs=[], active_capability_groups=["web", "runtime"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None, step=4, run_id="constraints",
        persistence_policy={"rag": False, "direct_memory": False, "learning": False}, criteria_rows=[])
    assert decision.action == "retry" and decision.reason == "outcome"
    assert outcome.answer_verification["status"] == "unconfirmed"
