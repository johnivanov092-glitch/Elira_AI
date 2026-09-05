from unittest.mock import patch

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.capabilities import should_escalate_web_from_answer


@pytest.mark.parametrize("draft", [
    "У меня нет доступа к актуальным новостям в реальном времени.",
    "У меня нет прямого доступа к интернету.",
    "Я не имею доступа к свежим новостям.",
    "I don't have access to current news.",
])
def test_unverified_external_access_denial_needs_discovery(draft):
    assert should_escalate_web_from_answer(draft)


@pytest.mark.parametrize("draft", [
    "У меня нет доступа к этому локальному файлу.",
    "Привет! Рада тебя видеть.",
    "Лолита — твоя жена.",
])
def test_local_access_and_conversation_do_not_need_web_discovery(draft):
    assert not should_escalate_web_from_answer(draft)


@pytest.mark.parametrize("base_tools", [None, ["capability_load"]])
def test_current_events_access_denial_recovers_to_search_without_user_confirmation(tmp_path, base_tools):
    """Replay the actual failed first draft through the real loop/executor."""
    draft = (
        "Честно скажу: у меня нет доступа к актуальным новостям в реальном времени, "
        "а дата у нас уже сентябрь 2026 — это за пределами того, что я знаю из обучения.\n"
        "Если хочешь, могу поискать свежие новости через веб-инструменты "
        "(нужно будет загрузить группу web). Что ближе?"
    )
    responses = iter([
        {"message": {"content": draft, "tool_calls": []}},
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "web_search", "arguments": {"query": "current world events"},
        }}]}},
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "web_fetch", "arguments": {"url": "https://example.org/news"},
        }}]}},
        {"message": {"content": "Вот подтверждённые события: https://example.org/news", "tool_calls": []}},
    ])
    schemas = []

    def chat(**kwargs):
        schemas.append({tool["function"]["name"] for tool in kwargs["tools"]})
        return next(responses)

    with (
        patch("app.application.code_agent.tools._dispatch.tool_web_search", return_value={
            "ok": True, "text": "News: https://example.org/news",
        }),
        patch("app.application.code_agent.tools._dispatch.tool_web_fetch", return_value={
            "ok": True, "text": "[fetched: https://example.org/news]\nVerified current event.",
        }),
    ):
        events = list(stream_code_agent(
            user_message="что интересного в мире происходит?",
            memory_query="что интересного в мире происходит?",
            conversation_history=[
                {"role": "user", "content": "привет"},
                {"role": "assistant", "content": "Привет! Рада тебя видеть. Чем могу помочь?"},
            ],
            project_root=tmp_path, model="test-model", chat_fn=chat, base_tools=base_tools,
            thinking=True, reasoning_effort="low", permission_mode="bypass", auto_remember=False,
        ))

    calls = [event for event in events if event["type"] == "tool_call"]
    assert [event["tool"] for event in calls] == ["web_search", "web_fetch"]
    assert all(event["ok"] for event in calls)
    if base_tools is None:
        assert {"read_file", "runtime_control", "web_search", "web_fetch"} <= schemas[0]
    else:
        assert schemas[0] == {"capability_load", "ask_user", "workflow_request"}
    assert "web_search" in schemas[1]
    assert not any(event["type"] in {"waiting_approval", "workflow_request"} for event in events)
    final = next(event["text"] for event in events if event["type"] == "final_response")
    assert final != draft
    assert "https://example.org/news" in final
