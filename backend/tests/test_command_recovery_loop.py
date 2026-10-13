"""Outside the Web a repeated command runs; the Web limit ends in the model's own answer."""
import sys

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_journal import RunJournal


def test_repeated_skill_command_runs_until_the_model_answers(tmp_path, monkeypatch):
    # John 2026-10-12: no repeat limit outside the Web; a command is never refused.
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    model_calls = 0
    command = f'"{sys.executable}" -c "print(123)"'

    def chat(**_kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 8:
            return {"message": {"content": "Команда семь раз вывела 123.", "tool_calls": []}}
        return {"message": {"content": "", "tool_calls": [{
            "id": f"repeat-{model_calls}", "type": "function",
            "function": {"name": "run_bash", "arguments": {"command": command}},
        }]}}

    run_id = "repeated-skill-command"
    events = list(stream_code_agent(
        user_message="Проверь существующие данные проекта.", project_root=tmp_path,
        model="test-model", chat_fn=chat, run_id=run_id,
        auto_remember=False, permission_mode="bypass", thinking=False,
    ))
    assert len([e for e in events if e["type"] == "tool_started" and e["tool"] == "run_bash"]) == 7
    receipts = [e for e in events if e["type"] == "tool_call" and e["tool"] == "run_bash"]
    assert all(e.get("dispatched") is not False for e in receipts)
    assert not any(e.get("error") == "repeated_tool_call" for e in events)
    assert events[-1]["stop_reason"] == "answer"
    assert RunJournal.load(run_id).state["command_progress"]["calls"] == {}


def test_web_repeat_limit_gives_the_model_a_last_turn_without_tools(tmp_path, monkeypatch):
    # John 2026-10-12: the limit steers; its notice is never the answer in chat.
    from app.application.agent_kernel.executor import ToolExecutionResult
    from app.application.code_agent import agent_loop

    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    executed = []
    monkeypatch.setattr(agent_loop, "_kernel_exec", lambda request, **kwargs: (
        executed.append(request.tool_name) or ToolExecutionResult(
            status="ok", output={"ok": True, "text": "Нашлось: nginx 1.27.3 вышел 26.11.2024."})))
    tools_seen = []

    def chat(**kwargs):
        tools_seen.append(bool(kwargs.get("tools")))
        if not kwargs.get("tools"):
            return {"message": {"content": "Нашла: nginx 1.27.3 вышел 26.11.2024. Другого не нашла.",
                                "tool_calls": []}}
        return {"message": {"content": "", "tool_calls": [{
            "id": f"web-{len(tools_seen)}", "type": "function",
            "function": {"name": "web_search", "arguments": {"queries": ["nginx 1.27.3"], "audience": "global", "mode": "quick"}},
        }]}}

    events = list(stream_code_agent(
        user_message="Найди в интернете, когда вышел nginx 1.27.3.", project_root=tmp_path,
        model="test-model", chat_fn=chat, run_id="web-answer-only",
        auto_remember=False, permission_mode="bypass", thinking=False,
    ))
    receipts = [e for e in events if e["type"] == "tool_call" and e["tool"] == "web_search"]
    assert executed == ["web_search"] * 4
    assert receipts[-1]["error"] == "repeated_tool_call" and receipts[-1]["dispatched"] is False
    assert tools_seen[-1] is False and all(tools_seen[:-1])
    final = next(e for e in events if e["type"] == "final_response")
    assert final["text"].startswith("Нашла: nginx 1.27.3")
    assert "ВОССТАНОВЛЕНИЕ" not in final["text"]
    assert events[-1]["stop_reason"] == "answer"


def test_answer_only_turn_never_runs_a_call_the_model_still_writes(tmp_path, monkeypatch):
    from app.application.agent_kernel.executor import ToolExecutionResult
    from app.application.code_agent import agent_loop

    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    executed = []
    monkeypatch.setattr(agent_loop, "_kernel_exec", lambda request, **kwargs: (
        executed.append(request.tool_name) or ToolExecutionResult(status="ok", output={"ok": True, "text": "x"})))
    no_tool_turns = 0

    def chat(**kwargs):
        nonlocal no_tool_turns
        if not kwargs.get("tools"):
            no_tool_turns += 1
            if no_tool_turns == 2:
                return {"message": {"content": "Ответ по найденному.", "tool_calls": []}}
        return {"message": {"content": "", "tool_calls": [{
            "id": f"web-{len(executed)}-{no_tool_turns}", "type": "function",
            "function": {"name": "web_fetch", "arguments": {"urls": ["https://example.org"]}},
        }]}}

    events = list(stream_code_agent(
        user_message="Прочитай https://example.org.", project_root=tmp_path,
        model="test-model", chat_fn=chat, run_id="web-answer-only-ignored",
        auto_remember=False, permission_mode="bypass", thinking=False,
    ))
    assert executed == ["web_fetch"] * 4
    assert next(e for e in events if e["type"] == "final_response")["text"] == "Ответ по найденному."
    assert events[-1]["stop_reason"] == "answer"


def test_recovery_accepts_observed_diagnosis_and_changed_external_result(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    observed = tmp_path / "observed.txt"
    observed.write_text("waiting", encoding="utf-8")
    command = f'"{sys.executable}" -c "from pathlib import Path; print(Path(\'observed.txt\').read_text())"'
    calls = 0

    def chat(**kwargs):
        nonlocal calls
        calls += 1
        assert calls <= 7
        if calls == 7:
            return {"message": {"content": "Наблюдаемое состояние изменилось на ready.", "tool_calls": []}}
        if calls == 5:
            assert any(m.get("role") == "tool" and "одинаковый наблюдаемый результат" in str(m.get("content"))
                       for m in kwargs["messages"])
            # External writer changes state; the agent must observe it before retry.
            observed.write_text("ready", encoding="utf-8")
            name, arguments = "read_file", {"path": "observed.txt"}
        else:
            name, arguments = "run_bash", {"command": command}
        return {"message": {"content": "", "tool_calls": [{
            "id": f"observe-{calls}", "type": "function",
            "function": {"name": name, "arguments": arguments},
        }]}}

    events = list(stream_code_agent(
        user_message="Проверь существующие данные проекта.", project_root=tmp_path,
        model="test-model", chat_fn=chat, run_id="recovery-new-evidence",
        auto_remember=False, permission_mode="bypass", thinking=False,
    ))
    command_results = [e for e in events if e["type"] == "tool_call" and e["tool"] == "run_bash" and e["ok"]]
    assert len(command_results) == 5
    assert "ready" in command_results[-1]["result"]
    assert events[-1]["stop_reason"] == "answer"
    assert not any(e.get("status") == "blocked" for e in events)


def test_words_around_a_call_in_the_answer_only_turn_are_not_the_answer(tmp_path, monkeypatch):
    # KZ run dbd2b141: "Собираю информацию…" plus a written web_search became the answer.
    from app.application.agent_kernel.executor import ToolExecutionResult
    from app.application.code_agent import agent_loop

    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(agent_loop, "_kernel_exec", lambda request, **kwargs: ToolExecutionResult(
        status="ok", output={"ok": True, "text": "Нашлось: шторм в Алматы 12.10."}))
    search = {"queries": ["ЧП Казахстан неделя"], "audience": "regional:Kazakhstan", "mode": "deep"}
    no_tool_turns = 0

    def chat(**kwargs):
        nonlocal no_tool_turns
        if not kwargs.get("tools"):
            no_tool_turns += 1
            if no_tool_turns == 1:
                return {"message": {"content": "Собираю информацию о ЧП за неделю.", "tool_calls": [{
                    "id": "late", "type": "function", "function": {"name": "web_search", "arguments": search}}]}}
            return {"message": {"content": "Нашла: шторм в Алматы 12.10. Больше подтверждённого нет.",
                                "tool_calls": []}}
        return {"message": {"content": "", "tool_calls": [{
            "id": "s", "type": "function", "function": {"name": "web_search", "arguments": search}}]}}

    events = list(stream_code_agent(
        user_message="Найди в интернете ЧП в Казахстане за неделю.", project_root=tmp_path,
        model="test-model", chat_fn=chat, run_id="answer-only-preamble",
        auto_remember=False, permission_mode="bypass", thinking=False,
    ))
    final = next(e for e in events if e["type"] == "final_response")
    assert final["text"].startswith("Нашла: шторм") and no_tool_turns == 2
