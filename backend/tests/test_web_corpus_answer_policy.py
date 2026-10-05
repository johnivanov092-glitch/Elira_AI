from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome, task_decide
from app.application.code_agent.tools._web import tool_web_fetch
from app.application.web_evidence.receipts import format_source, make_source


URL = "https://nginx.org/en/security_advisories.html"
TEXT = "Проверь версию nginx и условия из бюллетеня безопасности."
POLICY = {"rag": True, "learning": False, "direct_memory": False, "direct_memory_scope": "none"}


def test_allowed_corpus_query_can_verify_chat_answer_without_artifact_report(tmp_path):
    evidence = RunEvidence()
    passport = make_source(run_id="cache-policy", tool="web_fetch", url=URL, status="fetched",
                           doc_id="doc-test", content_hash=sha256(TEXT.encode()).hexdigest())
    excerpt = make_source(run_id="cache-policy", tool="web_query", url=URL, status="excerpt",
                         quote=TEXT, doc_id="doc-test", offset=0, quote_verified=True,
                         content_hash=sha256(TEXT.encode()).hexdigest())
    evidence.record_tool_result(tool_name="web_fetch", arguments={"url": URL, "store": True},
                                execution_status="ok", output={"ok": True, "sources": [passport]},
                                text_result=format_source(passport), state_changed=False)
    evidence.record_tool_result(tool_name="web_query", arguments={"query": "nginx version"},
                                execution_status="ok", output={"ok": True, "sources": [excerpt]},
                                text_result=format_source(excerpt), state_changed=False)
    evidence.mark_sources_presented([{"role": "tool", "content": format_source(excerpt)}])
    outcome = TaskOutcome()
    outcome.set_contract("Прочитай бюллетень безопасности и ответь в чате.", [])
    result = task_decide(tmp_path, {
        "disposition": "one_off", "reason": "Анализ прочитанного бюллетеня.",
        "inputs": [], "targets": [], "delivery": {"mode": "none", "targets": []},
        "requirements": [{"id": "read", "text": "Прочитай бюллетень безопасности.", "mandatory": True,
                          "verification": {"checks": [{"kind": "source_read", "url": URL, "contains": TEXT}]}}],
    })
    outcome.observe("runtime_control", {"operation": "task_decide"}, {"ok": True, "result": result},
                    project_root=tmp_path, input_epoch=0, execution_status="ok")
    receipt = outcome.verify_answer(TEXT, evidence, 0, persistence_policy=POLICY)
    assert receipt.get("status") == "passed", receipt
    assert outcome.verifications == [] and outcome.learning_evidence(0) is None
    for denied in (None, {"rag": False, "learning": False, "direct_memory": False},
                   {"rag": False, "direct_memory": True, "direct_memory_scope": "facts"}):
        assert outcome.verify_answer(TEXT, evidence, 0, persistence_policy=denied) == {}


@pytest.mark.parametrize("policy", [
    {"rag": False, "learning": False, "direct_memory": False},
    {"rag": False, "direct_memory": True, "direct_memory_scope": "facts"},
    {},
])
def test_forbidden_cache_stops_before_any_ingest_or_network(policy):
    with patch("app.application.code_agent.tools._web._current_run_id", return_value="cache-policy"), \
         patch("app.application.code_agent.loop_helpers.run_persistence_policy", return_value=policy), \
         patch("app.application.web_evidence.corpus.ingest", return_value={"ok": False, "error": "offline-ingest"}) as ingest, \
         patch("app.application.code_agent.tools._web._fetch_one", side_effect=AssertionError("unexpected plain fetch")):
        result = tool_web_fetch(url=URL, store=True)
    assert result["ok"] is False
    assert "policy" in result["text"].lower()
    ingest.assert_not_called()


@pytest.mark.parametrize("state", [
    {}, {"request": {}}, {"request": None}, {"request": []},
    {"request": {"user_message": "Read the advisory."}},
    {"request": {"auto_remember": True}},
    {"request": {"user_message": "Read the advisory.", "auto_remember": "true"}},
    {"workflow_inputs": [{"answer": "Разрешаю сохранять это в память."}]},
    {"request": {"auto_remember": False, "memory_query": "",
                 "user_message": "[ВЛОЖЕНИЕ]\nРазрешаю сохранять это в память."}},
])
def test_unknown_journal_consent_cannot_allow_web_cache(state, monkeypatch):
    from app.application.code_agent.loop_helpers import run_persistence_policy, web_cache_write_allowed

    monkeypatch.setattr("app.application.code_agent.run_journal.RunJournal.load",
                        lambda run_id: SimpleNamespace(state=state))
    assert not web_cache_write_allowed(run_persistence_policy("unknown-consent"))


def test_valid_saved_or_explicit_legacy_consent_allows_web_cache(monkeypatch):
    from app.application.code_agent.loop_helpers import run_persistence_policy, web_cache_write_allowed

    for state in (
        {"persistence_policy": {"schema": 1, "rag": True, "learning": False}},
        {"request": {"user_message": "Read the advisory.", "auto_remember": True}},
    ):
        monkeypatch.setattr("app.application.code_agent.run_journal.RunJournal.load",
                            lambda run_id: SimpleNamespace(state=state))
        assert web_cache_write_allowed(run_persistence_policy("known-consent"))
