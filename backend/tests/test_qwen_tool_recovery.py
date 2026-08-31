from __future__ import annotations

import io

from docx import Document

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.media import resource_store


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "message": {
            "content": "",
            "tool_calls": [{
                "function": {"name": name, "arguments": arguments},
            }],
        },
    }


def test_read_file_recovers_unique_truncated_prefix_from_previous_glob(tmp_path) -> None:
    generated = tmp_path / "generated"
    generated.mkdir()
    full_name = "коммерческое_предложение_для_заказчика_финальное.docx.txt"
    (generated / full_name).write_text("ТОЧНОЕ СОДЕРЖИМОЕ 8472", encoding="utf-8")
    responses = iter([
        _tool_call("glob", {"pattern": "generated/*"}),
        _tool_call("read_file", {"path": "generated/комм.txt"}),
        {"message": {"content": "Файл прочитан.", "tool_calls": []}},
    ])

    events = list(stream_code_agent(
        user_message="Найди и прочитай текстовый файл в generated",
        project_root=tmp_path,
        run_id="qwen-prefix-recovery",
        chat_fn=lambda **_kwargs: next(responses),
        auto_remember=False,
        permission_mode="bypass",
    ))

    read_event = next(
        event for event in events
        if event.get("type") == "tool_call" and event.get("tool") == "read_file"
    )
    assert read_event["ok"] is True
    assert read_event["arguments"]["path"] == f"generated/{full_name}"
    assert read_event["recovered_from"] == "generated/комм.txt"
    assert "ТОЧНОЕ СОДЕРЖИМОЕ 8472" in read_event["result"]


def test_read_file_refuses_third_identical_failed_path(tmp_path) -> None:
    responses = iter([
        _tool_call("read_file", {"path": "missing-report.docx"}),
        _tool_call("read_file", {"path": "missing-report.docx"}),
        _tool_call("read_file", {"path": "missing-report.docx"}),
        {"message": {"content": "Файл не найден.", "tool_calls": []}},
    ])

    events = list(stream_code_agent(
        user_message="Прочитай missing-report.docx",
        project_root=tmp_path,
        run_id="qwen-read-retry-cap",
        chat_fn=lambda **_kwargs: next(responses),
        auto_remember=False,
        permission_mode="bypass",
    ))

    reads = [
        event for event in events
        if event.get("type") == "tool_call" and event.get("tool") == "read_file"
    ]
    assert len(reads) == 3
    assert reads[0]["error"] == "file_not_found"
    assert reads[1]["error"] == "file_not_found"
    assert reads[2]["error"] == "read_retry_exhausted"
    assert "не повторяй тот же путь" in reads[2]["result"]


def test_read_file_maps_missing_attachment_name_to_unique_resource(tmp_path) -> None:
    document = Document()
    document.add_paragraph("РЕКВИЗИТЫ ФИРМЕННОГО БЛАНКА 9184")
    payload = io.BytesIO()
    document.save(payload)
    record = resource_store.register_resource(
        original_name="Бланк фирменный.DOCX",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        owner_session="qwen-resource-test",
        data=payload.getvalue(),
    )
    ref = resource_store.resource_ref(record)
    responses = iter([
        _tool_call("read_file", {"path": "Бланк фирменный.DOCX"}),
        {"message": {"content": "Вложение прочитано.", "tool_calls": []}},
    ])

    try:
        events = list(stream_code_agent(
            user_message="Прочитай прикреплённый Бланк фирменный.DOCX",
            project_root=tmp_path,
            run_id="qwen-resource-name-recovery",
            resource_refs=[ref],
            base_tools=["read_file", "resource_process"],
            chat_fn=lambda **_kwargs: next(responses),
            auto_remember=False,
            permission_mode="bypass",
        ))
    finally:
        resource_store.discard(record)

    read_event = next(
        event for event in events
        if event.get("type") == "tool_call" and event.get("tool") == "read_file"
    )
    assert read_event["ok"] is True
    assert read_event["arguments"] == {"path": "Бланк фирменный.DOCX"}
    assert read_event["resolved_from_resource"] is True
    assert read_event["resource_id"] == record.resource_id
    assert "РЕКВИЗИТЫ ФИРМЕННОГО БЛАНКА 9184" in read_event["result"]


def test_existing_project_file_wins_over_same_named_resource(tmp_path) -> None:
    name = "Бланк фирменный.txt"
    (tmp_path / name).write_text("PROJECT FILE 3197", encoding="utf-8")
    record = resource_store.register_resource(
        original_name=name,
        content_type="text/plain",
        owner_session="qwen-resource-collision-test",
        data=b"ATTACHED RESOURCE 8841",
    )
    responses = iter([
        _tool_call("read_file", {"path": name}),
        {"message": {"content": "Файл прочитан.", "tool_calls": []}},
    ])

    try:
        events = list(stream_code_agent(
            user_message=f"Прочитай project-файл {name}",
            project_root=tmp_path,
            run_id="qwen-resource-project-collision",
            resource_refs=[resource_store.resource_ref(record)],
            chat_fn=lambda **_kwargs: next(responses),
            auto_remember=False,
            permission_mode="bypass",
        ))
    finally:
        resource_store.discard(record)

    read_event = next(
        event for event in events
        if event.get("type") == "tool_call" and event.get("tool") == "read_file"
    )
    assert read_event["ok"] is True
    assert read_event.get("resolved_from_resource") is not True
    assert "PROJECT FILE 3197" in read_event["result"]
    assert "ATTACHED RESOURCE 8841" not in read_event["result"]
