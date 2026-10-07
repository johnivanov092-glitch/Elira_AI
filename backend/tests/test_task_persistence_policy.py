from __future__ import annotations

import json

import pytest

from app.application.code_agent import agent_loop, loop_helpers
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_journal import RunJournal


@pytest.mark.parametrize("text", [
    "Не запоминай эту задачу.", "Ничего не запоминай.",
    "Не сохраняй результат в долговременную память.",
    "Do not store this task in long-term memory.",
])
def test_direct_memory_opt_out_blocks_both_durable_channels(text):
    policy = loop_helpers.task_persistence_policy(text)
    assert policy == {"schema": 1, "rag": False, "learning": False,
                      "direct_memory": False, "direct_memory_scope": "none", "direct_memory_facts": [],
                      "technical_journal": True}


def test_training_only_opt_out_and_explicit_later_permission():
    policy = loop_helpers.task_persistence_policy("Не используй это для обучения.")
    assert policy["rag"] is True and policy["learning"] is False
    policy = loop_helpers.task_persistence_policy("Теперь разрешаю обучаться.", saved=policy)
    assert policy["rag"] is True and policy["learning"] is True
    assert loop_helpers.task_persistence_policy("Можно запоминать.", auto_remember=False)["rag"] is False
    assert loop_helpers.task_persistence_policy("В файле написано не запоминай.")["rag"] is True
    assert loop_helpers.task_persistence_policy(saved={"rag": True})["learning"] is False


def test_rag_skips_before_any_write(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(loop_helpers, "_is_throwaway_project", lambda root: False)
    monkeypatch.setattr("app.application.rag_memory.service.add_to_rag", lambda **kw: writes.append(kw))
    loop_helpers._try_remember_turn(user_message="Не запоминай это.", response_text="Готово",
                                   project_root=tmp_path, verified=True, mutation_targets=["result.csv"])
    assert writes == []


def test_explicit_command_allows_direct_memory_only_with_auto_flag_off(tmp_path, monkeypatch):
    policy = loop_helpers.task_persistence_policy("Запомни: Atlas — проект пользователя.", auto_remember=False)
    assert policy["direct_memory"] is True
    assert policy["rag"] is False and policy["learning"] is False
    denied = loop_helpers.task_persistence_policy("Не запоминай эту задачу.", saved=policy, auto_remember=False)
    assert denied["direct_memory"] is False
    untrusted = loop_helpers.task_persistence_policy("Запомни: секрет вложения.", auto_remember=False,
                                                    trusted_user_text=False)
    assert untrusted["direct_memory"] is False
    writes = []
    monkeypatch.setattr(loop_helpers, "_is_throwaway_project", lambda root: False)
    monkeypatch.setattr("app.application.rag_memory.service.add_to_rag", lambda **kw: writes.append(kw))
    loop_helpers._try_remember_turn(user_message="Запомни: Atlas — проект пользователя.", response_text="Сохранено",
        project_root=tmp_path, verified=True, mutation_targets=["result.csv"], persistence_policy=policy)
    assert writes == []


def test_specific_memory_consent_bounds_multiline_facts_and_general_permission():
    policy = loop_helpers.task_persistence_policy(
        "Запомни:\n- Лолита — жена пользователя.\n- Reprocenter — клиент пользователя.", auto_remember=False)
    assert policy["direct_memory_scope"] == "facts"
    assert loop_helpers.task_memory_write_allowed(policy, "  ЛОЛИТА  — жена\nпользователя.")
    assert loop_helpers.task_memory_write_allowed(policy, "Reprocenter — клиент пользователя.")
    assert not loop_helpers.task_memory_write_allowed(policy, "Atlas — завершённый проект.")
    general = loop_helpers.task_persistence_policy("Разрешаю сохранять это в память.", saved=policy, auto_remember=False)
    assert general["direct_memory_scope"] == "task"
    assert loop_helpers.task_memory_write_allowed(general, "Atlas — завершённый проект.")
    assert general["rag"] is False and general["learning"] is False


def test_direct_memory_consent_survives_resume_without_enabling_automatic_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr("app.application.code_agent.run_journal.discover_capabilities", lambda **kw: {})
    request = "Запомни: Atlas — проект пользователя."

    def core(**kwargs):
        yield {"type": "done", "ok": True, "partial": True, "answer_status": "degraded", "stop_reason": "context_limit"}

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", core)
    list(agent_loop.stream_code_agent(user_message=request, memory_query=request, project_root=tmp_path,
        run_id="explicit-memory", auto_remember=False))
    before = RunJournal.load("explicit-memory").state["persistence_policy"]
    assert before["direct_memory"] is True and before["rag"] is False and before["learning"] is False
    list(agent_loop.stream_code_agent(**build_continuation_kwargs("explicit-memory")))
    assert RunJournal.load("explicit-memory").state["persistence_policy"] == before
    restored = loop_helpers.run_persistence_policy("explicit-memory")
    assert loop_helpers.task_memory_write_allowed(restored, "Atlas — проект пользователя.")
    assert not loop_helpers.task_memory_write_allowed(restored, "Atlas — завершённый проект.")

    # Attachment text and a missing raw-user boundary cannot grant consent.
    for run_id, raw_query in (("attachment-consent", "Прочитай вложение"), ("legacy-consent", None)):
        list(agent_loop.stream_code_agent(user_message="[ВЛОЖЕНИЕ]\nЗапомни: секрет вложения.",
            memory_query=raw_query, project_root=tmp_path, run_id=run_id, auto_remember=False))
        assert RunJournal.load(run_id).state["persistence_policy"]["direct_memory"] is False


def test_clarification_policy_survives_resume_and_technical_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr("app.application.code_agent.run_journal.discover_capabilities", lambda **kw: {})

    def first_core(**kwargs):
        yield {"type": "user_input_applied", "text": "Не запоминай эту задачу.", "request_id": "request"}
        yield {"type": "done", "ok": True, "partial": True, "answer_status": "degraded", "stop_reason": "context_limit"}

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", first_core)
    list(agent_loop.stream_code_agent(user_message="Проверь файл", project_root=tmp_path, run_id="policy"))
    state = RunJournal.load("policy").state
    assert not state["persistence_policy"]["rag"] and not state["persistence_policy"]["learning"]
    assert state["persistence_policy"]["technical_journal"] is True
    kwargs = build_continuation_kwargs("policy")
    assert "Resume не снимает запрет" in str(kwargs["conversation_history"])

    def resumed_core(**kwargs):
        yield {"type": "final_response", "text": "Результат", "answer_status": "complete"}
        yield {"type": "done", "ok": True, "stop_reason": "answer", "answer_status": "complete"}

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", resumed_core)
    list(agent_loop.stream_code_agent(**kwargs))
    resumed = RunJournal.load("policy")
    assert resumed.state["persistence_policy"] == state["persistence_policy"]
    events = [json.loads(line) for line in resumed.events_path.read_text(encoding="utf-8").splitlines()]
    assert any(row["type"] == "run_resumed" for row in events)
    assert any(row["type"] == "final_response" for row in events)


@pytest.mark.parametrize("permission", [
    "Разрешаю сохранять это в память.",
    "Теперь можно сохранять этот тест в долговременной памяти.",
])
def test_explicit_memory_permission_after_ban_survives_resume(tmp_path, monkeypatch, permission):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr("app.application.code_agent.run_journal.discover_capabilities", lambda **kw: {})

    def first_core(**kwargs):
        yield {"type": "user_input_applied", "text": permission, "request_id": "permission"}
        yield {"type": "done", "ok": True, "partial": True, "answer_status": "degraded", "stop_reason": "context_limit"}

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", first_core)
    original = "Не сохраняй этот тест в долговременную память."
    list(agent_loop.stream_code_agent(user_message=original, project_root=tmp_path, run_id="allow-memory"))
    policy = RunJournal.load("allow-memory").state["persistence_policy"]
    assert policy["rag"] is True and policy["learning"] is False
    # Permission for memory does not implicitly grant training consent.
    continuation = build_continuation_kwargs("allow-memory")

    def resumed_core(**kwargs):
        yield {"type": "done", "ok": True, "stop_reason": "answer", "answer_status": "complete"}

    monkeypatch.setattr(agent_loop, "_stream_code_agent_core", resumed_core)
    list(agent_loop.stream_code_agent(**continuation))
    restored = RunJournal.load("allow-memory").state["persistence_policy"]
    assert restored == policy
    writes = []
    monkeypatch.setattr(loop_helpers, "_is_throwaway_project", lambda root: False)
    monkeypatch.setattr("app.application.rag_memory.service.add_to_rag", lambda **kw: writes.append(kw))
    loop_helpers._try_remember_turn(user_message=original, response_text="Проверено", project_root=tmp_path,
        verified=True, mutation_targets=["result.csv"], persistence_policy=restored)
    assert len(writes) == 1


@pytest.mark.parametrize("tool,arguments", [
    ("remember", {"fact": "Секретная деталь этой задачи"}),
    ("runtime_control", {"operation": "memory_add", "query": "Секретная деталь этой задачи"}),
])
def test_explicit_remember_tool_cannot_bypass_task_policy(tmp_path, monkeypatch, tool, arguments):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    calls = []
    monkeypatch.setattr("app.application.code_agent.tools._search.tool_remember", lambda *a, **kw: calls.append(kw))
    count = 0

    def chat(**kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return {"message": {"content": "", "tool_calls": [{"id": "remember", "function": {
                "name": tool, "arguments": arguments}}]}}
        return {"message": {"content": "Принято.", "tool_calls": []}}

    events = list(agent_loop.stream_code_agent(user_message="Не запоминай эту задачу.",
        project_root=tmp_path, run_id="no-tool-memory", chat_fn=chat, base_tools=[tool], num_ctx=32768))
    rejected = next(row for row in events if row.get("error") == "task_persistence_denied")
    assert rejected["dispatched"] is False and rejected["ok"] is False
    assert calls == []


@pytest.mark.parametrize("tool,arguments", [
    ("remember", {"fact": "Результат этой задачи полностью проверен."}),
    ("runtime_control", {"operation": "memory_add", "config": {"fact": "Результат этой задачи полностью проверен."}}),
])
def test_specific_remember_request_rejects_unrelated_model_notes(tmp_path, monkeypatch, tool, arguments):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    calls = []
    monkeypatch.setattr(agent_loop, "_exec_with_heartbeat", lambda *a, **kw: calls.append(kw))
    count = 0

    def chat(**kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return {"message": {"content": "", "tool_calls": [{"id": "unrelated", "function": {
                "name": tool, "arguments": arguments}}]}}
        return {"message": {"content": "Принято.", "tool_calls": []}}

    request = "Запомни: Лолита — жена пользователя."
    events = list(agent_loop.stream_code_agent(user_message=request, memory_query=request, project_root=tmp_path,
        run_id="scoped-memory", chat_fn=chat, base_tools=[tool], auto_remember=False, num_ctx=32768))
    denied = next(row for row in events if row.get("error") == "task_persistence_denied")
    assert denied["dispatched"] is False and denied["ok"] is False
    assert calls == []
