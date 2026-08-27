from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.capabilities import (
    CAPABILITY_GROUPS,
    CORE_BUILTIN_TOOLS,
    PROFILE_CAPABILITY_GROUPS,
    builtin_tools_for_groups,
    route_request_capabilities,
)
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.code_agent.tools._capability import tool_capability_load
from app.application.chat.local_chat import resolve_persona_mode
from app.application.tool_providers.runtime_registry import build_runtime_tool_registry
from app.core.persona_defaults import AUTO_PROFILE, PERSONA_MODES


def _tool_names(registry) -> set[str]:
    return {
        str((schema.get("function") or {}).get("name") or "")
        for schema in registry.collect_schemas()
    }


def test_initial_registry_exposes_core_but_not_deferred_web_tools(tmp_path) -> None:
    registry = build_runtime_tool_registry(
        tmp_path,
        builtin_tool_names=builtin_tools_for_groups(()),
        mcp_server_ids=(),
        lsp_server_ids=(),
        include_ssh=False,
        include_itops=False,
    )

    names = _tool_names(registry)
    assert {
        "capability_load", "runtime_control", "read_file", "run_bash", "run_server",
    } <= names
    assert {"web_search", "web_fetch", "browser", "computer"}.isdisjoint(names)

    hidden_result = registry.dispatch_raw(
        "regex",
        {"pattern": "a+", "text": "caa"},
    )
    assert "unknown tool" not in str(hidden_result.get("text") or "").lower()


def test_model_loads_web_group_for_next_turn(tmp_path) -> None:
    seen_tool_names: list[set[str]] = []
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "capability_load",
                        "arguments": {"group": "web"},
                    },
                }],
            },
        },
        {"message": {"content": "Готово.", "tool_calls": []}},
    ])

    def fake_chat(**kwargs):
        seen_tool_names.append(_tool_names_from_schemas(kwargs.get("tools") or []))
        return next(responses)

    events = list(stream_code_agent(
        user_message="Выполни локальную задачу",
        project_root=tmp_path,
        run_id="capability-load-web-next-turn",
        chat_fn=fake_chat,
        auto_remember=False,
        permission_mode="ask",
    ))

    assert len(seen_tool_names) >= 2
    assert "capability_load" in seen_tool_names[0]
    assert "web_search" not in seen_tool_names[0]
    assert {"web_search", "web_fetch", "browser"} <= seen_tool_names[1]
    assert "computer" not in seen_tool_names[1]
    load_events = [
        event for event in events
        if event.get("type") == "tool_call"
        and event.get("tool") == "capability_load"
    ]
    assert load_events[-1]["runtime_activation"]["capability_groups"] == ["web"]

    resumed_tool_names: list[set[str]] = []

    def resumed_chat(**kwargs):
        resumed_tool_names.append(
            _tool_names_from_schemas(kwargs.get("tools") or [])
        )
        return {"message": {"content": "Продолжаю.", "tool_calls": []}}

    list(stream_code_agent(
        user_message="Продолжай",
        project_root=tmp_path,
        run_id="capability-load-web-next-turn",
        chat_fn=resumed_chat,
        auto_remember=False,
        permission_mode="ask",
        resume=True,
    ))

    assert {"web_search", "web_fetch", "browser"} <= resumed_tool_names[0]
    assert "computer" not in resumed_tool_names[0]


def test_planner_preloads_selected_group_before_first_execution_turn(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    seen_execution_tools: list[set[str]] = []
    planner_reply = {
        "goal": "Создать и проверить страницу",
        "current_state": "Проект существует",
        "ordered_steps": ["Изменить страницу", "Проверить её в browser"],
        "acceptance_checks": ["browser(actual_url) показывает страницу"],
        "risks": [],
        "capability_groups": ["web"],
        "current_step": 1,
    }
    responses = iter([
        {"message": {"content": json.dumps(planner_reply), "tool_calls": []}},
        {"message": {"content": "Готово.", "tool_calls": []}},
    ])

    def fake_chat(**kwargs):
        if kwargs.get("tools"):
            seen_execution_tools.append(
                _tool_names_from_schemas(kwargs.get("tools") or [])
            )
        return next(responses)

    events = list(stream_code_agent(
        user_message=(
            "Цель: создать страницу.\n"
            "Критерии готовности:\n"
            "- browser открывает страницу и показывает текст `Готово`"
        ),
        project_root=tmp_path,
        run_id="planner-preloads-web",
        chat_fn=fake_chat,
        auto_remember=False,
        permission_mode="ask",
        reasoning_effort="medium",
    ))

    assert seen_execution_tools
    assert {"browser", "screenshot", "web_search"} <= seen_execution_tools[0]
    activation = next(
        event for event in events
        if event.get("type") == "runtime_activation_changed"
    )
    assert activation["source"] == "planner"
    assert activation["runtime_activation"]["capability_groups"] == ["web"]
    assert not any(
        event.get("type") == "tool_call" and event.get("tool") == "capability_load"
        for event in events
    )


def test_group_loading_does_not_expose_unrelated_groups(tmp_path) -> None:
    registry = build_runtime_tool_registry(
        tmp_path,
        builtin_tool_names=builtin_tools_for_groups(("resources",)),
        mcp_server_ids=(),
        lsp_server_ids=(),
        include_ssh=False,
        include_itops=False,
    )

    names = _tool_names(registry)
    assert {"resource_process", "read_image", "file_gen"} <= names
    assert {"web_search", "computer", "sql", "remember"}.isdisjoint(names)


def test_inline_existing_builtin_is_callable_without_loading_its_schema(tmp_path) -> None:
    seen_tool_names: list[set[str]] = []
    responses = iter([
        {
            "message": {
                "content": (
                    '{"name":"regex","arguments":'
                    '{"pattern":"a+","text":"caa"}}'
                ),
                "tool_calls": [],
            },
        },
        {"message": {"content": "Совпадение найдено.", "tool_calls": []}},
    ])

    def fake_chat(**kwargs):
        seen_tool_names.append(_tool_names_from_schemas(kwargs.get("tools") or []))
        return next(responses)

    events = list(stream_code_agent(
        user_message="Проверь регулярное выражение",
        project_root=tmp_path,
        run_id="capability-hidden-inline-call",
        chat_fn=fake_chat,
        auto_remember=False,
        permission_mode="ask",
    ))

    assert "regex" not in seen_tool_names[0]
    assert any(
        event.get("type") == "tool_call"
        and event.get("tool") == "regex"
        and event.get("ok") is True
        for event in events
    )


def test_backgrounded_ssh_pid_reaches_the_model_and_event_stream(tmp_path) -> None:
    model_contexts: list[list[dict]] = []
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "ssh_run_ps",
                        "arguments": {
                            "host": "media-server",
                            "script": "Start-Process jellyfin_setup.exe -Wait",
                        },
                    },
                }],
            },
        },
        {"message": {"content": "Фоновая задача запущена.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "run_server",
                        "arguments": {"action": "logs", "pid": 4242},
                    },
                }],
            },
        },
        {"message": {"content": "Jellyfin установлен.", "tool_calls": []}},
    ])

    def fake_chat(**kwargs):
        model_contexts.append(json.loads(json.dumps(
            kwargs["messages"],
            ensure_ascii=False,
        )))
        return next(responses)

    started = {
        "ok": True,
        "status": "running",
        "kind": "job",
        "pid": 4242,
        "text": "Job started in background.",
    }
    with patch(
        "app.application.code_agent.tools._run.start_background_argv_job",
        return_value=started,
    ), patch(
        "app.application.code_agent.tools._dispatch.tool_run_server",
        return_value={
            "ok": True,
            "status": "completed",
            "kind": "job",
            "pid": 4242,
            "exit_code": 0,
            "text": "SSH job completed successfully.",
        },
    ):
        events = list(stream_code_agent(
            user_message="Установи Jellyfin по SSH на media-server.",
            project_root=tmp_path,
            run_id="ssh-background-feedback",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
            profile_name="Инфраструктура",
        ))

    assert len(model_contexts) >= 4
    assert "run_server(action='logs', pid=4242)" in model_contexts[1][-1]["content"]
    assert '"backgrounded": true' in model_contexts[1][-1]["content"]
    assert "Задачу нельзя завершать" in model_contexts[2][-1]["content"]
    assert "SSH job completed successfully" in model_contexts[3][-1]["content"]
    ssh_event = next(
        event for event in events
        if event.get("type") == "tool_call"
        and event.get("tool") == "ssh_run_ps"
    )
    assert ssh_event["ok"] is True
    assert ssh_event["status"] == "running"
    assert ssh_event["backgrounded"] is True
    assert ssh_event["redirected_from"] == "ssh_run_ps"
    logs_event = next(
        event for event in events
        if event.get("type") == "tool_call"
        and event.get("tool") == "run_server"
    )
    assert logs_event["status"] == "completed"


def test_every_builtin_schema_is_reachable_from_core_or_one_group() -> None:
    schema_names = _tool_names_from_schemas(build_tool_schemas())
    grouped_names: set[str] = set()
    for group_names in CAPABILITY_GROUPS.values():
        assert grouped_names.isdisjoint(group_names)
        assert CORE_BUILTIN_TOOLS.isdisjoint(group_names)
        grouped_names.update(group_names)

    assert schema_names == set(CORE_BUILTIN_TOOLS) | grouped_names


def test_memory_tool_schemas_distinguish_project_rag_from_user_memory() -> None:
    schemas = {
        schema["function"]["name"]: schema["function"]
        for schema in build_tool_schemas()
    }

    recall_description = schemas["recall"]["description"]
    runtime_description = schemas["runtime_control"]["description"]
    assert "not long-term user memory" in recall_description
    assert "memory_search" in runtime_description
    assert "memory_list" in runtime_description
    operations = schemas["runtime_control"]["parameters"]["properties"]["operation"]["enum"]
    assert "project_index" in operations
    assert "project_status" in operations


def test_unknown_capability_group_fails_without_changing_visibility() -> None:
    result = tool_capability_load(group="not-a-group")

    assert result["ok"] is False
    assert result["error"] == "unknown_capability_group"


def test_auto_routes_every_domain_to_relevant_starter_tools(tmp_path) -> None:
    route_cases = (
        ("Мне тревожно, поговори со мной", "Личный"),
        ("Объясни, почему небо голубое простыми словами", "Баланс"),
        ("Проверь код и исправь баг в проекте", "Инженерный"),
        ("Составь коммерческое предложение и PDF для клиента", "Деловой"),
        ("Проверь TCP-порты 22 и 443 на 192.168.88.15", "Инфраструктура"),
        ("Объясни квантовую запутанность со ссылками", "Научный"),
        ("Какие симптомы бывают при пневмонии", "Медицина"),
    )
    assert set(PROFILE_CAPABILITY_GROUPS) == set(PERSONA_MODES)

    for profile_index, (message, expected_profile) in enumerate(route_cases):
        seen_tool_names: list[set[str]] = []
        seen_system_prompts: list[str] = []

        def fake_chat(**kwargs):
            seen_tool_names.append(
                _tool_names_from_schemas(kwargs.get("tools") or [])
            )
            seen_system_prompts.append(str(kwargs["messages"][0]["content"]))
            return {"message": {"content": "Проверка завершена.", "tool_calls": []}}

        effective_profile = resolve_persona_mode(AUTO_PROFILE, message)
        assert effective_profile == expected_profile
        request_route = route_request_capabilities(
            message,
            domain_policy=effective_profile,
        )
        events = list(stream_code_agent(
            user_message=message,
            project_root=tmp_path,
            run_id=f"persona-tool-route-{profile_index}",
            chat_fn=fake_chat,
            auto_remember=False,
            profile_name=effective_profile,
            permission_mode="ask",
        ))

        assert builtin_tools_for_groups(
            PROFILE_CAPABILITY_GROUPS[expected_profile]
        ) <= seen_tool_names[0]
        assert (
            "itops_network_inventory" in seen_tool_names[0]
        ) is (expected_profile == "Инфраструктура")
        assert (
            "ssh_run" in seen_tool_names[0]
        ) is (expected_profile == "Инфраструктура")
        assert "runtime_control" in seen_tool_names[0]
        assert "Не запускай все MCP автоматически" in seen_system_prompts[0]
        assert "не запускай последовательные `Test-NetConnection`" in seen_system_prompts[0]
        run_started = next(event for event in events if event["type"] == "run_started")
        assert run_started["profile_name"] == expected_profile
        assert run_started["ui_profile_name"] == "Elira / Auto"
        assert expected_profile in run_started["domain_policies"]
        assert run_started["runtime_activation"]["capability_groups"] == sorted(
            request_route.capability_groups
        )
        assert run_started["runtime_activation"]["itops"] is (
            expected_profile == "Инфраструктура"
        )


def test_auto_routes_an_explicit_absolute_filesystem_path_to_engineering() -> None:
    assert resolve_persona_mode(
        AUTO_PROFILE,
        r"Проект не подключён. Прочитай D:\Data\sample\README.md по абсолютному пути.",
    ) == "Инженерный"


def test_auto_routes_explicit_ssh_commands_to_infrastructure() -> None:
    assert resolve_persona_mode(
        AUTO_PROFILE,
        "Подключись по SSH к серверу и выполни `uname -s`.",
    ) == "Инфраструктура"
    assert resolve_persona_mode(
        AUTO_PROFILE,
        "Подключись к Windows SSH-хосту и выполни `Write-Output OK`.",
    ) == "Инфраструктура"


def test_auto_keeps_ssh_client_code_work_in_engineering() -> None:
    assert resolve_persona_mode(
        AUTO_PROFILE,
        "Исправь баг в Python SSH-клиенте и добавь тест.",
    ) == "Инженерный"


def test_auto_routes_developer_integrations_to_engineering() -> None:
    for message in (
        "Проверь подключение открытого редактора Unity.",
        "Получи статус Blender через интеграцию.",
        "Найди репозиторий через GitHub.",
    ):
        assert resolve_persona_mode(AUTO_PROFILE, message) == "Инженерный"


def test_auto_routes_a_user_memory_request_to_personal() -> None:
    assert resolve_persona_mode(
        AUTO_PROFILE,
        "Сохрани важный факт в долговременную память и затем вспомни его.",
    ) == "Личный"


def test_auto_routes_an_explicit_scientific_research_request_to_science() -> None:
    assert resolve_persona_mode(
        AUTO_PROFILE,
        "Проведи научную проверку страницы https://example.com и приведи источник.",
    ) == "Научный"


def test_legacy_explicit_profile_no_longer_locks_auto_route() -> None:
    assert resolve_persona_mode("Медицина", "Исправь баг в коде") == "Инженерный"
    history = [
        {"role": "user", "content": "Просканируй TCP-порты в локальной сети"},
        {"role": "assistant", "content": "Начинаю диагностику."},
    ]
    assert resolve_persona_mode(AUTO_PROFILE, "Продолжай", history) == "Инфраструктура"


def test_profile_preloaded_itops_survives_resume(tmp_path) -> None:
    run_id = "itops-profile-resume"

    def first_chat(**_kwargs):
        return {"message": {"content": "Первый ход завершён.", "tool_calls": []}}

    list(stream_code_agent(
        user_message="Проверь порт 22 на 192.168.88.15",
        project_root=tmp_path,
        run_id=run_id,
        chat_fn=first_chat,
        auto_remember=False,
        profile_name="Инфраструктура",
        permission_mode="ask",
    ))

    resumed_tool_names: list[set[str]] = []

    def resumed_chat(**kwargs):
        resumed_tool_names.append(
            _tool_names_from_schemas(kwargs.get("tools") or [])
        )
        return {"message": {"content": "Продолжаю.", "tool_calls": []}}

    list(stream_code_agent(
        user_message="Продолжай",
        project_root=tmp_path,
        run_id=run_id,
        chat_fn=resumed_chat,
        auto_remember=False,
        profile_name="Инфраструктура",
        permission_mode="ask",
        resume=True,
    ))

    assert "itops_network_inventory" in resumed_tool_names[0]


def test_request_base_tools_reach_the_actual_first_turn_registry(tmp_path) -> None:
    seen_tool_names: list[set[str]] = []

    def fake_chat(**kwargs):
        seen_tool_names.append(_tool_names_from_schemas(kwargs.get("tools") or []))
        return {"message": {"content": "Готово.", "tool_calls": []}}

    list(stream_code_agent(
        user_message="Найди это в режиме поиска",
        project_root=tmp_path,
        run_id="request-base-tools-registry",
        chat_fn=fake_chat,
        auto_remember=False,
        base_tools=(*CORE_BUILTIN_TOOLS, "web_search", "web_fetch"),
        profile_name="Баланс",
        permission_mode="ask",
    ))

    assert {"web_search", "web_fetch"} <= seen_tool_names[0]
    assert "browser" not in seen_tool_names[0]


def _tool_names_from_schemas(schemas) -> set[str]:
    return {
        str((schema.get("function") or {}).get("name") or "")
        for schema in schemas
    }
