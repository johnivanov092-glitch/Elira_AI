from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.capabilities import (
    CAPABILITY_GROUPS,
    CORE_BUILTIN_TOOLS,
    builtin_tools_for_groups,
)
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.code_agent.tools._capability import tool_capability_load
from app.application.tool_providers.runtime_registry import build_runtime_tool_registry


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
    assert {"capability_load", "runtime_control", "read_file", "run_bash"} <= names
    assert {"web_search", "web_fetch", "browser", "computer"}.isdisjoint(names)


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


def _tool_names_from_schemas(schemas) -> set[str]:
    return {
        str((schema.get("function") or {}).get("name") or "")
        for schema in schemas
    }
