"""Work authoring guidance follows actual work, not visible full-machine tools."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.api.routes.code_agent_routes import _base_tools_for_mode
from app.application.code_agent import agent_loop, turn_context
from app.application.code_agent.task_guidance import task_guidance_blocks
from app.application.skills import runtime as skill_runtime


WORK = task_guidance_blocks({"read_file"})["work"]
REMINDER = "[Рабочее напоминание Elira]"


def _reply(name=None, arguments=None, text="Полученные данные: проверено."):
    return {"message": {"content": "" if name else text, "tool_calls": [
        {"id": "operation", "function": {"name": name, "arguments": arguments or {}}}
    ] if name else []}}


def _names(call):
    return {tool["function"]["name"] for tool in call["tools"]}


@pytest.mark.parametrize("retrieval", ["read_file", "http_api"])
def test_full_machine_retrieval_keeps_tools_without_authoring_guidance(tmp_path, monkeypatch, retrieval):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "data.txt").write_text("Проверено: 12", encoding="utf-8")
    monkeypatch.setattr(skill_runtime, "http_request", lambda url, **kwargs: {
        "ok": True, "status": 200, "url": url, "body": {"value": 12}})
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy({key: kwargs[key] for key in ("messages", "tools")}))
        assert WORK not in str(kwargs["messages"]) and REMINDER not in str(kwargs["messages"])
        if retrieval == "http_api" and len(calls) == 1:
            return _reply("capability_load", {"group": "web"})
        if len(calls) == (2 if retrieval == "http_api" else 1):
            return _reply(retrieval, {"path": "data.txt"} if retrieval == "read_file" else {
                "url": "https://example.org/data", "method": "GET"})
        assert "12" in str(kwargs["messages"])
        return _reply(text="Значение: 12.")

    events = list(agent_loop.stream_code_agent(
        user_message="Прочитай данные и покажи значение здесь.", project_root=tmp_path,
        chat_fn=chat, base_tools=_base_tools_for_mode("full-machine"),
        auto_remember=False, permission_mode="bypass", num_ctx=65536))
    assert events[-1]["stop_reason"] == "answer"
    assert len(calls) == (3 if retrieval == "http_api" else 2)
    initial = _names(calls[0])
    assert {"read_file", "write_file", "project_map", "runtime_control", "web_search", "web_fetch"} <= initial
    assert all(initial <= _names(call) for call in calls)
    assert any("Не вызывай confirm за пользователя" in message.get("content", "")
               for message in calls[0]["messages"])
    assert not any(event.get("tool") in {"write_file", "edit_file", "runtime_control"} for event in events)


@pytest.mark.parametrize("catalog", ["", "[Каталог навыков] code-change"])
@pytest.mark.parametrize("name,arguments", [
    ("project_map", {}), ("glob", {"pattern": "*.txt"}),
    ("grep", {"pattern": "Проверено", "path": "data.txt"}),
])
def test_actual_project_discovery_activates_work_once_even_without_catalog(tmp_path, monkeypatch, catalog, name, arguments):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(turn_context, "catalog_context", lambda: catalog)
    (tmp_path / "data.txt").write_text("Проверено: 12", encoding="utf-8")
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        assert sum(WORK in message.get("content", "") for message in calls[-1]) == int(len(calls) > 1)
        if len(calls) == 1:
            return _reply(name, arguments)
        assert sum(REMINDER in message.get("content", "") for message in calls[-1]) == int(bool(catalog))
        assert {"read_file", "write_file", name} <= {tool["function"]["name"] for tool in kwargs["tools"]}
        return _reply("read_file", {"path": "data.txt"}) if len(calls) == 2 else _reply()

    events = list(agent_loop.stream_code_agent(user_message="Исследуй файлы проекта.",
        project_root=tmp_path, chat_fn=chat, auto_remember=False,
        permission_mode="bypass", num_ctx=65536))
    assert len(calls) == 3 and events[-1]["stop_reason"] == "answer"
    assert next(event for event in events if event.get("tool") == name and event["type"] == "tool_call")["ok"]


def test_real_mutation_gets_work_guidance_on_next_turn_without_hiding_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(turn_context, "catalog_context", lambda: "")
    calls = []

    def chat(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        if len(calls) == 1:
            assert WORK not in str(kwargs["messages"])
            return _reply("write_file", {"path": "note.txt", "content": "Проверено: 12"})
        assert sum(WORK in message.get("content", "") for message in calls[-1]) == 1
        assert REMINDER not in str(kwargs["messages"])
        assert {"write_file", "edit_file", "runtime_control"} <= {tool["function"]["name"] for tool in kwargs["tools"]}
        return _reply(text="Создан note.txt.")

    events = list(agent_loop.stream_code_agent(user_message="Создай note.txt.",
        project_root=tmp_path, chat_fn=chat, auto_remember=False,
        permission_mode="bypass", num_ctx=65536))
    assert len(calls) == 2 and events[-1]["stop_reason"] == "answer"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "Проверено: 12"
    assert next(event for event in events if event["type"] == "tool_call")["state_changed"]


