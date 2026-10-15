from __future__ import annotations

import json

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.loop_helpers import split_status_report


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


def test_status_report_goes_to_work_activity_not_the_answer(tmp_path):
    (tmp_path / "report.txt").write_text("Заключение: очаг L4.", encoding="utf-8")
    report = "Сделано: прочитала отчёт.\nПроверено: стр. 1 — «очаг L4».\nОсталось: ничего."
    answer = "## Что в отчёте\nВ заключении указан очаг L4."
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"message": {"content": "Читаю отчёт.", "tool_calls": [{"id": "read", "type": "function",
                    "function": {"name": "read_file", "arguments": {"path": "report.txt"}}}]}}
        return {"message": {"content": f"{report}\n\n{answer}", "tool_calls": []}}

    events = list(stream_code_agent(
        user_message="Прочитай report.txt и объясни.", project_root=tmp_path, model="test-model",
        run_id="notes-report", auto_remember=False, chat_fn=chat,
    ))
    notes = [event["text"] for event in events if event["type"] == "step_note"]
    final = next(event for event in events if event["type"] == "final_response")
    assert notes == ["Читаю отчёт.", report]
    assert final["text"] == answer
    assert events.index(final) > max(i for i, event in enumerate(events) if event["type"] == "step_note")


@pytest.mark.parametrize(("text", "answer", "report"), [
    ("Сделано: прочитала.\nПроверено: стр. 4.\nОсталось: ничего.\nТеперь объясню.\n\n## Итог\nТекст.",
     "Теперь объясню.\n\n## Итог\nТекст.", "Сделано: прочитала.\nПроверено: стр. 4.\nОсталось: ничего."),
    ("Ответ.\n\n**Сделано:** файл создан\n**Проверено:**\n- тест прошёл\n- сборка прошла\n✅ Осталось: ничего",
     "Ответ.", "**Сделано:** файл создан\n**Проверено:**\n- тест прошёл\n- сборка прошла\n✅ Осталось: ничего"),
    ("Сделано: прочитала.\nОсталось: ничего.\n\n---\n\n# Отчёт\nТекст.", "# Отчёт\nТекст.",
     "Сделано: прочитала.\nОсталось: ничего."),
])
def test_split_status_report_takes_the_edge_block(text, answer, report):
    assert split_status_report(text) == (answer, report)


@pytest.mark.parametrize("text", [
    "Сделано: файл прочитан. Проверено: verified input. Осталось: ничего.",
    "Начало.\nПроверено: это часть ответа.\nКонец.",
    "Обычный ответ без описи.",
])
def test_split_status_report_keeps_report_only_and_middle_lines(text):
    assert split_status_report(text) == (text, "")
