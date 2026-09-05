from __future__ import annotations

from unittest.mock import patch

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.capabilities import (
    is_local_tabular_catalog_probe,
    route_request_capabilities,
    should_require_local_catalog_search,
    should_escalate_web_after_failure,
    should_escalate_web_from_answer,
)


def _valid_bom_result() -> dict:
    return {
        "ok": True,
        "catalog_sha256": "a" * 64,
        "rows": [{
            "code": "CPU-1",
            "name": "CPU",
            "quantity": 1,
            "client_unit_price": "1000.00",
            "line_total": "1000.00",
        }],
        "markup_percent": "0.00",
        "vat_rate": "12.00",
        "prices_include_vat": True,
        "subtotal": "1000.00",
        "vat_amount": "107.14",
        "total": "1000.00",
        "text": '{"ok":true,"total":"1000.00"}',
    }


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


def test_library_text_cannot_require_unsolicited_download(tmp_path) -> None:
    calls: list[list[dict]] = []

    def fake_chat(**kwargs):
        calls.append(kwargs["messages"])
        return {"message": {"content": "Ответ по моделям.", "tool_calls": []}}

    events = list(stream_code_agent(
        user_message=(
            "Library: server.docx\nCDR скачивается; браузер не умеет просматривать CorelDRAW.\n"
            "ЗАПРОС: что реально умеет Gemini Flash и планируется ли Qwen 3.8 35B AB3?"
        ),
        memory_query="что реально умеет Gemini Flash и планируется ли Qwen 3.8 35B AB3?",
        project_root=tmp_path,
        model="test-model",
        profile_name="Баланс",
        chat_fn=fake_chat,
        auto_remember=False,
    ))

    assert len(calls) == 1
    assert events[-1]["stop_reason"] == "answer"
    assert events[-1]["ok"] is True
    started = next(event for event in events if event["type"] == "run_started")
    assert started["runtime_activation"]["ssh"] is False
    assert started["runtime_activation"]["capability_groups"] == []


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
        arguments={"operation": "mcp_start"},
    ) is True


def test_local_runtime_failure_does_not_replace_local_truth_with_web() -> None:
    assert should_escalate_web_after_failure(
        tool_name="runtime_control",
        error="Library item not found",
        failure_count=2,
        arguments={"operation": "library_add"},
    ) is False


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


def test_general_uncertainty_is_not_a_web_requirement() -> None:
    assert should_escalate_web_from_answer("Не уверен, данных недостаточно.") is False
    assert should_escalate_web_from_answer("Не знаю, что ты сейчас чувствуешь.") is False
    assert should_escalate_web_from_answer('Перевод: «I do not have access to the internet».') is False
    assert should_escalate_web_from_answer('I do not have access to the internet',
        'Повтори: «I do not have access to the internet»') is False
    assert should_escalate_web_from_answer("У меня нет доступа к актуальным новостям.") is True
    assert should_escalate_web_from_answer("Локальный файл успешно обновлён.") is False


def test_local_catalog_guard_is_narrow_and_accepts_a_structured_search() -> None:
    assert is_local_tabular_catalog_probe(
        "sandbox_run",
        {"code": "df = pd.read_excel('price.xlsx')"},
    ) is True
    assert is_local_tabular_catalog_probe(
        "sandbox_run",
        {"code": "df.to_excel('report.xlsx')"},
    ) is False
    assert should_require_local_catalog_search(
        "В локальном прайсе башенных кулеров нет вообще.",
        local_tabular_probe_seen=True,
        library_search_seen=False,
    ) is True
    assert should_require_local_catalog_search(
        "В локальном прайсе башенных кулеров нет вообще.",
        local_tabular_probe_seen=True,
        library_search_seen=True,
    ) is False
    assert should_require_local_catalog_search(
        "В тестах нет ошибок.",
        local_tabular_probe_seen=True,
        library_search_seen=False,
    ) is False


def test_local_catalog_absence_requires_library_search_before_finalizing(tmp_path) -> None:
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "sandbox_run",
                        "arguments": {
                            "code": (
                                "import pandas as pd\n"
                                "df = pd.read_excel('azerti_price.xlsx')\n"
                                "print(df[df['name'].str.contains('кулер')].head(8))"
                            ),
                        },
                    },
                }],
            },
        },
        {
            "message": {
                "content": "В локальном прайсе башенных кулеров нет вообще.",
                "tool_calls": [],
            },
        },
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "runtime_control",
                        "arguments": {
                            "operation": "library_search",
                            "query": "кулер процессора башня tower",
                        },
                    },
                }],
            },
        },
        {
            "message": {
                "content": "Нашёл локально Montech NX400 и добавил его в BOM.",
                "tool_calls": [],
            },
        },
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "bom_validate",
                        "arguments": {
                            "catalog_path": "azerti_price.xlsx",
                            "code_column": "code",
                            "name_column": "name",
                            "price_column": "price",
                            "stock_column": "stock",
                            "items": [{"code": "NX400", "quantity": 1}],
                        },
                    },
                }],
            },
        },
        {
            "message": {
                "content": "Нашёл локально Montech NX400 и добавил его в BOM.",
                "tool_calls": [],
            },
        },
    ])
    prompts: list[list[dict[str, object]]] = []

    def fake_chat(**kwargs):
        prompts.append(list(kwargs["messages"]))
        return next(responses)

    with (
        patch(
            "app.application.code_agent.tools._dispatch.tool_sandbox_run",
            return_value={
                "ok": True,
                "text": "===== CPU coolers (0) =====",
            },
        ),
            patch(
                "app.application.code_agent.tools._dispatch.tool_runtime_control",
            return_value={
                "ok": True,
                "status": "completed",
                "operation": "library_search",
                "result": {
                    "ok": True,
                    "items": [{
                        "name": "Price Azerti.xlsx",
                        "excerpt": "Вентилятор для процессора Montech NX400",
                    }],
                },
                },
            ),
            patch(
                "app.application.code_agent.tools._dispatch.tool_bom_validate",
                return_value=_valid_bom_result(),
            ),
        ):
        events = list(stream_code_agent(
            user_message="Собери компьютер по локальному XLSX-прайсу",
            project_root=tmp_path,
            run_id="local-catalog-search-guard",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    assert len(prompts) == 6
    assert "internal local-catalog correction" in str(prompts[2][-1]["content"])
    assert "internal BOM correction" in str(prompts[4][-1]["content"])
    assert any(
        event.get("type") == "tool_call"
        and event.get("tool") == "runtime_control"
        and event.get("arguments", {}).get("operation") == "library_search"
        for event in events
    )
    final = next(event for event in events if event.get("type") == "final_response")
    assert "Канонический итог: 1000.00" in final["text"]
    assert "Montech NX400" not in final["text"]


def test_local_catalog_correction_is_sent_only_once(tmp_path) -> None:
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "sandbox_run",
                        "arguments": {
                            "code": "df = pd.read_excel('price.xlsx')",
                        },
                    },
                }],
            },
        },
        {
            "message": {
                "content": "В локальном прайсе нужной позиции нет.",
                "tool_calls": [],
            },
        },
        {
            "message": {
                "content": "В локальном прайсе нужной позиции нет.",
                "tool_calls": [],
            },
        },
    ])
    prompts: list[list[dict[str, object]]] = []

    def fake_chat(**kwargs):
        prompts.append(list(kwargs["messages"]))
        return next(responses)

    with patch(
        "app.application.code_agent.tools._dispatch.tool_sandbox_run",
        return_value={"ok": True, "text": "no exact matches"},
    ):
        events = list(stream_code_agent(
            user_message="Проверь локальный XLSX-прайс",
            project_root=tmp_path,
            run_id="local-catalog-search-guard-once",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    assert len(prompts) == 3
    assert "internal local-catalog correction" in str(prompts[2][-1]["content"])
    final = next(event for event in events if event.get("type") == "final_response")
    assert final["text"] == "В локальном прайсе нужной позиции нет."


def test_confirmed_local_bom_absence_requires_web_fallback(tmp_path) -> None:
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "sandbox_run",
                        "arguments": {"code": "pd.read_excel('price.xlsx')"},
                    },
                }],
            },
        },
        {"message": {"content": "В локальном прайсе башенного кулера нет.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "runtime_control",
                        "arguments": {
                            "operation": "library_search",
                            "query": "башенный кулер CPU tower",
                        },
                    },
                }],
            },
        },
        {"message": {"content": "В локальном прайсе башенного кулера нет.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "web_search",
                        "arguments": {"query": "башенный кулер LGA1700 цена наличие"},
                    },
                }],
            },
        },
        {"message": {"content": "Нашёл внешнюю альтернативу с источником.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "bom_validate",
                        "arguments": {
                            "catalog_path": "price.xlsx",
                            "code_column": "code",
                            "name_column": "name",
                            "price_column": "price",
                            "stock_column": "stock",
                            "items": [{"code": "CPU-1", "quantity": 1}],
                        },
                    },
                }],
            },
        },
        {"message": {"content": "BOM завершён с подтверждённой альтернативой.", "tool_calls": []}},
    ])
    prompts: list[list[dict[str, object]]] = []

    def fake_chat(**kwargs):
        prompts.append(list(kwargs["messages"]))
        return next(responses)

    with (
        patch(
            "app.application.code_agent.tools._dispatch.tool_sandbox_run",
            return_value={"ok": True, "text": "no exact matches"},
        ),
        patch(
            "app.application.code_agent.tools._dispatch.tool_runtime_control",
            return_value={
                "ok": True,
                "status": "completed",
                "operation": "library_search",
                "result": {"ok": True, "items": [], "count": 0},
                "text": '{"ok":true,"items":[],"count":0}',
            },
        ),
        patch(
            "app.application.code_agent.tools._dispatch.tool_web_search",
            return_value={"ok": True, "text": "https://vendor.example/cooler — in stock"},
        ),
        patch(
            "app.application.code_agent.tools._dispatch.tool_bom_validate",
            return_value=_valid_bom_result(),
        ),
    ):
        events = list(stream_code_agent(
            user_message="Собери ПК по локальному XLSX-прайсу и подготовь BOM",
            project_root=tmp_path,
            run_id="catalog-web-fallback",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    assert "internal local-catalog correction" in str(prompts[2][-1]["content"])
    assert any("internal catalog Web fallback" in str(m.get("content", "")) for m in prompts[4])
    assert any(
        event.get("type") == "runtime_activation_changed"
        and event.get("source") == "catalog_absence_fallback"
        for event in events
    )
    assert any(
        event.get("type") == "tool_call" and event.get("tool") == "web_search"
        for event in events
    )


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


@pytest.mark.parametrize("streaming", [False, True])
def test_unverified_document_qa_claim_is_replaced_by_runtime_backstop(tmp_path, streaming) -> None:
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "resource_publish",
                        "arguments": {
                            "project_path": "proposal.docx",
                            "download_name": "proposal.docx",
                        },
                    },
                }],
            },
        },
        {
            "message": {
                "content": "proposal.docx полностью проверен, QA passed.",
                "tool_calls": [],
            },
        },
    ])

    def fake_chat(**_kwargs):
        return next(responses)

    def fake_stream(**kwargs):
        response = fake_chat(**kwargs)
        yield {"type": "delta", "content": response["message"]["content"]}
        yield {"type": "message", "response": response}

    with patch(
        "app.application.code_agent.tools._dispatch.tool_resource_publish",
        return_value={
            "ok": True,
            "text": "Published proposal.docx",
            "project_path": "proposal.docx",
            "download_url": "/api/skills/download/proposal.docx",
            "download_name": "proposal.docx",
            "size": 123,
            "sha256": "a" * 64,
        },
    ):
        events = list(stream_code_agent(
            user_message="Создай DOCX и дай файл для скачивания",
            project_root=tmp_path,
            run_id="document-qa-backstop",
            chat_fn=fake_chat,
            chat_stream_fn=fake_stream if streaming else None,
            auto_remember=False,
            permission_mode="bypass",
        ))

    final = next(event for event in events if event.get("type") == "final_response")
    done = next(event for event in events if event.get("type") == "done")
    assert final["answer_status"] == "degraded"
    assert "внешняя проверка не подтверждена" in final["text"]
    assert "QA passed" not in final["text"]
    assert done["answer_status"] == "degraded"
    if streaming:
        assert all(event["answer_state"] == "draft" for event in events if event["type"] == "delta")
        assert final["answer_state"] == "accepted"


def test_user_page_count_contract_overrides_model_omission(tmp_path) -> None:
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "resource_publish",
                        "arguments": {"project_path": "proposal.docx"},
                    },
                }],
            },
        },
        {"message": {"content": "Файл готов.", "tool_calls": []}},
    ])
    captured: dict = {}

    def fake_chat(**_kwargs):
        return next(responses)

    def fake_publish(_project_root, **kwargs):
        captured.update(kwargs)
        sha256 = "f" * 64
        return {
            "ok": True,
            "text": "Published proposal.docx",
            "project_path": "proposal.docx",
            "download_url": "/api/skills/download/proposal.docx",
            "download_name": "proposal.docx",
            "size": 123,
            "sha256": sha256,
            "document_qa": {
                "status": "passed",
                "sha256": sha256,
                "target": "proposal.docx",
                "page_count": 1,
                "expected_page_count": 1,
                "vision_status": "passed",
                "issues": [],
            },
        }

    with patch(
        "app.application.code_agent.tools._dispatch.tool_resource_publish",
        side_effect=fake_publish,
    ):
        list(stream_code_agent(
            user_message="Создай DOCX: 1 страница на документ, затем дай скачать",
            project_root=tmp_path,
            run_id="document-page-contract",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    assert captured["expected_page_count"] == 1
    assert captured["run_id"] == "document-page-contract"
