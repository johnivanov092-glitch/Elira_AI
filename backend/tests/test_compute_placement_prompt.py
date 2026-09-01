from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.media import execution, resource_store


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "message": {
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


def test_transcription_attachment_injects_live_ask_user_placement_options(tmp_path) -> None:
    record = resource_store.register_resource(
        original_name="voice.ogg",
        content_type="audio/ogg",
        owner_session="placement-prompt-test",
        data=b"OggS fake audio",
    )
    seen_prompt = ""

    def fake_chat(**kwargs):
        nonlocal seen_prompt
        seen_prompt = str(kwargs["messages"][0]["content"])
        return {"message": {"content": "ok", "tool_calls": []}}

    catalog = [
        {
            "target": "local_gpu",
            "available": True,
            "device": "RTX 4060 Ti",
            "operations": ["transcribe"],
        },
        {
            "target": "server_cpu",
            "available": True,
            "device": "remote-stt",
            "operations": ["transcribe"],
        },
        {
            "target": "local_cpu",
            "available": True,
            "device": "cpu",
            "operations": ["transcribe"],
        },
    ]
    try:
        with patch.object(execution, "capability_catalog", return_value=catalog):
            list(stream_code_agent(
                user_message="Сделай расшифровку",
                project_root=tmp_path,
                run_id="placement-prompt",
                resource_refs=[resource_store.resource_ref(record)],
                base_tools=["resource_process"],
                chat_fn=fake_chat,
                auto_remember=False,
                permission_mode="bypass",
            ))
    finally:
        resource_store.discard(record)

    assert "ask_user" in seen_prompt
    assert "размещение работы, а не permission" in seen_prompt
    assert "Автоматически (auto)" in seen_prompt
    assert "Локальный GPU (local_gpu)" in seen_prompt
    assert "Серверный CPU (server_cpu)" in seen_prompt
    assert "Локальный CPU (local_cpu)" in seen_prompt


def test_non_media_attachment_does_not_inject_compute_placement(tmp_path) -> None:
    seen_prompt = ""

    def fake_chat(**kwargs):
        nonlocal seen_prompt
        seen_prompt = str(kwargs["messages"][0]["content"])
        return {"message": {"content": "ok", "tool_calls": []}}

    list(stream_code_agent(
        user_message="Прочитай документ",
        project_root=tmp_path,
        run_id="no-placement-prompt",
        resource_refs=[{
            "resource_id": "a" * 32,
            "name": "note.txt",
            "kind": "document",
            "content_type": "text/plain",
            "size": 4,
        }],
        base_tools=["resource_process"],
        chat_fn=fake_chat,
        auto_remember=False,
        permission_mode="bypass",
    ))

    assert "[COMPUTE PLACEMENT]" not in seen_prompt


def test_existing_ask_user_emits_compute_placement_workflow_request(tmp_path) -> None:
    options = [
        "Автоматически (auto)",
        "Локальный GPU (local_gpu)",
        "Серверный CPU (server_cpu)",
        "Локальный CPU (local_cpu)",
    ]
    events = list(stream_code_agent(
        user_message="Сделай расшифровку вложения",
        project_root=tmp_path,
        run_id="placement-ask-user",
        base_tools=["resource_process"],
        chat_fn=lambda **_kwargs: _tool_call(
            "ask_user",
            {"question": "Где выполнить расшифровку?", "options": options},
        ),
        auto_remember=False,
        permission_mode="bypass",
        pause_for_workflow_request=True,
    ))

    request = next(event for event in events if event["type"] == "workflow_request")
    assert request["status"] == "needs_input"
    assert request["request"]["kind"] == "input"
    assert request["request"]["schema"]["properties"]["answer"]["enum"] == options
    done = next(event for event in events if event["type"] == "done")
    assert done["stop_reason"] == "workflow_request"


def test_runtime_intercepts_implicit_auto_and_uses_existing_ask_user(tmp_path) -> None:
    record = resource_store.register_resource(
        original_name="voice.ogg",
        content_type="audio/ogg",
        owner_session="placement-intercept-test",
        data=b"OggS fake audio",
    )
    available = ("local_gpu", "server_cpu", "local_cpu")
    try:
        with patch.object(
            execution,
            "available_execution_targets",
            return_value=available,
        ):
            events = list(stream_code_agent(
                user_message="Сделай расшифровку вложения",
                project_root=tmp_path,
                run_id="placement-runtime-intercept",
                resource_refs=[resource_store.resource_ref(record)],
                base_tools=["resource_process"],
                chat_fn=lambda **_kwargs: _tool_call(
                    "resource_process",
                    {
                        "resource_id": record.resource_id,
                        "operation": "transcribe",
                        "execution_target": "auto",
                    },
                ),
                auto_remember=False,
                permission_mode="bypass",
                pause_for_workflow_request=True,
            ))
    finally:
        resource_store.discard(record)

    request = next(event for event in events if event["type"] == "workflow_request")
    assert request["status"] == "needs_input"
    assert request["request"]["message"] == "Где выполнить расшифровку?"
    assert request["request"]["schema"]["properties"]["answer"]["enum"] == [
        "Автоматически (auto)",
        "Локальный GPU (local_gpu)",
        "Серверный CPU (server_cpu)",
        "Локальный CPU (local_cpu)",
    ]
    assert not any(event.get("tool") == "resource_process" for event in events)


def test_explicit_automatic_placement_does_not_require_question() -> None:
    from app.application.code_agent.prompts import compute_placement_request

    request = compute_placement_request(
        task_text="Сделай расшифровку автоматически",
        arguments={"operation": "transcribe", "execution_target": "auto"},
        resource_refs=[{"resource_id": "a" * 32, "kind": "audio"}],
    )

    assert request is None


def test_workflow_answer_overrides_model_default_and_dispatches_selected_target(
    tmp_path,
) -> None:
    from app.application.media import processing

    record = resource_store.register_resource(
        original_name="voice.ogg",
        content_type="audio/ogg",
        owner_session="placement-resume-test",
        data=b"OggS fake audio",
    )
    responses = iter([
        _tool_call(
            "resource_process",
            {
                "resource_id": record.resource_id,
                "operation": "transcribe",
                "execution_target": "auto",
            },
        ),
        {"message": {"content": "Расшифровка готова.", "tool_calls": []}},
    ])
    result = {
        "ok": True,
        "operation": "transcribe",
        "resource_id": record.resource_id,
        "requested_target": "local_gpu",
        "selected_target": "local_gpu",
        "backend": "faster-whisper",
        "text": "готово",
    }
    try:
        with patch.object(processing, "process_resource", return_value=result) as process:
            events = list(stream_code_agent(
                user_message=(
                    "Продолжи расшифровку. WORKFLOW UI RESPONSE: "
                    '{"values":{"answer":"Локальный GPU (local_gpu)"}}'
                ),
                project_root=tmp_path,
                run_id="placement-resume-dispatch",
                resource_refs=[resource_store.resource_ref(record)],
                base_tools=["resource_process"],
                chat_fn=lambda **_kwargs: next(responses),
                auto_remember=False,
                permission_mode="bypass",
            ))
    finally:
        resource_store.discard(record)

    assert not any(event["type"] == "workflow_request" for event in events)
    assert any(event.get("tool") == "resource_process" for event in events)
    assert process.call_args.args[2] == "local_gpu"
