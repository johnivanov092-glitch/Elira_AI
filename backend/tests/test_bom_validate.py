from __future__ import annotations

from openpyxl import Workbook

from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.tools._bom import (
    canonical_bom_file_inputs,
    make_bom_snapshot,
    tool_bom_validate,
)
from app.application.code_agent.tools import _bom
from unittest.mock import patch


def _price_file(tmp_path):
    path = tmp_path / "price.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Прайс"
    sheet.append(["Артикул", "Наименование", "Цена", "Остаток"])
    sheet.append(["SKU-1", "Процессор", 1000, 2])
    sheet.append(["SKU-2", "Кулер", 500, 0])
    workbook.save(path)
    return path


def _valid_bom_result() -> dict:
    return {
        "ok": True,
        "catalog_sha256": "a" * 64,
        "rows": [{
            "code": "SKU-1",
            "name": "Процессор",
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


def test_bom_validate_calculates_catalog_rows_service_vat_and_total(tmp_path) -> None:
    price = _price_file(tmp_path)

    result = tool_bom_validate(
        tmp_path,
        catalog_path=price.name,
        sheet_name="Прайс",
        code_column="Артикул",
        name_column="Наименование",
        price_column="Цена",
        stock_column="Остаток",
        items=[{"code": "SKU-1", "quantity": 2}],
        service_items=[{
            "code": "SERVICE-ASSEMBLY",
            "name": "Сборка и настройка",
            "quantity": 1,
            "unit_price": 23200,
        }],
        markup_percent=20,
        vat_rate=12,
        prices_include_vat=True,
        expected_total=25600,
    )

    assert result["ok"] is True
    assert result["rows"][0] == {
        "code": "SKU-1",
        "name": "Процессор",
        "quantity": 2,
        "available_quantity": 2,
        "catalog_unit_price": "1000.00",
        "client_unit_price": "1200.00",
        "line_total": "2400.00",
        "source": "catalog",
    }
    assert result["subtotal"] == "25600.00"
    assert result["vat_amount"] == "2742.86"
    assert result["total"] == "25600.00"
    assert result["expected_total_matches"] is True
    assert len(result["catalog_sha256"]) == 64
    assert len(result["receipt_sha256"]) == 64


def test_canonical_file_inputs_ignore_model_arithmetic() -> None:
    snapshot = make_bom_snapshot(_valid_bom_result())
    assert snapshot is not None

    inputs = canonical_bom_file_inputs(snapshot, format="pdf")

    assert inputs["title"] == "Спецификация"
    assert "1000.00" in inputs["content"]
    assert "9999.00" not in inputs["content"]
    assert snapshot["receipt_sha256"] in inputs["content"]


def test_bom_validate_fails_closed_on_missing_code_or_insufficient_stock(tmp_path) -> None:
    price = _price_file(tmp_path)

    result = tool_bom_validate(
        tmp_path,
        catalog_path=price.name,
        code_column="Артикул",
        name_column="Наименование",
        price_column="Цена",
        stock_column="Остаток",
        items=[
            {"code": "SKU-2", "quantity": 1},
            {"code": "SKU-MISSING", "quantity": 1},
        ],
    )

    assert result["ok"] is False
    assert {issue["code"] for issue in result["issues"]} == {
        "insufficient_stock",
        "catalog_code_not_found",
    }
    assert "total" not in result


def test_bom_validate_refuses_catalog_over_row_limit(tmp_path, monkeypatch) -> None:
    price = _price_file(tmp_path)
    monkeypatch.setattr(_bom, "_MAX_CATALOG_ROWS", 1)

    result = tool_bom_validate(
        tmp_path,
        catalog_path=price.name,
        code_column="Артикул",
        name_column="Наименование",
        price_column="Цена",
        stock_column="Остаток",
        items=[{"code": "SKU-1", "quantity": 1}],
    )

    assert result["ok"] is False
    assert result["error"] == "catalog_too_many_rows"


def test_bom_validate_schema_is_qwen_friendly_and_available() -> None:
    schema = next(
        row["function"] for row in build_tool_schemas()
        if row["function"]["name"] == "bom_validate"
    )

    assert schema["parameters"]["additionalProperties"] is False
    assert schema["parameters"]["required"] == [
        "catalog_path",
        "code_column",
        "name_column",
        "price_column",
        "stock_column",
        "items",
    ]


def test_local_price_bom_cannot_finish_before_deterministic_validation(tmp_path) -> None:
    responses = iter([
        {"message": {"content": "КП готово, итог 1000 тенге.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "bom_validate",
                        "arguments": {
                            "catalog_path": "price.xlsx",
                            "code_column": "Код",
                            "name_column": "Название",
                            "price_column": "Цена",
                            "stock_column": "Остаток",
                            "items": [{"code": "A1", "quantity": 1}],
                        },
                    },
                }],
            },
        },
        {"message": {"content": "КП готово по итогу 9999 тенге.", "tool_calls": []}},
    ])
    prompts = []

    def fake_chat(**kwargs):
        prompts.append(list(kwargs["messages"]))
        return next(responses)

    with patch(
        "app.application.code_agent.tools._dispatch.tool_bom_validate",
        return_value=_valid_bom_result(),
    ):
        events = list(stream_code_agent(
            user_message="Собери ПК по локальному XLSX-прайсу и сделай коммерческое предложение",
            project_root=tmp_path,
            run_id="bom-completion-guard",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    assert "internal BOM correction" in str(prompts[1][-1]["content"])
    assert any(
        event.get("type") == "tool_call"
        and event.get("tool") == "bom_validate"
        and event.get("ok") is True
        for event in events
    )
    final = next(event for event in events if event.get("type") == "final_response")
    assert "Канонический итог: 1000.00" in final["text"]
    assert "9999" not in final["text"]


def test_failed_revalidation_revokes_receipt_and_blocks_publish(tmp_path) -> None:
    responses = [
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "bom_validate",
            "arguments": {
                "catalog_path": "price.xlsx",
                "code_column": "Код",
                "name_column": "Название",
                "price_column": "Цена",
                "stock_column": "Остаток",
                "items": [{"code": "A1", "quantity": 1}],
            },
        }}]}},
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "bom_validate",
            "arguments": {
                "catalog_path": "price.xlsx",
                "code_column": "Код",
                "name_column": "Название",
                "price_column": "Цена",
                "stock_column": "Остаток",
                "items": [{"code": "CHANGED", "quantity": 1}],
            },
        }}]}},
        {"message": {"content": "", "tool_calls": [{"function": {
            "name": "resource_publish",
            "arguments": {"project_path": "quote.pdf"},
        }}]}},
    ]

    def fake_chat(**_kwargs):
        if responses:
            return responses.pop(0)
        return {"message": {"content": "КП опубликовано на 9999.", "tool_calls": []}}

    with patch(
        "app.application.code_agent.tools._dispatch.tool_bom_validate",
        side_effect=[
            _valid_bom_result(),
            {"ok": False, "error": "bom_validation_failed", "text": "ERROR"},
        ],
    ):
        events = list(stream_code_agent(
            user_message="Собери BOM по XLSX и дай PDF для скачивания",
            project_root=tmp_path,
            run_id="bom-revalidation-revokes-receipt",
            chat_fn=fake_chat,
            auto_remember=False,
            permission_mode="bypass",
        ))

    publish = next(
        event for event in events
        if event.get("type") == "tool_call" and event.get("tool") == "resource_publish"
    )
    final = next(event for event in events if event.get("type") == "final_response")
    assert publish["ok"] is False
    assert "successful bom_validate" in str(publish["result"])
    assert final["answer_status"] == "degraded"
    assert "9999" not in final["text"]


def test_failed_bom_validation_cannot_be_reported_as_complete(tmp_path) -> None:
    responses = iter([
        {"message": {"content": "КП готово.", "tool_calls": []}},
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "bom_validate",
                        "arguments": {
                            "catalog_path": "price.xlsx",
                            "code_column": "Код",
                            "name_column": "Название",
                            "price_column": "Цена",
                            "stock_column": "Остаток",
                            "items": [{"code": "MISSING", "quantity": 1}],
                        },
                    },
                }],
            },
        },
        {"message": {"content": "КП всё равно готово.", "tool_calls": []}},
    ])

    with patch(
        "app.application.code_agent.tools._dispatch.tool_bom_validate",
        return_value={"ok": False, "error": "bom_validation_failed", "text": "ERROR"},
    ):
        events = list(stream_code_agent(
            user_message="Собери ПК по локальному XLSX-прайсу и сделай коммерческое предложение",
            project_root=tmp_path,
            run_id="bom-fail-closed",
            chat_fn=lambda **_kwargs: next(responses),
            auto_remember=False,
            permission_mode="bypass",
        ))

    final = next(event for event in events if event.get("type") == "final_response")
    assert final["answer_status"] == "degraded"
    assert "BOM не завершён" in final["text"]
    assert "всё равно готово" not in final["text"]
