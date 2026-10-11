"""Invalid calls stop before dispatch; runtime-owned kwargs stay out of model input."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
from app.application.agent_kernel.builtin_arguments import _errors, _schemas
from app.application.agent_kernel.runtime_context import current_runtime_context
from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.code_agent.capabilities import ALL_BUILTIN_TOOLS
from app.application.code_agent.loop_helpers import _ASK_USER_SCHEMA, _WORKFLOW_REQUEST_SCHEMA
from app.application.code_agent.tools import build_tool_dispatch
from app.application.tool_registry.builtins import build_builtin_tools
from app.application.tool_providers.builtin import BuiltinToolProvider


VALID_CALLS = [
    ("web_search", {"queries": ["Python enumerate documentation", "Python enumerate examples"], "audience": "global", "mode": "quick"}),
    ("web_fetch", {"urls": ["https://example.org"]}),
    ("capability_load", {"group": "memory"}), ("mcp", {"action": "list"}),
    ("read_file", {"path": "note.txt"}), ("write_file", {"path": "note.txt", "content": "текст"}),
    ("edit_file", {"path": "note.txt", "old_string": "текст", "new_string": "новый"}),
    ("glob", {"pattern": "*.txt"}), ("grep", {"pattern": "текст"}),
    ("resource_process", {"resource_id": "from-chat", "operation": "inspect"}),
    ("resource_materialize", {"resource_id": "from-chat", "destination_name": "input.txt"}),
    ("resource_publish", {"project_path": "note.txt"}),
    ("project_map", {}), ("recall", {"action": "status"}),
    ("memory", {"action": "search", "query": "тест"}), ("library", {"action": "search"}),
    ("todo_update", {"items": [{"id": "one", "text": "сделать", "status": "pending"}]}),
    ("delegate_task", {"role": "explore", "task": "Inspect note.txt"}),
    ("run_bash", {"command": "echo test"}), ("run_server", {"action": "list"}),
    ("math", {"request": {"operation": "evaluate", "expression": "2+2"}}),
    ("ask_user", {"question": "Как продолжить?"}),
    ("workflow_request", {"kind": "input", "message": "Введи данные.", "schema": {}}),
]


def _request(name, args, **options):
    return ToolExecutionRequest(run_id="validation-run", agent_id="code-agent", project_scope_id="",
                                tool_name=name, args=args, source="code_agent",
                                permission_mode=options.pop("permission_mode", "bypass"), **options)


@pytest.mark.parametrize("name,args", VALID_CALLS)
def test_all_builtin_valid_calls_reach_dispatch_unchanged(name, args):
    calls = []
    original = deepcopy(args)
    result = execute_tool(_request(name, args), lambda tool, data: calls.append((tool, data)) or {"ok": True})
    assert result.status == "ok"
    assert calls == [(name, original)] and args == original


@pytest.mark.parametrize("name,args", VALID_CALLS)
def test_unknown_field_returns_help_before_dispatch_or_approval(name, args, monkeypatch):
    calls = []
    monkeypatch.setattr("app.application.agent_kernel.executor._emit_executed",
                        lambda request, output, status: calls.append((request.tool_name, status)))
    result = execute_tool(_request(name, {**args, "unexpected": "ignored before"}, permission_mode="ask"),
                          lambda *_: pytest.fail("invalid call dispatched"))
    assert result.status == "error" and result.error == "invalid_tool_arguments"
    assert "unexpected" in result.output["text"] and "Пример аргументов" in result.output["text"]
    assert calls == [(name, "error")]


@pytest.mark.parametrize("name,args", [
    ("web_search", {"queries": ["test"], "audience": "global", "mode": "unlimited"}),
    ("capability_load", {"group": "unknown"}), ("mcp", {"action": "create"}),
    ("resource_process", {"resource_id": "x", "operation": "read"}),
    ("resource_process", {"resource_id": "x", "operation": "inspect", "execution_target": "gpu"}),
    ("recall", {"action": "create"}), ("memory", {"action": "save"}),
    ("library", {"action": "delete"}), ("run_server", {"action": "restart"}),
    ("run_server", {"action": "list", "kind": "daemon"}),
    ("delegate_task", {"task": "read", "role": "coder"}),
    ("todo_update", {"updates": [{"id": "one", "status": "done"}]}),
])
def test_invalid_enum_never_dispatches(name, args):
    result = execute_tool(_request(name, args), lambda *_: pytest.fail("invalid enum dispatched"))
    assert result.error == "invalid_tool_arguments" and "допустимо" in result.output["text"]


@pytest.mark.parametrize("name,args", [
    ("read_file", {}), ("read_file", {"path": "note.txt", "offset": True}),
    ("web_fetch", {"urls": []}), ("web_fetch", {"urls": ["https://example.org"] * 9}),
    ("web_search", {"queries": "test", "audience": "global", "mode": "quick"}),
    ("todo_update", {"updates": [{"id": "one", "tags": []}]}),
    ("memory", {"action": "create", "text": "test", "tags": []}),
    ("write_file", {"path": "note.txt", "content": None}),
    ("math", {"request": []}), ("write_file", []),
])
def test_malformed_types_arrays_and_nested_fields_do_not_execute(name, args):
    result = execute_tool(_request(name, args), lambda *_: pytest.fail("malformed call dispatched"))
    assert result.status == "error" and result.error == "invalid_tool_arguments"


def test_schema_matrix_covers_every_native_builtin_and_special_branch(tmp_path):
    native_schemas = {schema["function"]["name"]: schema["function"]["parameters"]
                      for schema in build_tool_schemas()}
    special_schemas = {schema["function"]["name"]: schema["function"]["parameters"]
                       for schema in (_ASK_USER_SCHEMA, _WORKFLOW_REQUEST_SCHEMA)}
    dispatch_names = set(build_tool_dispatch(tmp_path))
    registry_names = {tool["name"] for tool in build_builtin_tools()}
    assert dispatch_names == registry_names == set(ALL_BUILTIN_TOOLS) == set(native_schemas)
    schemas = {**native_schemas, **special_schemas}
    assert set(schemas) == dispatch_names | set(special_schemas) == {name for name, _ in VALID_CALLS}
    assert _schemas() == schemas
    assert all(schema["additionalProperties"] is False for schema in schemas.values())
    assert "additionalProperties" not in schemas["math"]["properties"]["request"]
    assert "additionalProperties" not in schemas["mcp"]["properties"]["config"]


@pytest.mark.parametrize("count", [0, 101])
def test_published_numeric_bounds_reject_before_dispatch_or_approval(count):
    result = execute_tool(_request("resource_publish", {"project_path": "result.docx", "expected_page_count": count},
                                   permission_mode="ask"),
                          lambda *_: pytest.fail("out-of-bounds count dispatched"))
    assert result.status == "error" and result.error == "invalid_tool_arguments"
    assert "expected_page_count" in result.output["text"]


@pytest.mark.parametrize("count", [1, 100])
def test_published_numeric_boundaries_accept_exact_values(count):
    seen = []
    args = {"project_path": "result.docx", "expected_page_count": count}
    result = execute_tool(_request("resource_publish", args),
                          lambda name, data: seen.append((name, data)) or {"ok": True})
    assert result.status == "ok" and seen == [("resource_publish", args)]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_number_schema_rejects_non_json_numbers(value):
    assert _errors(value, {"type": "number"})


def test_dynamic_skill_payload_and_mcp_tools_keep_provider_owned_arguments():
    calls = []
    for name, args in [("math", {"request": {"operation": "plot", "custom_skill_option": {"axis": "x"}}}),
                       ("paper_search__search_arxiv", {"query": "agents", "server_option": True})]:
        result = execute_tool(_request(name, args), lambda tool, data: calls.append((tool, data)) or {"ok": True})
        assert result.status == "ok" and calls[-1] == (name, args)


@pytest.mark.parametrize("name", ["todo_update", "delegate_task", "resource_publish"])
@pytest.mark.parametrize("key", ["run_id", "permission_mode", "_runtime_refuse_reason"])
def test_model_cannot_supply_trusted_runtime_fields(name, key):
    args = dict(next(args for tool, args in VALID_CALLS if tool == name))
    result = execute_tool(_request(name, {**args, key: "spoofed"}), lambda *_: pytest.fail("spoofed call dispatched"))
    assert result.error == "invalid_tool_arguments"


@pytest.mark.parametrize("name", ["todo_update", "delegate_task", "resource_publish"])
def test_trusted_glue_reaches_handler_after_validation(tmp_path, name):
    provider = BuiltinToolProvider(tmp_path)
    seen = []
    provider._dispatch_table[name] = lambda **kwargs: seen.append((kwargs, dict(current_runtime_context()))) or {"ok": True}
    args = dict(next(args for tool, args in VALID_CALLS if tool == name))
    result = execute_tool(_request(name, args, runtime_context={"step": "7", "resource_id": "bound-id"}),
                          provider.dispatch)
    assert result.status == "ok"
    handler_args, context = seen[0]
    assert handler_args == {**args, "run_id": "validation-run",
                            **({"permission_mode": "bypass"} if name == "delegate_task" else {})}
    assert context["step"] == "7" and context["resource_id"] == "bound-id"
    assert context["project_root"] == str(tmp_path.resolve())
    assert name in json.loads(context["visible_builtin_tools"])


def test_real_file_and_checklist_calls_preserve_effects(tmp_path):
    provider = BuiltinToolProvider(tmp_path)
    for name, args in [("write_file", {"path": "note.txt", "content": "текст"}),
                       ("edit_file", {"path": "note.txt", "old_string": "текст", "new_string": "новый"}),
                       ("read_file", {"path": "note.txt"}),
                       ("todo_update", {"items": [{"id": "one", "text": "сделать"}]})]:
        assert execute_tool(_request(name, args), provider.dispatch).status == "ok"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "новый"
    result = execute_tool(_request("todo_update", {"updates": [{"id": "missing", "status": "completed"}]}), provider.dispatch)
    assert result.error == "item_not_found" and "one" in result.output["text"]


def test_memory_create_alias_reaches_real_handler(tmp_path, monkeypatch):
    from app.application.code_agent.tools import _memory

    calls = []
    monkeypatch.setattr(_memory, "_add_fact", lambda text, **kwargs: calls.append((text, kwargs)) or {"ok": True})
    result = execute_tool(_request("memory", {"action": "create", "text": "запомни"}), BuiltinToolProvider(tmp_path).dispatch)
    assert result.status == "ok" and calls == [("запомни", {"correction": False, "replaces_id": None})]


def test_memory_alias_cannot_bypass_task_no_memory_policy(tmp_path, monkeypatch):
    from app.application.code_agent import agent_loop

    monkeypatch.setattr(agent_loop, "task_memory_write_allowed", lambda *_: False)
    responses = iter([
        {"message": {"content": "", "tool_calls": [{"id": "m", "function": {
            "name": "memory", "arguments": {"action": "create", "text": "private fact"}}}]}},
        {"message": {"content": "Не сохраняю.", "tool_calls": []}},
    ])
    events = list(agent_loop.stream_code_agent(user_message="Не сохраняй", project_root=tmp_path,
        run_id="validation-no-memory", chat_fn=lambda **_: next(responses), base_tools=["memory"],
        permission_mode="bypass", auto_remember=False))
    call = next(event for event in events if event.get("type") == "tool_call" and event.get("tool") == "memory")
    assert call["error"] == "task_persistence_denied" and call["dispatched"] is False


def test_web_research_budget_metadata_reaches_public_tool_receipt(tmp_path, monkeypatch):
    from app.application.code_agent import agent_loop

    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    budget = {"mode": "deep", "elapsed": 31, "warning": True, "stopped": False, "cache": True}
    monkeypatch.setattr("app.application.code_agent.tools._dispatch.tool_web_fetch",
                        lambda *args, **kwargs: {"ok": True, "text": "Read page", "web_research": budget})
    responses = iter([
        {"message": {"content": "", "tool_calls": [{"id": "web", "function": {
            "name": "web_fetch", "arguments": {"urls": ["https://example.org"], "mode": "deep"}}}]}},
        {"message": {"content": "Прочитано.", "tool_calls": []}},
    ])
    events = list(agent_loop.stream_code_agent(
        user_message="Прочитай страницу.", project_root=tmp_path, run_id="web-budget-receipt",
        chat_fn=lambda **kwargs: next(responses), base_tools=["web_fetch"], auto_remember=False,
        permission_mode="bypass"))
    receipt = next(event for event in events if event.get("type") == "tool_call")
    assert receipt["ok"] is True and receipt["web_research"] == budget
