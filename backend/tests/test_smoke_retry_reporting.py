from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


RUNNER_PATH = Path(__file__).resolve().parent / "smokes" / "run.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("smoke_retry_runner", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    original_path = list(sys.path)
    try:
        spec.loader.exec_module(runner)
    finally:
        sys.path[:] = original_path
    return runner


@pytest.fixture
def smoke_runner(tmp_path: Path, monkeypatch, capsys):
    runner = _load_runner()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "task.txt").write_text("Smoke reporting fixture", encoding="utf-8")
    output = tmp_path / "output"
    calls: list[dict] = []
    network_calls: list[tuple] = []

    def reject_network(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("live network is forbidden in the reporting tests")

    monkeypatch.setattr(runner.urllib.request, "urlopen", reject_network)
    monkeypatch.setattr(runner.socket, "create_connection", reject_network)
    monkeypatch.setattr(runner, "HERE", inputs)
    monkeypatch.setattr(runner, "tempfile", SimpleNamespace(gettempdir=lambda: str(output)))
    monkeypatch.setattr(runner, "time", SimpleNamespace(strftime=lambda _format: "fixture"))
    monkeypatch.setattr(runner, "_backend_up", lambda _backend: True)
    monkeypatch.setattr(runner, "_ssh_host_allowed", lambda _backend, _host: False)
    monkeypatch.setattr(sys, "argv", [str(RUNNER_PATH)])

    def run(outcomes: list[dict | Exception], *, skip: bool = False):
        spec = {
            "task": "task.txt",
            "expect_completion": "confirmed",
            "expect_confirmed": 1,
            "max_tool_calls": 3,
        }
        if skip:
            spec["requires_ssh_host"] = "fixture-host"
        (inputs / "baseline.json").write_text(
            json.dumps({"fixture": spec}), encoding="utf-8",
        )

        def fake_transport(**kwargs):
            calls.append(kwargs)
            events_path = kwargs["events_path"]
            events_path.write_text(
                json.dumps({"type": "run_started", "run_id": kwargs["run_id"]}) + "\n",
                encoding="utf-8",
            )
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return {"run_id": kwargs["run_id"], **outcome}

        monkeypatch.setattr(runner, "run_smoke", fake_transport)
        exit_code = runner.main()
        out_root = output / "elira-smokes" / "fixture"
        report = json.loads((out_root / "results.json").read_text(encoding="utf-8"))
        return exit_code, report["fixture"], calls, capsys.readouterr().out, out_root

    yield run
    assert network_calls == []


def _summary(**overrides) -> dict:
    return {
        "stop_reason": "answer",
        "completion_status": "confirmed",
        "confirmed": 1,
        "total_criteria": 1,
        "tool_calls": 1,
        "auto_verifier_calls": 0,
        "duration_s": 1,
        **overrides,
    }


def test_first_pass_has_no_retry_and_keeps_the_runner_verdict(smoke_runner):
    code, result, calls, output, root = smoke_runner([_summary(status="provider-status")])

    assert code == 0
    assert len(calls) == 1
    assert result["status"] == "PASS"
    assert result["fails"] == []
    assert len(result["attempts"]) == 1
    attempt = result["attempts"][0]
    assert attempt["attempt"] == 1
    assert attempt["summary"]["status"] == "provider-status"
    assert attempt["summary"]["completion_status"] == "confirmed"
    assert attempt["fails"] == []
    assert attempt["run_id"] == calls[0]["run_id"] == "smoke-fixture-fixture-a1"
    assert (root / attempt["events_file"]).is_file()
    assert not (root / "fixture").exists()
    assert "Итог: 1 first-pass, 0 rerun-pass, 0 fail, 0 skip" in output


def test_rerun_pass_retains_the_first_failures_and_both_summaries(smoke_runner):
    code, result, calls, output, root = smoke_runner([
        _summary(completion_status="partial", confirmed=0),
        _summary(),
    ])

    assert code == 0
    assert len(calls) == 2
    assert result["status"] == "FLAKY-PASS"
    assert result["completion_status"] == "confirmed"
    assert result["confirmed"] == 1
    assert result["fails"] == []
    first, second = result["attempts"]
    assert [first["attempt"], second["attempt"]] == [1, 2]
    assert first["summary"]["completion_status"] == "partial"
    assert first["summary"]["confirmed"] == 0
    assert first["fails"] == [
        "completion=partial (ожидалось confirmed)",
        "confirmed=0/1 (ожидалось 1)",
    ]
    assert second["summary"]["completion_status"] == "confirmed"
    assert second["fails"] == []
    assert first["run_id"] != second["run_id"]
    assert first["events_file"] != second["events_file"]
    assert all((root / attempt["events_file"]).is_file() for attempt in (first, second))
    assert "Итог: 0 first-pass, 1 rerun-pass, 0 fail, 0 skip" in output


def test_both_failures_keep_the_last_summary_and_failure_exit(smoke_runner):
    code, result, calls, output, root = smoke_runner([
        _summary(confirmed=0),
        _summary(tool_calls=4),
    ])

    assert code == 1
    assert len(calls) == 2
    assert result["status"] == "FAIL"
    assert result["tool_calls"] == 4
    assert result["fails"] == ["tool_calls=4 > бюджета 3"]
    first, second = result["attempts"]
    assert first["fails"] == ["confirmed=0/1 (ожидалось 1)"]
    assert second["fails"] == result["fails"]
    assert (root / "fixture").is_dir()
    assert "Итог: 0 first-pass, 0 rerun-pass, 1 fail, 0 skip" in output


def test_transport_failure_is_retained_before_a_successful_retry(smoke_runner):
    code, result, calls, output, root = smoke_runner([
        OSError("fixture transport interrupted"),
        _summary(),
    ])

    assert code == 0
    assert len(calls) == 2
    assert result["status"] == "FLAKY-PASS"
    first, second = result["attempts"]
    assert first["summary"] == {
        "stop_reason": "transport-error",
        "error": "fixture transport interrupted",
    }
    assert first["run_id"] == calls[0]["run_id"]
    assert first["fails"][0].startswith("stop_reason=transport-error")
    assert (root / first["events_file"]).is_file()
    assert second["fails"] == []
    assert "Итог: 0 first-pass, 1 rerun-pass, 0 fail, 0 skip" in output


def test_skipped_precondition_has_no_attempt_and_a_separate_count(smoke_runner):
    code, result, calls, output, _root = smoke_runner([], skip=True)

    assert code == 0
    assert calls == []
    assert result == {"status": "SKIP", "reason": "ssh host fixture-host unavailable"}
    assert "Итог: 0 first-pass, 0 rerun-pass, 0 fail, 1 skip" in output


def test_import_does_not_replace_stdout_or_change_sys_path():
    original_stdout = sys.stdout
    original_path = list(sys.path)

    _load_runner()

    assert sys.stdout is original_stdout
    assert sys.path == original_path
