"""Search recovery through the real coordinator, native tool and evidence ledger."""
from copy import deepcopy

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.answer_acceptance import AnswerAcceptance
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.task_outcomes import TaskOutcome
from test_web_search_engine_warnings import URL, _http


def test_failed_browser_loop_stops_exact_passive_repetition_without_hiding_interaction(tmp_path, monkeypatch):
    from app.application.code_agent.tools import _web
    reads, turns = [], []
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    def render(*args):
        reads.append(args)
        raise TimeoutError("navigation timeout")
    monkeypatch.setattr(_web, "_browser_render", render)
    def chat(**kwargs):
        if not kwargs["tools"]:
            assert "[Ответ по прочитанному]" in kwargs["messages"][-1]["content"]
            return {"message": {"content": "Страница не открылась: истекло время ожидания."}}
        turns.append(1)
        assert len(turns) <= 8, "Failed passive browser reads must reach an answer"
        assert "browser" in {s["function"]["name"] for s in kwargs["tools"]}
        return {"message": {"tool_calls": [{"id": str(len(turns)), "function": {
            "name": "browser", "arguments": {"url": URL},
        }}]}}
    events = list(stream_code_agent(user_message="Прочитай страницу и сообщи, что удалось узнать.",
        project_root=tmp_path, chat_fn=chat, permission_mode="bypass", auto_remember=False,
        base_tools=["browser"], num_ctx=65536))
    assert len(reads) == 2
    assert events[-1]["stop_reason"] == "answer"
    assert "Задача не завершена" not in next(
        event for event in events if event["type"] == "final_response")["text"]


@pytest.mark.parametrize("obeys_recovery", [True, False])
def test_repeated_search_gets_a_final_answer_without_global_step_limit(tmp_path, obeys_recovery):
    """A web question never ends in «Задача не завершена»: the model answers from what it has."""
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        assert len(calls) <= 9, "An identical search must not loop indefinitely"
        if not kwargs["tools"]:
            assert not obeys_recovery and "[Ответ по прочитанному]" in kwargs["messages"][-1]["content"]
            return {"message": {"content": f"Найдена документация: [Источник]({URL}). "
                                           "Содержимое страницы пока не проверено."}}
        assert "web_search" in {schema["function"]["name"] for schema in kwargs["tools"]}
        if len(calls) >= 4 and obeys_recovery:
            return {"message": {"content": f"Найдена документация: [Источник]({URL}). Содержимое страницы пока не проверено."}}
        return {"message": {"content": "", "tool_calls": [{"id": str(len(calls)), "function": {
            "name": "web_search", "arguments": {"query": "service documentation"},
        }}]}}

    with _http({"results": [{"url": URL, "title": "Documentation"}], "unresponsive_engines": []}) as requests:
        events = list(stream_code_agent(user_message="Найди документацию сервиса.", project_root=tmp_path,
            chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536))
    assert len(requests) == 1
    final = next(event for event in events if event["type"] == "final_response")
    assert URL in final["text"] and "Задача не завершена" not in final["text"]
    assert events[-1]["stop_reason"] == "answer" and events[-1]["ok"]
    assert any(event.get("status") == "strategy_required" for event in events)
    state = RunJournal.load(events[-1]["run_id"]).state
    assert state["command_progress"]["web_repeats"]


def test_recovery_allows_a_changed_query_then_normal_answer(tmp_path):
    count = 0

    def chat(**kwargs):
        nonlocal count
        count += 1
        assert count < 8
        if count == 4:
            return {"message": {"content": f"Другой запрос нашёл документацию: [Источник]({URL})."}}
        return {"message": {"tool_calls": [{"function": {
            "name": "web_search", "arguments": {"query": "service documentation" if count < 3 else "service reference"},
        }}]}}

    with _http({"results": [{"url": URL, "title": "Documentation"}], "unresponsive_engines": []}) as requests:
        events = list(stream_code_agent(user_message="Найди документацию сервиса.", project_root=tmp_path,
            chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536))
    assert len(requests) == 2
    assert events[-1]["stop_reason"] == "answer"
    assert not any(event.get("status") == "strategy_required" for event in events)


def test_exhausted_search_never_accepts_an_unfinished_background_job():
    acceptance = AnswerAcceptance()
    outcome, evidence = TaskOutcome(), RunEvidence()
    decision = acceptance.evaluate(final_text="Готово.", raw_user_message="Проверь сервер и найди документацию.",
        pending_redirected_jobs=[19], active_capability_groups=["web"], task_outcome=outcome,
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None,
        step=8, run_id="pending")
    assert decision.action == "retry" and decision.reason == "background"


def test_runtime_contract_echo_is_never_a_complete_user_answer():
    acceptance = AnswerAcceptance()
    args = dict(final_text='[Системный ответ]\n[ТЕКУЩИЙ КОНТРАКТ ЗАДАЧИ — ДАННЫЕ RUNTIME]\n{"goal":"news"}',
        raw_user_message="Найди новости Казахстана.", pending_redirected_jobs=[],
        active_capability_groups=["web"], task_outcome=TaskOutcome(), run_evidence=RunEvidence(),
        code_input_epoch=0, quote_word_limit=None, step=1, run_id="echo")
    first = acceptance.evaluate(**args)
    assert first.action == "retry" and first.reason == "evidence"
    acceptance.commit(first)
    second = acceptance.evaluate(**args)
    assert second.action == "accept" and second.answer_status == "degraded"
    assert "ТЕКУЩИЙ КОНТРАКТ" not in second.text


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("same_batch", [False, True, "duplicates"])
@pytest.mark.parametrize("filename", ["selection.md", "selection.py"])
def test_search_recovery_continues_artifact_work_before_or_after_declaration(tmp_path, declared, same_batch, filename):
    """The real executor writes an evidence-based choice after exhausted search.

    No artifact contract may exist yet: history alone cannot classify the goal.
    Independent calls later in the same batch must survive a search refusal.
    """
    calls, seen = [], []

    def tool(name, **args):
        return {"id": name + str(len(calls)), "function": {"name": name, "arguments": args}}

    def chat(**kwargs):
        assert kwargs["tools"], "Recovery must keep independent work available"
        calls.append(deepcopy(kwargs["messages"]))
        number = len(calls) - int(declared)
        if declared and number == 0:
            return {"message": {"tool_calls": [tool("runtime_control", operation="task_decide", config={
                "disposition": "one_off", "reason": "Выбор по источникам и создание результата", "targets": [filename],
            })]}}
        assert number <= 4
        batch = [tool("web_search", query="service documentation")] if number <= 3 else []
        if same_batch == "duplicates" and number == 3:
            batch *= 7
        if number == (3 if same_batch else 4):
            assert any(URL in str(message.get("content")) for message in kwargs["messages"] if message["role"] == "tool")
            if not same_batch:
                names = {schema["function"]["name"] for schema in kwargs["tools"]}
                assert "web_search" in names and "write_file" in names
            batch.append(tool("write_file", path=filename, content=f'# Selected source: {URL}\n'))
        return {"message": {"tool_calls": batch}}

    with _http({"results": [{"url": URL, "title": "Documentation"}], "unresponsive_engines": []}) as requests:
        stream = stream_code_agent(user_message="Сравни варианты в интернете, выбери подходящий и создай " + filename,
            project_root=tmp_path, chat_fn=chat, permission_mode="bypass", auto_remember=False,
            base_tools=["web_search", "write_file", "runtime_control"], num_ctx=65536)
        try:
            for event in stream:
                seen.append(event)
                if event.get("tool") == "write_file" and event.get("state_changed"):
                    break
        finally:
            stream.close()
    assert len(requests) == 1
    assert URL in (tmp_path / filename).read_text(encoding="utf-8")
    assert not any(event["type"] in {"final_response", "done"} for event in seen)
    assert any(event.get("status") == "strategy_required" for event in seen)
