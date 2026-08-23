from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.capabilities import (
    CAPABILITY_GROUPS,
    CORE_BUILTIN_TOOLS,
    PROFILE_CAPABILITY_GROUPS,
    builtin_tools_for_groups,
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
        user_message="Найди свежую документацию в интернете",
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


def test_every_builtin_schema_is_reachable_from_core_or_one_group() -> None:
    schema_names = _tool_names_from_schemas(build_tool_schemas())
    grouped_names: set[str] = set()
    for group_names in CAPABILITY_GROUPS.values():
        assert grouped_names.isdisjoint(group_names)
        assert CORE_BUILTIN_TOOLS.isdisjoint(group_names)
        grouped_names.update(group_names)

    assert schema_names == set(CORE_BUILTIN_TOOLS) | grouped_names


def test_unknown_capability_group_fails_without_changing_visibility() -> None:
    result = tool_capability_load(group="not-a-group")

    assert result["ok"] is False
    assert result["error"] == "unknown_capability_group"


def test_auto_routes_every_profile_to_its_starter_tools(tmp_path) -> None:
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
        assert "runtime_control" in seen_tool_names[0]
        assert "Не запускай все MCP автоматически" in seen_system_prompts[0]
        assert "не запускай последовательные `Test-NetConnection`" in seen_system_prompts[0]
        run_started = next(event for event in events if event["type"] == "run_started")
        assert run_started["profile_name"] == expected_profile
        assert run_started["runtime_activation"]["capability_groups"] == sorted(
            PROFILE_CAPABILITY_GROUPS[expected_profile]
        )
        assert run_started["runtime_activation"]["itops"] is (
            expected_profile == "Инфраструктура"
        )


def test_explicit_profile_stays_locked_and_auto_followup_keeps_prior_route() -> None:
    assert resolve_persona_mode("Медицина", "Исправь баг в коде") == "Медицина"
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
