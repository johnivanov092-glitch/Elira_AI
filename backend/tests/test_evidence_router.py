from __future__ import annotations

from unittest.mock import patch

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.capabilities import (
    route_request_capabilities,
    should_escalate_web_after_failure,
    should_escalate_web_from_answer,
)


def test_infrastructure_route_preloads_typed_ssh_itops_and_web_evidence() -> None:
    decision = route_request_capabilities(
        "Подключись к MikroTik и проверь совместимость RouterOS 6.49 с MikroMCP",
        domain_policy="Инфраструктура",
    )

    assert decision.include_itops is True
    assert decision.include_ssh is True
    assert "web" in decision.capability_groups
    assert decision.evidence_reasons


def test_download_request_preloads_resources_and_marks_delivery_contract() -> None:
    decision = route_request_capabilities(
        "Создай PDF и дай мне файл для скачивания",
        domain_policy="Деловой",
    )

    assert "resources" in decision.capability_groups
    assert decision.download_requested is True


def test_local_code_edit_does_not_load_web_without_external_evidence_need() -> None:
    decision = route_request_capabilities(
        "Исправь опечатку в локальном файле backend/app/main.py",
        domain_policy="Инженерный",
    )

    assert "web" not in decision.capability_groups


def test_external_integration_failure_escalates_web_on_first_failure() -> None:
    assert should_escalate_web_after_failure(
        tool_name="runtime_control",
        error="MCP server start failed: unsupported RouterOS version",
        failure_count=1,
    ) is True


def test_repeated_local_tool_failure_escalates_web() -> None:
    assert should_escalate_web_after_failure(
        tool_name="run_bash",
        error="command failed",
        failure_count=2,
    ) is True


def test_finance_and_security_requests_preload_web_in_any_domain() -> None:
    finance = route_request_capabilities(
        "Оцени актуальный курс и процентную ставку",
        domain_policy="Деловой",
    )
    security = route_request_capabilities(
        "Проверь уязвимость CVE в локальном проекте",
        domain_policy="Инженерный",
    )

    assert "web" in finance.capability_groups
    assert "web" in security.capability_groups


def test_one_request_combines_code_network_and_web_without_profile_lock() -> None:
    decision = route_request_capabilities(
        "Исправь Python-код диагностики SSH-сети и проверь актуальные CVE",
        domain_policy="Инженерный",
    )

    assert "Инженерный" in decision.domain_policies
    assert "Инфраструктура" in decision.domain_policies
    assert decision.include_itops is True
    assert decision.include_ssh is True
    assert "web" in decision.capability_groups


def test_uncertain_model_draft_requires_evidence_pass() -> None:
    assert should_escalate_web_from_answer("Не уверен, данных недостаточно.") is True
    assert should_escalate_web_from_answer("Локальный файл успешно обновлён.") is False


def test_download_request_cannot_finish_before_resource_publish(tmp_path) -> None:
    responses = iter([
        {"message": {"content": "Файл готов.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "resource_publish",
                        "arguments": {
                            "project_path": "report.pdf",
                            "download_name": "report.pdf",
                        },
                    },
                }],
            },
        },
        {"message": {"content": "Файл доступен по кнопке скачивания.", "tool_calls": []}},
    ])
    prompts: list[list[dict[str, object]]] = []

    def fake_chat(**kwargs):
        prompts.append(list(kwargs["messages"]))
        return next(responses)

    def fake_publish(_project_root, **_kwargs):
        return {
            "ok": True,
            "text": "Published report.pdf",
            "project_path": "report.pdf",
            "download_url": "/api/skills/download/report.pdf",
            "download_name": "report.pdf",
            "size": 12,
            "sha256": "a" * 64,
        }

    with patch(
        "app.application.code_agent.tools._dispatch.tool_resource_publish",
        side_effect=fake_publish,
    ):
        events = list(stream_code_agent(
            user_message="Создай PDF и дай файл для скачивания",
            project_root=tmp_path,
            run_id="download-delivery-router",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    assert len(prompts) == 3
    assert "internal delivery correction" in str(prompts[1][-1]["content"])
    published = next(
        event for event in events
        if event.get("type") == "tool_call" and event.get("tool") == "resource_publish"
    )
    assert published["download_url"] == "/api/skills/download/report.pdf"
    done = next(event for event in events if event.get("type") == "done")
    assert done["answer_status"] == "complete"


def test_failed_resource_publish_cannot_be_reported_as_downloadable(tmp_path) -> None:
    responses = iter([
        {"message": {"content": "Файл готов.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "resource_publish",
                        "arguments": {"project_path": "missing.pdf"},
                    },
                }],
            },
        },
        {"message": {"content": "Скачайте готовый файл.", "tool_calls": []}},
    ])

    def fake_chat(**_kwargs):
        return next(responses)

    with patch(
        "app.application.code_agent.tools._dispatch.tool_resource_publish",
        return_value={"ok": False, "text": "ERROR: source_not_file"},
    ):
        events = list(stream_code_agent(
            user_message="Дай файл для скачивания",
            project_root=tmp_path,
            run_id="download-delivery-failed",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    final = next(event for event in events if event.get("type") == "final_response")
    done = next(event for event in events if event.get("type") == "done")
    assert final["answer_status"] == "degraded"
    assert "кнопка скачивания не создана" in final["text"]
    assert "Скачайте готовый файл" not in final["text"]
    assert done["answer_status"] == "degraded"
