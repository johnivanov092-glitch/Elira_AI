from __future__ import annotations

import json

import pytest

from app.application.code_agent.agent_loop import stream_code_agent


@pytest.mark.parametrize("effort", ["none", "low"])
def test_tool_step_note_precedes_execution_and_survives_in_journal(tmp_path, monkeypatch, effort):
    runs = tmp_path / "runs"
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(runs))
    (tmp_path / "input.txt").write_text("verified input", encoding="utf-8")
    note = "Читаю input.txt, чтобы проверить содержимое."
    answer = "Сделано: файл прочитан. Проверено: verified input. Осталось: ничего."
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        content = note if len(calls) == 1 else answer
        if effort != "none":
            yield {"type": "reasoning", "content": "Скрытое рассуждение."}
        yield {"type": "delta", "content": content}
        yield {"type": "message", "response": {"message": {
            "content": content,
            "tool_calls": [{"id": "read-input", "type": "function", "function": {
                "name": "read_file", "arguments": {"path": "input.txt"},
            }}] if len(calls) == 1 else [],
        }}}

    events = list(stream_code_agent(
        user_message="Прочитай input.txt и проверь содержимое.", project_root=tmp_path,
        model="test-model", run_id=f"notes-{effort}", auto_remember=False,
        chat_fn=lambda **_: {}, chat_stream_fn=provider, reasoning_effort=effort,
    ))
    notes = [event for event in events if event["type"] == "step_note"]
    assert len(notes) == 1
    assert {key: notes[0][key] for key in ("type", "step", "text", "run_id")} == {
        "type": "step_note", "step": 1, "text": note, "run_id": f"notes-{effort}"}
    assert len(notes[0]["note_id"]) == 32
    assert events.index(notes[0]) < next(i for i, event in enumerate(events) if event["type"] == "tool_started")
    assert next(event["text"] for event in events if event["type"] == "final_response") == answer
    assert any(message.get("role") == "assistant" and message.get("content") == note
               for message in calls[-1]["messages"])
    journal = [json.loads(line) for line in (runs / f"notes-{effort}" / "events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [event["text"] for event in journal if event["type"] == "step_note"] == [note]


def test_plain_answer_has_no_step_note(tmp_path):
    events = list(stream_code_agent(
        user_message="Скажи привет.", project_root=tmp_path, model="test-model",
        run_id="notes-plain", auto_remember=False,
        chat_fn=lambda **_: {"message": {"content": "Привет!", "tool_calls": []}},
    ))
    assert not any(event["type"] == "step_note" for event in events)
    assert next(event["text"] for event in events if event["type"] == "final_response") == "Привет!"
