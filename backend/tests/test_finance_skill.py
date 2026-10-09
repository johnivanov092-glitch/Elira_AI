"""Mutable finance skill: baseline parity, actual CLI, and calculation boundaries."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SKILL = Path(__file__).resolve().parents[2] / "skills" / "finance"
SCRIPT = SKILL / "finance.py"
FIXTURE = Path(__file__).parent / "fixtures" / "finance_skill_parity.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def finance():
    spec = importlib.util.spec_from_file_location("standalone_finance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_original_finance_envelope_exact_parity(finance, case):
    assert finance.run_request(case["request"]) == case["expected"]


def run_cli(*args):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], capture_output=True,
        text=True, encoding="utf-8", timeout=15,
    )
    assert not completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def test_skill_catalog_and_standard_library_boundary():
    from app.application.code_agent.task_skills import read_package
    package = read_package("finance", SKILL)
    assert package["name"] == "finance" and "НДС" in package["description"]
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(item.name.split(".")[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert modules <= sys.stdlib_module_names | {"elira_common", "shared_path"}
    # Both local helpers remain stdlib-only; the skill never imports the backend.
    for dependency in (SKILL.parent / "_shared/shared_path.py", SKILL.parents[1] / "shared/elira_common/numbers.py"):
        for node in ast.walk(ast.parse(dependency.read_text(encoding="utf-8"))):
            imported = [item.name for item in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) else [])
            assert all(name.split(".")[0] in sys.stdlib_module_names for name in imported)
    assert (SKILL / "KZ_VAT.md").read_text(encoding="utf-8").find("## Подтверждённые правила") > 0


def test_invoice_matches_independent_hand_calculation(finance):
    result = finance.calculate("invoice", {
        "items": [{"name": "CPU", "qty": 1, "price": "189 900"},
                  {"name": "RAM", "qty": 2, "price": "45 500,50"}],
        "markup_percent": 10, "vat_percent": 16,
    })
    assert [line["line_total"] for line in result["lines"]] == ["208890.00", "100101.10"]
    assert result["subtotal"] == "308991.10"
    assert result["vat_amount"] == "49438.58" and result["total"] == "358429.68"


def test_vat_margin_loan_split_semantics(finance):
    assert finance.calculate("vat_extract", {"amount": 145000, "vat_percent": 16})["net"] == "125000.00"
    markup = finance.calculate("markup", {"cost": 100000, "markup_percent": 25})
    assert markup["price"] == "125000.00" and markup["margin_percent"] == "20"
    assert finance.calculate("price_from_margin", {"cost": 80000, "margin_percent": 20})["price"] == "100000.00"
    loan = finance.calculate("loan_payment", {"amount": 1000000, "rate_percent": 18, "months": 12})
    assert loan["monthly_payment"] == "91679.99"
    split = finance.calculate("split", {"amount": 100, "weights": [1, 1, 1]})
    assert split["shares"] == ["33.34", "33.33", "33.33"]


def test_vat_rate_must_remain_explicit(finance):
    with pytest.raises(finance.FinanceError, match="vat_percent"):
        finance.calculate("vat_add", {"amount": 100})


def test_cli_success_json_and_utf8_result_file(tmp_path):
    source, output = tmp_path / "расчёт.json", tmp_path / "результат.json"
    source.write_text(json.dumps({"operation": "invoice", "items": [{"name": "Товар", "price": "100000"}],
                                  "vat_percent": "16"}, ensure_ascii=False), encoding="utf-8")
    exit_code, result = run_cli("--input", source, "--output", output)
    assert exit_code == 0 and result["ok"]
    assert result["result"]["total"] == "116000.00"
    raw = output.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    assert json.loads(raw) == result and "Товар" in raw.decode("utf-8")


@pytest.mark.parametrize("content,expected", [
    ('{"operation":"vat_add","amount":"100"}', "vat_percent"),
    ("[]", "JSON-объект"),
    ("invalid JSON", "ERROR:"),
    ('{"operation":"vat_add","amount":"1e999999","vat_percent":"16"}', "ERROR:"),
])
def test_cli_errors_are_json_and_never_success(tmp_path, content, expected):
    source = tmp_path / "input.json"
    source.write_text(content, encoding="utf-8")
    exit_code, result = run_cli("--input", source)
    assert exit_code == 1 and result["ok"] is False and result["error"] == "finance_error"
    assert expected in result["text"] and "result" not in result


def test_cli_never_clobbers_input_or_existing_result(tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    original = '{"operation":"vat_add","amount":"100","vat_percent":"16"}'
    source.write_text(original, encoding="utf-8")
    output.write_text("retain", encoding="utf-8")
    assert run_cli("--input", source, "--output", source)[0] == 1
    assert source.read_text(encoding="utf-8") == original
    assert run_cli("--input", source, "--output", output)[0] == 1
    assert output.read_text(encoding="utf-8") == "retain"
    assert run_cli("--input", source, "--output", output, "--overwrite")[0] == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"]


def test_cli_missing_file_and_oversized_input_fail_honestly(tmp_path):
    assert run_cli("--input", tmp_path / "missing.json")[0] == 1
    source = tmp_path / "huge.json"
    source.write_text(" " * 2_000_001, encoding="utf-8")
    exit_code, result = run_cli("--input", source)
    assert exit_code == 1 and "2 МБ" in result["text"]


def test_agent_no_longer_exposes_builtin_finance(tmp_path):
    from app.application.code_agent.capabilities import CAPABILITY_GROUPS
    from app.application.code_agent.tools._dispatch import build_tool_dispatch
    from app.application.tool_registry.builtins import _build_native_code_agent_tools
    assert "finance_calc" not in build_tool_dispatch(tmp_path)
    assert all(spec["name"] != "finance_calc" for spec in _build_native_code_agent_tools())
    assert "finance_calc" not in CAPABILITY_GROUPS["math"]
