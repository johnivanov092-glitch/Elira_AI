"""Attachment transcription is a mutable skill; Workflow still owns ambiguity."""
from __future__ import annotations

from _runtime_roles import runtime_section, user_texts
from app.application.code_agent.agent_loop import stream_code_agent


def _tool_call(name: str, arguments: dict) -> dict:
    return {"message": {"content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}},
    ]}}


def test_transcription_guidance_points_to_skill_without_builtin_target_route(tmp_path):
    seen_prompt = ""

    def fake_chat(**kwargs):
        nonlocal seen_prompt
        seen_prompt = runtime_section(kwargs["messages"]) + "\n" + "\n".join(user_texts(kwargs["messages"]))
        return {"message": {"content": "ok", "tool_calls": []}}

    list(stream_code_agent(
        user_message="Сделай расшифровку на локальном GPU", project_root=tmp_path,
        run_id="placement-prompt", resource_refs=[{
            "resource_id": "a" * 32, "name": "voice.ogg", "kind": "audio",
            "content_type": "audio/ogg", "size": 4,
        }], base_tools=["resource_process"], chat_fn=fake_chat,
        auto_remember=False, permission_mode="bypass",
    ))

    assert "[COMPUTE PLACEMENT]" not in seen_prompt
    assert "Где выполнить расшифровку?" not in seen_prompt
    assert "data/skills/audio-transcribe/transcribe.py" in seen_prompt
    assert "resource_materialize" in seen_prompt
    assert "run_server" in seen_prompt and "resource_publish" in seen_prompt
    assert "указанный GPU передавай как local_gpu" not in seen_prompt
    assert "resource_process auto перебирает" not in seen_prompt
    assert "Объяви все требования исходной задачи через task_decide" not in seen_prompt


def test_existing_ask_user_still_emits_workflow_input_for_real_ambiguity(tmp_path):
    options = ["Полный текст", "Краткий конспект"]
    events = list(stream_code_agent(
        user_message="Обработай вложение", project_root=tmp_path,
        run_id="placement-ask-user", base_tools=["resource_process"],
        chat_fn=lambda **_kwargs: _tool_call(
            "ask_user", {"question": "Какой результат нужен?", "options": options}),
        auto_remember=False, permission_mode="bypass", pause_for_workflow_request=True,
    ))

    request = next(event for event in events if event["type"] == "workflow_request")
    assert request["status"] == "needs_input"
    assert request["request"]["kind"] == "input"
    assert request["request"]["schema"]["properties"]["answer"]["enum"] == options
    done = next(event for event in events if event["type"] == "done")
    assert done["stop_reason"] == "workflow_request"
