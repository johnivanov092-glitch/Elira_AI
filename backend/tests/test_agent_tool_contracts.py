from __future__ import annotations

import ast
import inspect
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from app.application.code_agent.capabilities import (
    ALL_BUILTIN_TOOLS,
    CAPABILITY_GROUPS,
    CAPABILITY_GROUP_DESCRIPTIONS,
    CORE_BUILTIN_TOOLS,
)
from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.code_agent.tools._computer import tool_computer
from app.application.code_agent.tools._dispatch import build_tool_dispatch
from app.application.code_agent.tools._web import tool_browser
from app.application.tool_registry.builtins import _build_native_code_agent_tools
from app.application.tool_providers.builtin import BuiltinToolProvider


def _schema_names() -> list[str]:
    return [
        str(schema["function"]["name"])
        for schema in build_tool_schemas()
    ]


def _declared_dispatch_names() -> list[str]:
    tree = ast.parse(inspect.getsource(build_tool_dispatch))
    return [
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]


def test_capability_inventory_has_one_schema_owner_and_toolspec(tmp_path: Path) -> None:
    schema_names = _schema_names()
    dispatch_names = _declared_dispatch_names()
    spec_rows = _build_native_code_agent_tools()
    spec_names = [str(spec["name"]) for spec in spec_rows]
    specs = {str(spec["name"]): spec for spec in spec_rows}

    assert not [name for name, count in Counter(schema_names).items() if count != 1]
    assert not [name for name, count in Counter(dispatch_names).items() if count != 1]
    assert not [name for name, count in Counter(spec_names).items() if count != 1]
    assert set(schema_names) == ALL_BUILTIN_TOOLS
    assert set(dispatch_names) == ALL_BUILTIN_TOOLS
    assert set(build_tool_dispatch(tmp_path)) == ALL_BUILTIN_TOOLS
    assert set(spec_names) == ALL_BUILTIN_TOOLS
    assert specs["reconcile_server_facts"]["side_effect"] is True


def test_capability_groups_are_complete_and_non_overlapping() -> None:
    assert set(CAPABILITY_GROUPS) == set(CAPABILITY_GROUP_DESCRIPTIONS)

    assigned = set(CORE_BUILTIN_TOOLS)
    for tools in CAPABILITY_GROUPS.values():
        assert assigned.isdisjoint(tools)
        assigned.update(tools)

    assert assigned == ALL_BUILTIN_TOOLS


def test_every_builtin_owner_returns_the_structured_result_contract(tmp_path: Path) -> None:
    safe_calls = {
        "capability_load": {"group": "__invalid__"},
        "runtime_control": {"operation": "__invalid__"},
        "read_file": {"path": "missing.txt"},
        "write_file": {},
        "edit_file": {},
        "glob": {"pattern": ""},
        "grep": {"pattern": "("},
        "path_exists": {"path": ""},
        "project_map": {"path": "missing-directory"},
        "todo_update": {"run_id": ""},
        "delegate_task": {"run_id": "", "task": ""},
        "run_bash": {"command": ""},
        "run_server": {"action": "__invalid__"},
        "web_search": {"query": ""},
        "web_fetch": {"url": ""},
        "web_query": {"query": ""},
        "web_sitemap": {"url": ""},
        "http_api": {"url": ""},
        "browser": {"url": ""},
        "screenshot": {},
        "computer": {"action": "__invalid__"},
        "resource_process": {},
        "resource_remote_process": {},
        "resource_materialize": {},
        "resource_publish": {},
        "read_image": {},
        "ocr_file": {"path": "missing.png"},
        "file_gen": {"format": "__invalid__"},
        "sandbox_run": {"code": ""},
        "sandbox_reset": {"unexpected": True},
        "translator": {},
        "regex": {},
        "csv": {"file_path": "missing.csv"},
        "bom_validate": {},
        "converter": {"source_path": "missing.bin", "target_format": "txt"},
        "sql": {"action": "__invalid__"},
        "encrypt": {"action": "__invalid__"},
        "archiver": {"action": "__invalid__"},
        "recall": {},
        "remember": {"fact": ""},
        "reconcile_server_facts": {},
        "webhook": {"action": "__invalid__"},
    }
    missing_cases = ALL_BUILTIN_TOOLS - set(safe_calls)
    assert missing_cases == set()
    safe_calls = {
        name: args
        for name, args in safe_calls.items()
        if name in ALL_BUILTIN_TOOLS
    }

    provider = BuiltinToolProvider(tmp_path)
    with patch(
        "app.application.drift.runtime.reconcile",
        return_value={"reachable": False},
    ):
        results = {
            name: provider.dispatch(name, args)
            for name, args in safe_calls.items()
        }

    invalid = {
        name: result
        for name, result in results.items()
        if not isinstance(result.get("ok"), bool)
        or result.get("error") == "invalid_tool_result"
    }
    assert invalid == {}


def test_browser_reports_incomplete_multi_action_sequence() -> None:
    actions = [
        {"fill": "Email", "value": "user@example.com"},
        {"click": "Submit"},
    ]
    with (
        patch("app.application.web.ssrf_guard.check_ssrf", return_value=None),
        patch(
            "app.application.code_agent.tools._web._browser_render",
            return_value=("Form", "https://example.com", "body", 1, None),
        ),
    ):
        result = tool_browser(url="https://example.com", actions=actions)

    assert result["ok"] is False
    assert result["error"] == "browser_actions_incomplete"
    assert result["interacted"] is False


def test_computer_screenshot_requires_a_vision_description(tmp_path: Path) -> None:
    with (
        patch(
            "app.application.code_agent.tools._computer._grab_png",
            return_value=(b"png", (1280, 720), None),
        ),
        patch("app.infrastructure.llm.vision_ocr.is_vision_enabled", return_value=False),
    ):
        result = tool_computer(tmp_path, action="screenshot")

    assert result["ok"] is False
    assert result["error"] == "vision_disabled"
