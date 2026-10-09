"""Actual skill CLI boundaries: malformed input must never become a valid result."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _bom_request(tmp_path: Path) -> dict:
    (tmp_path / "prices.csv").write_text(
        "Код;Название;Цена;Остаток\nA;SSD;100;5\n", encoding="utf-8"
    )
    return {"catalog_path": "prices.csv", "code_column": "Код", "name_column": "Название",
            "price_column": "Цена", "stock_column": "Остаток",
            "items": [{"code": "A", "quantity": 1}], "vat_rate": 16}


def _bom_cli(tmp_path: Path, payload):
    source = tmp_path / "spec.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return subprocess.run([sys.executable, str(SKILLS / "bom-check/bom_check.py"), str(source)],
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_bom_rejects_non_boolean_vat_mode(tmp_path, value):
    result = _bom_cli(tmp_path, {**_bom_request(tmp_path), "prices_include_vat": value})
    assert result.returncode == 2, result.stdout
    assert "prices_include_vat" in result.stdout and "boolean" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("field,value", [
    ("items", [42]), ("items", [None]), ("service_items", ["fee"]),
    ("service_items", {"code": "fee"}), ("header_row", True), ("header_row", 1.5),
    ("header_row", 0), ("header_row", -1),
])
def test_bom_rejects_invalid_structure_without_traceback(tmp_path, field, value):
    result = _bom_cli(tmp_path, {**_bom_request(tmp_path), field: value})
    assert result.returncode == 2, result.stdout
    assert field in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("payload", [[], None, "input", 42])
def test_bom_requires_json_object(tmp_path, payload):
    result = _bom_cli(tmp_path, payload)
    assert result.returncode == 2
    assert "JSON" in result.stdout and "объект" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("mode,total", [(False, "116.00"), (True, "100.00")])
def test_bom_valid_boolean_preserves_catalog_calculation(tmp_path, mode, total):
    result = _bom_cli(tmp_path, {**_bom_request(tmp_path), "prices_include_vat": mode})
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["ok"] is True and payload["prices_include_vat"] is mode
    assert payload["total"] == total


def _scenario_cli(monkeypatch, tmp_path, output):
    spec = importlib.util.spec_from_file_location("audit_scenario_cli", SKILLS / "_shared/cli.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    calls = []

    def effect():
        calls.append("executed")
        return {"ok": True, "text": "Scenario completed"}

    monkeypatch.setattr(cli, "bootstrap", lambda: None)
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: SimpleNamespace(effect=effect))
    monkeypatch.setattr(sys, "argv", ["skill.py", "effect", "--workspace", str(tmp_path), "--output", str(output)])
    monkeypatch.chdir(tmp_path)
    return cli, calls


def test_invalid_output_path_is_rejected_before_scenario_effect(monkeypatch, tmp_path, capsys):
    cli, calls = _scenario_cli(monkeypatch, tmp_path, tmp_path)
    assert cli.main({"effect": "test_effect:effect"}) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and "Output" in payload["text"]
    assert calls == []


def test_output_write_failure_preserves_completed_action(monkeypatch, tmp_path, capsys):
    output = tmp_path / "result.json"
    cli, calls = _scenario_cli(monkeypatch, tmp_path, output)
    original = Path.write_text

    def failed_write(path, *args, **kwargs):
        if path == output:
            raise PermissionError("output permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failed_write)
    assert cli.main({"effect": "test_effect:effect"}) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["output_saved"] is False
    assert "PermissionError" in payload["output_error"]
    assert calls == ["executed"] and not output.exists()


@pytest.mark.parametrize("same_directory", [True, False])
def test_saved_weather_forecast_never_rewrites_its_input(tmp_path, same_directory):
    daily = {key: [1] for key in (
        "temperature_2m_min", "temperature_2m_max", "apparent_temperature_min",
        "apparent_temperature_max", "rain_sum", "snowfall_sum",
        "precipitation_probability_max", "wind_gusts_10m_max", "weather_code")}
    daily["time"] = ["2026-10-12"]
    payload = {"note": "Сохранённый исходник", "daily": daily, "hourly": {"time": []}}
    original = json.dumps(payload, ensure_ascii=False, indent=4).encode("utf-8") + b"\n"
    source = tmp_path / "forecast.json"
    source.write_bytes(original)
    out_dir = tmp_path if same_directory else tmp_path / "result"
    result = subprocess.run([sys.executable, str(SKILLS / "weather/weather.py"), "forecast",
        "--lat", "43.24", "--lon", "76.95", "--days", "1", "--input-json", str(source),
        "--out-dir", str(out_dir)], capture_output=True, text=True, encoding="utf-8", timeout=20,
        env={**os.environ, "PYTHONIOENCODING": "cp1251", "PYTHONUTF8": "0"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2026-10-12 Пн" in result.stdout
    assert source.read_bytes() == original
    assert (out_dir / "forecast.json").read_bytes() == original


def test_web_cli_source_dates_remain_visible_before_long_page_text(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(SKILLS / "web-research"))
    spec = importlib.util.spec_from_file_location("audit_web_cli", SKILLS / "web-research/web.py")
    web = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web)
    result = {"ok": True, "text": "Page body " * 2000, "sources": [
        {"url": "https://example.org/one", "title": "First source",
         "status": "excerpt", "dates": {"published": "2025-04-29"}},
        {"url": "https://example.org/two", "title": "Second source",
         "status": "excerpt", "dates": {"published": "2024-09-19"}}]}
    monkeypatch.setattr(web, "execute", lambda args: result)
    monkeypatch.setattr(web.context, "configure", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["web.py", "--no-cache", "fetch", "--url", "https://example.org/one"])
    assert web.main() == 0
    raw = capsys.readouterr().out
    assert "2025-04-29" in raw[:3500] and "2024-09-19" in raw[:3500]
    assert "First source" in raw[:3500] and "Second source" in raw[:3500]
    payload = json.loads(raw)
    assert payload["text"] == result["text"] and payload["sources"] == result["sources"]


def test_plain_web_fetch_keeps_original_html_metadata_in_receipts(monkeypatch):
    from webskill.application.code_agent.tools import _web
    from webskill.infrastructure.search import web_runtime
    from webskill.application.web_evidence.receipts import valid_source
    import requests

    class Response:
        status_code = 200
        url = "https://example.org/article"
        headers = {"Content-Type": "text/html", "Last-Modified": "Wed, 30 Apr 2025 12:00:00 GMT"}
        encoding = "utf-8"
        text = ('<html><head><title>Source article</title>'
                '<meta property="article:published_time" content="2025-04-29T10:00:00Z">'
                '</head><body><main><h1>Article</h1><p>' + "Source paragraph. " * 200 + '</p></main></body></html>')
        content = text.encode("utf-8")

        def close(self):
            pass

    calls = []

    def get(*args, **kwargs):
        calls.append(args[0])
        return Response()

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda _: None)
    page = web_runtime.fetch_page(Response.url, max_chars=100)
    assert page.ok and page.truncated
    metadata = _web._page_metadata(Response.url, page)
    assert metadata["title"] == "Source article"
    assert metadata["dates"] == {"published": "2025-04-29", "modified": "2025-04-30"}
    _, sources = _web._fetch_receipts(Response.url, page)
    assert sources and all(valid_source(source) for source in sources)
    assert all(source["title"] == metadata["title"] and source["dates"] == metadata["dates"] for source in sources)
    assert calls == [Response.url]
