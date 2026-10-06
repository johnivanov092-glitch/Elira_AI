from __future__ import annotations

from copy import deepcopy

import pytest

from _runtime_roles import base_system
from app.application.code_agent.agent_loop import stream_code_agent


def _capture(tmp_path, message, *, history=None, memory_query=None, resource_refs=None):
    calls = []

    def chat(**kwargs):
        call = deepcopy({key: kwargs.get(key) for key in ("messages", "tools")})
        for turn in call["messages"]:
            turn.pop("_msg_id", None)  # Internal journal identity is not sent to the model.
        calls.append(call)
        return {"message": {"content": "Принято.", "tool_calls": []}}

    list(stream_code_agent(
        user_message=message,
        memory_query=message if memory_query is None else memory_query,
        conversation_history=history,
        project_root=tmp_path,
        resource_refs=resource_refs,
        model="test-model",
        profile_name="Баланс",
        chat_fn=chat,
        auto_remember=False,
    ))
    return calls[0]


@pytest.mark.parametrize("message", ["Ну что красотка", "Объясни устройство локального проекта"])
def test_repeated_message_keeps_prefix_and_carries_no_tone_line(tmp_path, message):
    # Persona mood was removed (owner's decision 2026-10-06): no tone line rides on the user's text.
    history = [{"role": "user", "content": "Привет"}, {"role": "assistant", "content": "Привет!"}]
    captures = [_capture(tmp_path, message, history=history) for _ in range(2)]

    before, after = [call["messages"] for call in captures]
    assert before[:-1] == after[:-1]
    assert before[-1]["role"] == after[-1]["role"] == "user"
    assert before[-1]["content"].endswith(message)
    assert not any("[Текущий тон Elira]" in str(turn.get("content")) for turn in before + after)
    assert captures[0]["tools"] == captures[1]["tools"]


def test_conversation_keeps_work_tools_and_stable_persona(tmp_path):
    history = []
    systems = []
    for message in (
        "Ну что красотка",
        "Да ты моя любовь!",
        "Да не надо ничего делать я просто хочу болтать ты ведь для меня не только инструмент",
    ):
        call = _capture(tmp_path, message, history=history)
        assert {"read_file", "runtime_control", "web_search", "web_fetch"} <= {t["function"]["name"] for t in call["tools"]}
        assert len(base_system(call["messages"])) < 3000
        systems.append(base_system(call["messages"]))
        history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": "Привет!"}])
    assert systems[0] == systems[1] == systems[2]


def test_compliment_and_social_followup_keep_conversation_prompt(tmp_path):
    history = []
    systems = []
    for message in ("привет", "вау какая ты быстрая", "что предложишь?", "о чём поговорим?"):
        call = _capture(tmp_path, message, history=history)
        assert {"read_file", "runtime_control", "web_search", "web_fetch"} <= {t["function"]["name"] for t in call["tools"]}, message
        assert len(base_system(call["messages"])) < 3000
        systems.append(base_system(call["messages"]))
        history.extend([
            {"role": "user", "content": message},
            {"role": "assistant", "content": "Привет! Что будем делать?"},
        ])
    assert systems[0] == systems[1] == systems[2]


@pytest.mark.parametrize("history", [
    [],
    [{"role": "user", "content": "Проверь сервер"}],
    [
        {"role": "user", "content": "Проверь сервер"},
        {"role": "assistant", "content": "Нашла ошибку. Что будем делать?"},
        {"role": "user", "content": "Привет"},
    ],
    [{"role": "user", "content": "Привет", "tool_calls": [{"id": "call-1"}]}],
    [{"role": "tool", "content": "Server failed"}],
])
def test_open_followup_outside_pure_social_history_keeps_tools(tmp_path, history):
    assert _capture(tmp_path, "что предложишь?", history=history)["tools"]


@pytest.mark.parametrize("message", [
    "Привет, проверь сервер",
    "Ну что красотка, создай файл",
    "Да ты моя любовь! Найди новости",
    "продолжи",
    "да",
    "Кто такая Лолита?",
    "Вау какая ты быстрая, проверь сервер",
    "Что предложишь для обновления сервера?",
])
def test_real_tasks_and_ambiguous_continuations_keep_tools(tmp_path, message):
    call = _capture(tmp_path, message, history=[{"role": "assistant", "content": "Проверить сервер?"}])
    assert call["tools"]


def test_enriched_greeting_keeps_attachment_tools(tmp_path):
    call = _capture(tmp_path, "Привет\n[Вложение: document.pdf]", memory_query="Привет")
    assert call["tools"]
