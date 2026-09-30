"""The real stream stops unchanged execution even when the model ignores advice."""
import sys

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_journal import RunJournal


def test_ignored_recovery_blocks_execution_and_survives_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    model_calls = 0
    command = f'"{sys.executable}" -c "print(123)"'

    def chat(**_kwargs):
        nonlocal model_calls
        model_calls += 1
        assert model_calls <= 7, "The model ignored recovery and the runtime did not stop it"
        return {"message": {"content": "", "tool_calls": [{
            "id": f"repeat-{model_calls}", "type": "function",
            "function": {"name": "run_bash", "arguments": {"command": command}},
        }]}}

    run_id = "ignored-recovery"
    events = list(stream_code_agent(
        user_message="Проверь существующие данные проекта.", project_root=tmp_path,
        model="test-model", chat_fn=chat, run_id=run_id,
        auto_remember=False, permission_mode="bypass", thinking=False,
    ))
    assert len([e for e in events if e["type"] == "tool_started" and e["tool"] == "run_bash"]) == 3
    assert events[-1]["stop_reason"] == "blocked"
    assert events[-1]["resumable"] is True
    assert events[-1]["ok"] is False
    assert any(e["type"] == "command_recovery" and e["status"] == "recovery_required" for e in events)
    state = RunJournal.load(run_id).state
    assert state["status"] == "blocked" and state["resumable"]
    assert state["command_progress"]["results"]
    resumed = list(stream_code_agent(**build_continuation_kwargs(run_id, chat_fn=chat)))
    assert resumed[-1]["stop_reason"] == "blocked"
    assert not any(e["type"] == "tool_started" and e["tool"] == "run_bash" for e in resumed)


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
            assert any("recovery_required" in str(m.get("content")) for m in kwargs["messages"])
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
    assert len(command_results) == 4
    assert "ready" in command_results[-1]["result"]
    assert events[-1]["stop_reason"] == "answer"
    assert not any(e.get("status") == "blocked" for e in events)
