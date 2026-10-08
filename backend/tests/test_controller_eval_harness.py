from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "tests" / "smokes" / "controller_eval.py"
CASE_IDS = {"vault_restore", "library_roundtrip", "telegram_typed_roundtrip"}


def _run_harness(tmp_path: Path, *, injection: str = "") -> tuple[subprocess.CompletedProcess[str], dict]:
    report_dir = tmp_path / "report"
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(ROOT / "backend"), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if injection:
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "import controller_eval as module\n"
            + injection
            + f"\nraise SystemExit(module.main(['--output-dir', {str(report_dir)!r}]))\n"
        )
        command = [sys.executable, "-X", "utf8", "-c", code]
    else:
        command = [sys.executable, "-X", "utf8", str(SCRIPT), "--output-dir", str(report_dir)]
    result = subprocess.run(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False)
    assert (report_dir / "results.json").is_file(), f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads((report_dir / "results.json").read_text(encoding="utf-8"))
    assert set(report["cases"]) == CASE_IDS
    assert report["kind"] == "controller_contract"
    assert report["external_delivery_verified"] is False
    assert all(case["kind"] == "controller_contract" and case["external_delivery_verified"] is False for case in report["cases"].values())
    assert report["isolated_data_removed"] is True
    assert not Path(report["data_root"]).exists()
    stdout = json.loads(result.stdout.strip().splitlines()[-1])
    assert stdout["ok"] == report["ok"]
    assert stdout["kind"] == "controller_contract"
    assert stdout["external_delivery_verified"] is False
    return result, report


def test_controller_eval_runs_three_real_local_roundtrips_without_live_data(tmp_path, monkeypatch) -> None:
    original_data = tmp_path / "user-data"
    original_data.mkdir()
    sentinel = original_data / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("ELIRA_DATA_DIR", str(original_data))

    result, report = _run_harness(tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert report["ok"] is True
    assert report["passed"] == report["total"] == 3
    assert all(case["status"] == "PASS" for case in report["cases"].values())
    assert list(original_data.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    raw_report = (tmp_path / "report" / "results.json").read_text(encoding="utf-8")
    markdown = (tmp_path / "report" / "report.md").read_text(encoding="utf-8")
    for secret in ("CONTROLLER_VAULT_SECRET_CANARY_20261008", "isolated-controller-eval-passphrase", "100200300:CONTROLLER_FAKE_TOKEN_NEVER_SENT"):
        assert secret not in raw_report + markdown + result.stdout + result.stderr
    assert "Telegram transport/polling stubbed" in markdown


def test_failed_library_case_still_deletes_uploaded_row_and_file_and_exits_nonzero(tmp_path) -> None:
    injection = '''
from unittest.mock import patch
real_library = module._library_roundtrip
def injected_library(client, data_dir):
    from app.application.code_agent.tools import _memory
    real_tool = _memory.tool_library
    search_refused = False
    def refuse_search(*args, **kwargs):
        nonlocal search_refused
        if kwargs.get("action") == "search":
            search_refused = True
            return {"ok": False, "error": "injected_search_refusal", "text": "controller search refused"}
        return real_tool(*args, **kwargs)
    try:
        with patch.object(_memory, "tool_library", refuse_search):
            real_library(client, data_dir)
    except AssertionError as exc:
        assert search_refused
        assert client.get("/api/lib/list").json()["items"] == []
        assert list((data_dir / "uploads").iterdir()) == []
        raise AssertionError("injected Library search refusal left row and file removed") from exc
    raise AssertionError("Library search refusal was not rejected")
module._library_roundtrip = injected_library
'''
    result, report = _run_harness(tmp_path, injection=injection)

    assert result.returncode == 1
    assert report["ok"] is False
    assert report["passed"] == 2
    assert report["cases"]["library_roundtrip"]["status"] == "FAIL"
    assert report["cases"]["library_roundtrip"]["error"] == "AssertionError: injected Library search refusal left row and file removed"
    assert report["cases"]["vault_restore"]["status"] == "PASS"
    assert report["cases"]["telegram_typed_roundtrip"]["status"] == "PASS"


def test_failed_first_telegram_send_stops_receiver_without_success_log_or_false_pass(tmp_path) -> None:
    injection = '''
from unittest.mock import patch
real_telegram = module._telegram_typed_roundtrip
def injected_telegram(client, data_dir):
    from app.application.telegram import runtime, store
    try:
        with patch.object(runtime, "send_message", return_value={"ok": False, "description": "injected transport refusal"}):
            real_telegram(client, data_dir)
    except AssertionError as exc:
        assert str(exc) == "typed Telegram send lost successful evidence"
        assert store.get_telegram_log(chat_id=100200300)["count"] == 0
        assert not runtime._running
        assert runtime._bot_thread is None
        raise AssertionError("injected Telegram refusal left journal empty and receiver stopped")
    raise AssertionError("Telegram failure injection did not execute")
module._telegram_typed_roundtrip = injected_telegram
'''
    result, report = _run_harness(tmp_path, injection=injection)

    assert result.returncode == 1
    assert report["ok"] is False
    assert report["passed"] == 2
    assert report["cases"]["telegram_typed_roundtrip"]["status"] == "FAIL"
    assert report["cases"]["telegram_typed_roundtrip"]["error"] == "AssertionError: injected Telegram refusal left journal empty and receiver stopped"
    assert report["cases"]["vault_restore"]["status"] == "PASS"
    assert report["cases"]["library_roundtrip"]["status"] == "PASS"
