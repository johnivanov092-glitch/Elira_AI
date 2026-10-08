"""Procedural instructions stay separate from explicit acceptance criteria."""
from __future__ import annotations

import json
import socket

import pytest
import requests

from app.application.code_agent.taskspec import derive_task_spec


FILE_IO_TASK = (
    "Выполни строго по порядку четыре операции в рабочей папке, только с probe.txt: "
    "1) write_file: создай probe.txt в UTF-8 без BOM с LF и точным содержимым:\n"
    "```text\nalpha=17\nbeta=25\nmarker=Янтарь\n```\n"
    "2) read_file: прочитай созданный probe.txt. "
    "3) edit_file: замени только beta=25 на beta=26. "
    "4) read_file: перечитай probe.txt. "
    "В ответе сообщи marker и конечные alpha, beta. Арифметика не нужна. "
    "Другие файлы и инструменты для этой задачи не нужны."
)


def test_ordered_file_operations_do_not_create_a_readiness_criterion():
    assert derive_task_spec(FILE_IO_TASK) is None


def test_explicit_criterion_after_ordered_instructions_is_preserved():
    spec = derive_task_spec(FILE_IO_TASK + "\nКритерии готовности:\n- probe.txt содержит beta=26")
    assert spec is not None
    assert spec.success_criteria == ["probe.txt содержит beta=26"]
    assert any("read_file: прочитай созданный probe.txt" in item for item in spec.details)


def test_build_requirement_bullets_without_a_heading_are_preserved():
    spec = derive_task_spec("Сделай сайт:\n- index.html с формой\n- кнопка отправки")
    assert spec is not None
    assert spec.success_criteria == ["index.html с формой", "кнопка отправки"]


def test_explicit_read_action_criterion_is_preserved():
    spec = derive_task_spec("Цель: Прочитай source.txt.\nКритерии готовности:\n- source.txt прочитан инструментом read_file")
    assert spec is not None
    assert spec.success_criteria == ["source.txt прочитан инструментом read_file"]


def test_named_workflow_after_criteria_stays_in_details():
    spec = derive_task_spec("Цель: Подготовить проект.\nКритерии готовности:\n- тесты проходят\n"
                            "Порядок работы:\n1. Осмотри папку.\n2. Запусти тесты.")
    assert spec is not None
    assert spec.success_criteria == ["тесты проходят"]
    assert spec.details == ["Осмотри папку.", "Запусти тесты."]


@pytest.mark.parametrize("intro", [
    "Выполни следующие действия по порядку:",
    "Сделай операции в следующем порядке:",
    "Perform these operations in order:",
    "Execute these actions in this order:",
])
def test_ordered_instruction_intro_is_not_specific_to_tools_or_filenames(intro):
    assert derive_task_spec(intro + "\n1. Осмотри папку.\n2. Сохрани результат.") is None


@pytest.mark.parametrize("intro, requirement", [
    ("Perform the change in order to support uploads:", "upload.py must exist"),
    ("Do not modify configuration in order to preserve compatibility:", "app.py must contain the fix"),
    ("Do not process files in this order:", "app.py must contain the fix"),
])
def test_purpose_or_negative_instruction_does_not_hide_existing_requirements(intro, requirement):
    spec = derive_task_spec(intro + "\n- " + requirement + "\n- pytest must pass")
    assert spec is not None
    assert spec.success_criteria == [requirement, "pytest must pass"]
    assert spec.verifiers == ["прогнать pytest и убедиться, что проходит"]


@pytest.mark.parametrize("effort", ["none", "low"])
def test_ordered_file_operations_keep_the_original_request_and_real_tool_dispatch(
    tmp_path, monkeypatch, effort,
):
    from app.application.code_agent.agent_loop import stream_code_agent
    from app.infrastructure.llm import openai_compatible as provider

    endpoint = "http://ordered-instructions.invalid"
    monkeypatch.setenv("LLAMA_SERVER_ENABLED", "true")
    monkeypatch.setenv("LLAMA_SERVER_BASE_URL", endpoint + "/v1")
    monkeypatch.setenv("LLAMA_SERVER_MODEL", "local-model")
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(provider, "_props_ctx_cache", {})
    payloads = []
    operations = [
        ("write_file", {"path": "probe.txt", "content": "alpha=17\nbeta=25\nmarker=Янтарь\n"}),
        ("read_file", {"path": "probe.txt"}),
        ("edit_file", {"path": "probe.txt", "old_string": "beta=25", "new_string": "beta=26"}),
        ("read_file", {"path": "probe.txt"}),
    ]

    def no_network(*args, **kwargs):
        pytest.fail("Offline instruction regression attempted a real connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    def send(session, request, **kwargs):
        response = requests.Response()
        response.request, response.url, response.status_code = request, request.url, 200
        response._content_consumed = True
        if request.method == "GET":
            assert request.url in {endpoint + "/props", endpoint + "/v1/models"}
            response._content = b'{"data":[{"id":"local-model","owned_by":"vllm","max_model_len":65536}]}'
            if request.url.endswith("/props"):
                response.status_code = 404
            return response
        assert (request.method, request.url) == ("POST", endpoint + "/v1/chat/completions")
        payloads.append(json.loads(request.body))
        turn = len(payloads) - 1
        assert turn <= len(operations), "Unexpected additional coordinator turn"
        if turn < len(operations):
            name, arguments = operations[turn]
            delta = {"tool_calls": [{"index": 0, "id": f"file-{turn}", "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            if effort == "low":
                delta["reasoning_content"] = "Execute the next requested operation."
        else:
            delta = {"content": "marker=Янтарь, alpha=17, beta=26."}
        response.headers["Content-Type"] = "text/event-stream"
        response._content = ("data: " + json.dumps({"choices": [{"delta": delta}]})
                             + "\n\ndata: [DONE]\n\n").encode("utf-8")
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    events = list(stream_code_agent(
        user_message=FILE_IO_TASK, project_root=tmp_path, model="local-model",
        run_id="instruction-boundary-" + effort + "-" + tmp_path.name,
        reasoning_effort=effort, base_tools=["write_file", "read_file", "edit_file"],
        read_only=False, permission_mode="accept_edits", auto_remember=False,
        pause_for_workflow_request=True,
    ))
    assert len(payloads) == 5
    assert not any(event["type"] == "planning_started" for event in events)
    calls = [event for event in events if event["type"] == "tool_call"]
    assert [event["tool"] for event in calls] == [name for name, _ in operations]
    assert all(event["ok"] for event in calls)
    assert "beta=25" in calls[1]["result"]
    assert "beta=26" in calls[3]["result"]
    assert (tmp_path / "probe.txt").read_bytes() == "alpha=17\nbeta=26\nmarker=Янтарь\n".encode("utf-8")
    for body in payloads:
        assert [item["content"] for item in body["messages"] if item["role"] == "user"] == [FILE_IO_TASK]
    done = next(event for event in events if event["type"] == "done")
    assert done["ok"] is True
    assert not done.get("criteria")
    assert not done.get("task_spec")
