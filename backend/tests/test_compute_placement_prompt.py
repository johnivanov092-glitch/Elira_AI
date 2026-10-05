from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.media import execution, processing, resource_store


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "message": {
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


def test_transcription_guidance_leaves_placement_to_agent_without_server_probe(tmp_path) -> None:
    seen_prompt = ""

    def fake_chat(**kwargs):
        nonlocal seen_prompt
        seen_prompt = "\n".join(m.get("content", "") for m in kwargs["messages"][1:])
        return {"message": {"content": "ok", "tool_calls": []}}

    with patch.object(execution, "capability_catalog") as catalog:
        list(stream_code_agent(
            user_message="Сделай расшифровку",
            project_root=tmp_path,
            run_id="placement-prompt",
            resource_refs=[{
                "resource_id": "a" * 32,
                "name": "voice.ogg",
                "kind": "audio",
                "content_type": "audio/ogg",
                "size": 4,
            }],
            base_tools=["resource_process"],
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    assert "[COMPUTE PLACEMENT]" not in seen_prompt
    assert "Где выполнить расшифровку?" not in seen_prompt
    assert "локальное клиентское" in seen_prompt
    assert "восстановление локальной GPU-среды" in seen_prompt
    assert "resource_process auto перебирает только локальные GPU/CPU, server_cpu выбирается" in seen_prompt
    assert "Объяви все требования исходной задачи через task_decide" not in seen_prompt
    catalog.assert_not_called()


def test_existing_ask_user_still_emits_workflow_input_for_real_ambiguity(tmp_path) -> None:
    options = ["Полный текст", "Краткий конспект"]
    events = list(stream_code_agent(
        user_message="Обработай вложение",
        project_root=tmp_path,
        run_id="placement-ask-user",
        base_tools=["resource_process"],
        chat_fn=lambda **_kwargs: _tool_call(
            "ask_user",
            {"question": "Какой результат нужен?", "options": options},
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


@pytest.mark.parametrize(("task", "target"), [
    ("Сделай расшифровку вложения", "auto"),
    ("Сделай расшифровку вложения", "local_gpu"),
    ("Не спрашивай и делай на GPU", "local_gpu"),
    ("Продолжи расшифровку. WORKFLOW UI RESPONSE: "
     '{"values":{"answer":"Локальный GPU (local_gpu)"}}', "local_gpu"),
])
def test_agent_placement_dispatches_without_mandatory_question(tmp_path, task, target) -> None:
    record = resource_store.register_resource(
        original_name="voice.ogg",
        content_type="audio/ogg",
        owner_session="placement-agent-choice-test",
        data=b"OggS fake audio",
    )
    responses = iter([
        _tool_call("resource_process", {
            "resource_id": record.resource_id,
            "operation": "transcribe",
            "execution_target": target,
        }),
        {"message": {"content": "Расшифровка готова.", "tool_calls": []}},
    ])
    result = {
        "ok": True,
        "operation": "transcribe",
        "resource_id": record.resource_id,
        "requested_target": target,
        "selected_target": "local_gpu",
        "backend": "faster-whisper",
        "text": "готово",
    }
    try:
        with patch.object(processing, "process_resource", return_value=result) as process:
            events = list(stream_code_agent(
                user_message=task,
                project_root=tmp_path,
                run_id="placement-agent-choice",
                resource_refs=[resource_store.resource_ref(record)],
                base_tools=["resource_process"],
                chat_fn=lambda **_kwargs: next(responses),
                auto_remember=False,
                permission_mode="bypass",
                pause_for_workflow_request=True,
            ))
    finally:
        resource_store.discard(record)

    assert not any(event["type"] == "workflow_request" for event in events)
    assert any(event.get("tool") == "resource_process" and event.get("ok") for event in events)
    assert process.call_count == 1
    assert process.call_args.args[2] == target
