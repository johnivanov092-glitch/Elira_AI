from __future__ import annotations

from copy import deepcopy

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.chat.local_chat import resolve_persona_mode
from app.application.persona.service import build_persona_prompt, mode_temperature
from app.core.persona_defaults import PERSONA_MODES


def capture(tmp_path, query, **extra):
    calls = []

    def chat(**kwargs):
        call = deepcopy({key: kwargs.get(key) for key in ("messages", "tools")})
        for turn in call["messages"]:
            turn.pop("_msg_id", None)  # Internal IDs do not affect the model prefix.
        calls.append(call)
        return {"message": {"content": "Принято.", "tool_calls": []}}

    list(stream_code_agent(
        user_message=query, memory_query=query, project_root=tmp_path,
        chat_fn=chat, auto_remember=False, **extra,
    ))
    return calls[0]


def names(call):
    return {s["function"]["name"] for s in call["tools"]}


def test_legacy_profiles_do_not_switch_identity_or_sampling():
    prompts = {build_persona_prompt(mode, "local-model") for mode in PERSONA_MODES}
    assert len(prompts) == 1
    assert len({mode_temperature(mode) for mode in PERSONA_MODES}) == 1
    assert {resolve_persona_mode(mode, "Напиши код проверки сервера") for mode in PERSONA_MODES} == {"Баланс"}


@pytest.mark.parametrize("query", [
    "Знаешь, просто приятно, что ты рядом",
    "Ну и денёк выдался, даже чай остыл",
    "Мне приснился смешной сон про летающий чайник",
    "Какое настроение сегодня?",
    "Проверь код проекта",
    "Кто такая Лолита?",
])
def test_any_message_starts_with_work_tools_and_one_persona(tmp_path, query):
    call = capture(tmp_path, query)
    assert {"capability_load", "runtime_control", "read_file", "web_search", "web_fetch"} <= names(call)
    assert "проверь результат" in str(call["messages"])
    assert "первичные источники" in str(call["messages"])
    assert len(call["messages"][0]["content"]) <= 3000
    assert "runtime" in str(call["tools"]) and "project" in str(call["tools"])


def test_memory_and_project_context_do_not_change_system(tmp_path, monkeypatch):
    from app.application import memory

    seen = []

    def facts(query, **kwargs):
        seen.append(query)
        return [{"text": "Лолита — жена пользователя."}] if "Лолита" in query else []

    monkeypatch.setattr(memory, "resolve_relevant_facts", facts)
    a = capture(tmp_path, "Кто такая Лолита?", profile_name="Личный")
    b = capture(tmp_path, "Проверь файл", profile_name="Инженерный", base_tools=["read_file"])
    assert a["messages"][0] == b["messages"][0]
    assert "Лолита" not in a["messages"][0]["content"]
    assert "Лолита — жена пользователя." in str(a["messages"][1:])
    assert "Лолита — жена пользователя." not in str(b["messages"])
    assert seen == ["Кто такая Лолита?", "Проверь файл"]


def test_current_message_follows_context_and_tone_without_changing_prefix(tmp_path, monkeypatch):
    from app.application.persona import mood

    history = [{"role": "user", "content": "Расскажи про вечер."},
               {"role": "assistant", "content": "За окном тихо."}]
    query = "Спасибо, на этом пока всё."
    calls = []
    for tone in ("ровное", "оживлённое"):
        monkeypatch.setattr(mood, "mood_overlay_line", lambda tone=tone: tone)
        call = capture(tmp_path, query, conversation_history=history)
        calls.append(call)
        assert call["messages"][-1]["content"].endswith("[Текущее сообщение пользователя]\n" + query)
        assert tone in call["messages"][-1]["content"]
    assert calls[0]["messages"][:-1] == calls[1]["messages"][:-1]
    assert calls[0]["tools"] == calls[1]["tools"]


def test_discovery_loads_project_in_same_loop_and_reads_file(tmp_path):
    (tmp_path / "hello.txt").write_text("проверенный факт", encoding="utf-8")
    calls = []
    responses = iter([
        {"message": {"content": "", "tool_calls": [{"id": "load", "function": {
            "name": "capability_load", "arguments": {"group": "project"},
        }}]}},
        {"message": {"content": "", "tool_calls": [{"id": "read", "function": {
            "name": "read_file", "arguments": {"path": "hello.txt"},
        }}]}},
        {"message": {"content": "проверенный факт", "tool_calls": []}},
    ])

    def chat(**kwargs):
        calls.append(deepcopy({key: kwargs.get(key) for key in ("messages", "tools")}))
        return next(responses)

    events = list(stream_code_agent(
        user_message="Что в hello.txt?", memory_query="Что в hello.txt?",
        project_root=tmp_path, chat_fn=chat, auto_remember=False, permission_mode="bypass",
        base_tools=["capability_load"],
    ))
    assert names(calls[0]) == {"capability_load", "ask_user", "workflow_request"}
    assert {"read_file", "write_file", "run_bash"} <= names(calls[1])
    assert calls[0]["messages"][0] == calls[1]["messages"][0] == calls[2]["messages"][0]
    assert any(m["role"] == "tool" and "проверенный факт" in m.get("content", "") for m in calls[2]["messages"])
    assert events[-1]["ok"]


@pytest.mark.parametrize("tool", ["web_search", "mcp__test__change_server"])
def test_external_work_receives_verification_guidance(tool):
    from app.application.code_agent.task_guidance import task_guidance_blocks

    blocks = task_guidance_blocks({"capability_load", tool})
    assert "work" in blocks
    assert "проверь результат" in blocks["work"]


@pytest.mark.parametrize("summary_ok", [True, False])
def test_task_guidance_survives_real_loop_compaction(tmp_path, monkeypatch, summary_ok):
    from app.application.code_agent import agent_loop, loop_helpers

    original_prepare = agent_loop._prepare_messages_for_llm

    def force_compaction(messages, **kwargs):
        kwargs["context_profile"] = {
            **kwargs["context_profile"],
            "compaction_thresholds": {
                "auto": {"percent": 0.001}, "strong": {"percent": 0.001},
                "critical": {"percent": 100},
            },
        }
        return original_prepare(messages, **kwargs)

    monkeypatch.setattr(agent_loop, "_prepare_messages_for_llm", force_compaction)
    monkeypatch.setattr(loop_helpers, "summarize_history", lambda *a, **k: {
        "ok": summary_ok, "summary": "История сжата без рабочих инструкций." if summary_ok else "",
    })
    calls = []
    for i in range(4):
        (tmp_path / f"file{i}.txt").write_text(f"Факт {i}", encoding="utf-8")

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        index = len(calls) - 1
        function = (
            {"name": "capability_load", "arguments": {"group": "project"}} if index == 0
            else {"name": "read_file", "arguments": {"path": f"file{index - 1}.txt"}}
        )
        return {"message": {"content": "Готово." if index == 5 else "", "tool_calls": [] if index == 5 else [
            {"id": f"call{index}", "function": function},
        ]}}

    events = list(stream_code_agent(
        user_message="Прочитай четыре файла", memory_query="Прочитай четыре файла",
        project_root=tmp_path, chat_fn=chat, auto_remember=False, permission_mode="bypass",
    ))
    assert any(e["type"] == "context_compacted" for e in events)
    assert len(calls) == 6
    for messages in calls[1:]:
        assert "Сделай минимальный патч" in str(messages)
    assert all(messages[0] == calls[0][0] for messages in calls)


def test_web_work_loads_project_instructions_outside_stable_prefix(tmp_path):
    (tmp_path / ".elira").mkdir()
    (tmp_path / ".elira/agent.md").write_text("PROJECT_REVIEW_MARKER", encoding="utf-8")
    call = capture(tmp_path, "Проверь источник", base_tools=["web_search"])
    assert "PROJECT_REVIEW_MARKER" not in call["messages"][0]["content"]
    assert "PROJECT_REVIEW_MARKER" in str(call["messages"][1:])
    assert "UNTRUSTED INSTRUCTIONS" in str(call["messages"][1:])


def test_wrong_initial_capability_can_recover_and_execute(tmp_path):
    (tmp_path / "proof.txt").write_text("verified recovery", encoding="utf-8")
    functions = iter([
        {"name": "capability_load", "arguments": {"group": "web"}},
        {"name": "capability_load", "arguments": {"group": "missing-group"}},
        {"name": "capability_load", "arguments": {"group": "project"}},
        {"name": "read_file", "arguments": {"path": "proof.txt"}},
    ])
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy({key: kwargs[key] for key in ("messages", "tools")}))
        function = next(functions, None)
        return {"message": {"content": "verified recovery" if function is None else "", "tool_calls": [
            {"id": f"call{len(calls)}", "function": function},
        ] if function else []}}

    events = list(stream_code_agent(
        user_message="Прочти proof.txt", memory_query="Прочти proof.txt",
        project_root=tmp_path, chat_fn=chat, auto_remember=False, permission_mode="bypass",
    ))
    assert len(calls) == 5
    assert all("capability_load" in names(call) for call in calls)
    assert any(m.get("role") == "tool" and "verified recovery" in m.get("content", "") for m in calls[-1]["messages"])
    assert events[-1]["ok"]
