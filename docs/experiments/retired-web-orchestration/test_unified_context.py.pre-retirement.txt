from __future__ import annotations

from copy import deepcopy

import pytest

from _runtime_roles import base_system, runtime_section, user_texts
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
    assert {"capability_load", "read_file", "web_search", "web_fetch"} <= names(call)
    assert "проверь результат" in str(call["messages"])
    assert "первичные источники" in str(call["messages"])
    assert len(base_system(call["messages"])) <= 3000
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
    # The stable persona prompt is shared; recalled memory is runtime context in
    # the system section, never text in the owner's name.
    assert base_system(a["messages"]) == base_system(b["messages"])
    assert "Лолита" not in base_system(a["messages"])
    assert "Лолита — жена пользователя." in runtime_section(a["messages"])
    assert not any("Лолита — жена" in text for text in user_texts(a["messages"]))
    assert "Лолита — жена пользователя." not in str(b["messages"])
    assert seen == ["Кто такая Лолита?", "Проверь файл"]


def test_current_message_follows_context_without_tone_or_prefix_change(tmp_path):
    history = [{"role": "user", "content": "Расскажи про вечер."},
               {"role": "assistant", "content": "За окном тихо."}]
    query = "Спасибо, на этом пока всё."
    calls = []
    for _ in range(2):
        call = capture(tmp_path, query, conversation_history=history)
        calls.append(call)
        # The user role carries the owner's text verbatim (decision 2026-10-06).
        assert call["messages"][-1] == {"role": "user", "content": query}
        assert "[Текущий тон Elira]" not in str(call["messages"])
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
    assert base_system(calls[0]["messages"]) == base_system(calls[1]["messages"]) == base_system(calls[2]["messages"])
    assert any(m["role"] == "tool" and "проверенный факт" in m.get("content", "") for m in calls[2]["messages"])
    assert events[-1]["ok"]


@pytest.mark.parametrize("tool", ["write_file", "mcp__test__change_server", "file_gen"])
def test_external_work_receives_verification_guidance(tool):
    from app.application.code_agent.task_guidance import task_guidance_blocks

    blocks = task_guidance_blocks({"capability_load", tool})
    assert "work" in blocks
    assert "проверь результат" in blocks["work"]


def test_search_starts_with_retrieval_and_loads_work_contract_only_when_needed():
    from app.api.routes.code_agent_routes import _base_tools_for_mode
    from app.application.code_agent.task_guidance import task_guidance_blocks

    tools = set(_base_tools_for_mode("search"))
    assert tools == {"capability_load", "web_search", "web_fetch"}
    assert set(task_guidance_blocks(tools)) == {"web"}
    assert set(task_guidance_blocks(tools | {"browser", "web_query", "http_api"})) == {"web"}
    assert "work" in task_guidance_blocks(tools | {"write_file"})
    assert _base_tools_for_mode("code") is None


@pytest.mark.parametrize("summary_ok", [True, False])
@pytest.mark.parametrize("work_started", [True, False])
def test_task_guidance_survives_real_loop_compaction(tmp_path, monkeypatch, summary_ok, work_started):
    from app.application.code_agent import agent_loop, loop_helpers
    from app.application.code_agent.task_guidance import task_guidance_blocks

    original_prepare = agent_loop._prepare_messages_for_llm
    guidance = task_guidance_blocks({"read_file"})
    work_guidance = guidance.pop("work")

    def force_compaction(messages, **kwargs):
        texts = [*guidance.values(), *([work_guidance] if work_started and calls else [])]
        for text in texts:
            owner = [message for message in messages if text in message.get("content", "")]
            assert len(owner) == 1
            assert owner[0]["_msg_id"] in kwargs["pinned_message_ids"]
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
            ({"name": "project_map", "arguments": {}} if work_started else
             {"name": "capability_load", "arguments": {"group": "project"}}) if index == 0
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
        for text in guidance.values():
            assert sum(text in message.get("content", "") for message in messages) == 1
        assert sum(work_guidance in message.get("content", "") for message in messages) == int(work_started)
    assert all(base_system(messages) == base_system(calls[0]) for messages in calls)


def test_user_confirmation_guidance_is_pinned_before_work_and_restored_on_resume(tmp_path, monkeypatch):
    from app.application.code_agent.agent_loop import request_cancel
    from app.application.code_agent.delivery_session import build_continuation_kwargs
    from app.application.code_agent.task_guidance import task_guidance_blocks

    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "state.txt").write_text("observed-before-stop", encoding="utf-8")
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        content = runtime_section(kwargs["messages"])
        assert content.count("[Инструкции текущей задачи]") == 1
        assert "«Установить сейчас» или «Позже»" in content
        assert "Не вызывай confirm за пользователя и не имитируй его нажатие" in content
        assert "проверено, ожидает пользователя" in content
        assert "без нового диалога" in content
        return {"message": {"content": "Прочитано." if len(calls) > 1 else "", "tool_calls": [] if len(calls) > 1 else [{
            "id": "read-before-stop", "function": {"name": "read_file", "arguments": {"path": "state.txt"}},
        }]}}

    run_id = "update-guidance-resume"
    initial = []
    for event in stream_code_agent(
        user_message="Прочитай состояние перед обновлением Elira.", project_root=tmp_path,
        run_id=run_id, chat_fn=chat, base_tools=["read_file"], auto_remember=False,
        permission_mode="bypass",
    ):
        initial.append(event)
        if event["type"] == "tool_call" and event.get("tool") == "read_file":
            assert event["ok"]
            assert "observed-before-stop" in str(event.get("result"))
            assert request_cancel(run_id)
    assert initial[-1]["stop_reason"] == "cancelled"
    resumed = list(stream_code_agent(**build_continuation_kwargs(run_id, chat_fn=chat)))
    assert resumed[-1]["ok"] and len(calls) == 2
    assert base_system(calls[0]) == base_system(calls[1])
    guidance = task_guidance_blocks({"read_file"})
    work_guidance = guidance.pop("work")
    assert all(work_guidance not in str(messages) for messages in calls)
    for text in guidance.values():
        for messages in calls:
            # Pinned guidance reaches the model once, in the runtime section.
            assert runtime_section(messages).count(text) == 1
            assert not any(text in user_text for user_text in user_texts(messages))
    assert any(event["type"] == "run_resumed" and event["from_step"] == 1 for event in resumed)
    assert not any(event["type"] == "tool_started" for event in resumed)


def test_web_work_loads_project_instructions_outside_stable_prefix(tmp_path):
    (tmp_path / ".elira").mkdir()
    (tmp_path / ".elira/agent.md").write_text("PROJECT_REVIEW_MARKER", encoding="utf-8")
    call = capture(tmp_path, "Проверь источник", base_tools=["web_search"])
    # Project-file text stays outside the stable persona prompt and keeps its
    # untrusted label; it is runtime context, never text in the owner's name.
    assert "PROJECT_REVIEW_MARKER" not in base_system(call["messages"])
    assert "PROJECT_REVIEW_MARKER" in runtime_section(call["messages"])
    assert "UNTRUSTED INSTRUCTIONS" in runtime_section(call["messages"])
    assert not any("PROJECT_REVIEW_MARKER" in text for text in user_texts(call["messages"]))


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


def test_late_web_activation_promotes_only_runtime_contract_to_first_system(tmp_path, monkeypatch):
    import re

    from app.application.code_agent.task_guidance import WEB_SOURCE_FIDELITY_GUIDANCE
    from webskill.application.code_agent.tools import _web
    from app.infrastructure.llm.openai_compatible import _normalize_messages_for_request
    from webskill.infrastructure.search.web_runtime import PageFetchResult

    url = "https://example.org/report"
    user_marker = "USER_REQUEST_NOT_SYSTEM"
    history_marker = "ASSISTANT_HISTORY_NOT_SYSTEM"
    excerpt = "The report measured 17 observations. EXCERPT_NOT_SYSTEM."
    calls, reads = [], []

    def fetch(actual_url, limit):
        reads.append(actual_url)
        return PageFetchResult(text=excerpt, final_url=actual_url)

    monkeypatch.setattr(_web, "_fetch_one", fetch)

    def chat(**kwargs):
        snapshot = deepcopy({key: kwargs[key] for key in ("messages", "tools")})
        # The transport injects a lock-bearing cancellation handle per request;
        # sampling/template options remain the values compared across turns.
        snapshot["options"] = deepcopy({key: value for key, value in kwargs["options"].items()
                                        if key != "_stream_cancel_handle"})
        calls.append(snapshot)
        index = len(calls)
        assert sum(message.get("role") == "system" for message in kwargs["messages"]) == 1
        system = kwargs["messages"][0]
        assert system["role"] == "system"
        assert not any(value in base_system(kwargs["messages"]) for value in (user_marker, history_marker, excerpt))
        assert not any(value in runtime_section(kwargs["messages"]) for value in (history_marker, excerpt))
        if index == 1:
            assert "capability_load" in names(calls[-1])
            assert not {"web_search", "web_fetch"} & names(calls[-1])
            assert WEB_SOURCE_FIDELITY_GUIDANCE not in system["content"]
            function = {"name": "capability_load", "arguments": {"group": "web"}}
            call_id = "web-activate"
        else:
            assert base_system(kwargs["messages"]) == base_system(calls[0]["messages"])
            assert system["content"].count(WEB_SOURCE_FIDELITY_GUIDANCE) == 1
            assert {"capability_load", "web_search", "web_fetch"} <= names(calls[-1])
            if index == 2:
                function = {"name": "web_fetch", "arguments": {"url": url}}
                call_id = "web-read"
            else:
                assert index == 3
                read = next(message["content"] for message in kwargs["messages"]
                            if message.get("role") == "tool" and message.get("name") == "web_fetch")
                assert excerpt in read
                marker = re.search(r"\[\[source:[^\]]+\]\]", read).group()
                return {"message": {"content": "В отчёте зарегистрированы 17 наблюдений. " + marker}}
        return {"message": {"content": "", "tool_calls": [{"id": call_id, "function": function}]}}

    events = list(stream_code_agent(user_message=f"Прочитай {url} и объясни результат. {user_marker}",
        conversation_history=[{"role": "user", "content": "Предыдущий вопрос."},
                              {"role": "assistant", "content": history_marker}],
        project_root=tmp_path, chat_fn=chat, base_tools=["capability_load"],
        num_ctx=65536, permission_mode="bypass", auto_remember=False))
    assert len(calls) == 3 and reads == [url]
    assert calls[1]["messages"][0] == calls[2]["messages"][0]
    assert calls[1]["tools"] == calls[2]["tools"]
    assert calls[0]["options"] == calls[1]["options"] == calls[2]["options"]
    assert all(any(message.get("content") == history_marker for message in call["messages"])
               for call in calls)
    wire = _normalize_messages_for_request(calls[2]["messages"])
    assert wire[0]["role"] == "system" and sum(message["role"] == "system" for message in wire) == 1
    assert [call["id"] for message in wire for call in message.get("tool_calls", [])] == ["web-activate", "web-read"]
    assert [message["tool_call_id"] for message in wire if message["role"] == "tool"] == ["web-activate", "web-read"]
    assert [event["tool"] for event in events if event["type"] == "tool_call"] == ["capability_load", "web_fetch"]
    assert next(event for event in events if event["type"] == "final_response")["source_status"] == "matched"
    assert events[-1]["stop_reason"] == "answer" and events[-1]["ok"]
