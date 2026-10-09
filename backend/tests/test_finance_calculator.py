"""The finance CLI is the single public entry for money, algebra and dates."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "skills/math/calculate.py"


@pytest.mark.parametrize("payload,key,expected", [
    ({"operation": "evaluate", "expression": "0.1+0.2"}, "result", {"exact": "3/10", "decimal": "0.3", "approximate": False}),
    ({"operation": "evaluate", "expression": "18.7/3.6", "places": 1}, "result", {"exact": "187/36", "decimal": "5.2", "approximate": True, "rounded_to": 1}),
    ({"operation": "simplify", "expression": "x+x"}, "result", "2*x"),
    ({"operation": "expand", "expression": "(x+1)**2"}, "result", "x**2 + 2*x + 1"),
    ({"operation": "factor", "expression": "x**2-1"}, "result", "(x - 1)*(x + 1)"),
    ({"operation": "solve", "expression": "2*x=4"}, "solutions", [{"x": {"exact": "2", "decimal": "2", "approximate": False}}]),
    ({"operation": "diff", "expression": "x**3"}, "result", "3*x**2"),
    ({"operation": "integrate", "expression": "x**2", "lower": "0", "upper": "3"}, "result", {"exact": "9", "decimal": "9", "approximate": False}),
    ({"operation": "date_info", "expression": "2026-10-09"}, "dates", [{"date": "2026-10-09", "weekday_iso": 5, "weekday_ru": "пятница"}]),
])
def test_calculator_operations_use_finance_cli(tmp_path, payload, key, expected):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source)],
                               capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["result"][key] == expected
