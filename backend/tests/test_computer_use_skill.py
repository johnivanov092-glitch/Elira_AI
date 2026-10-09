"""Standalone desktop skill and removal of its native entry point."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "skills/computer-use/computer.py"

@pytest.fixture
def computer():
    spec = importlib.util.spec_from_file_location("standalone_computer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def test_computer_type_sends_unicode_and_reports_partial_input(computer) -> None:
    # Review defect 8a30d2a3f717: pyautogui.write dropped Cyrillic but reported success.
    from unittest.mock import MagicMock

    gui = MagicMock()
    module = computer.__name__
    with (
        patch(f"{module}._load_pyautogui", return_value=(gui, None)),
        patch(f"{module}.os.name", "nt"),
        patch(f"{module}._send_unicode_text", return_value=(12, 12)) as send,
    ):
        ok = computer.run_action( action="type", text="Привет")
    send.assert_called_once_with("Привет")
    gui.write.assert_not_called()
    assert ok["ok"] is True

    with (
        patch(f"{module}._load_pyautogui", return_value=(gui, None)),
        patch(f"{module}.os.name", "nt"),
        patch(f"{module}._send_unicode_text", return_value=(4, 12)),
    ):
        partial = computer.run_action( action="type", text="Привет")
    assert partial["ok"] is False and partial["error"] == "input_rejected"

    with patch(f"{module}._load_pyautogui", return_value=(gui, None)), patch(f"{module}.os.name", "posix"):
        unsupported = computer.run_action( action="type", text="Привет")
    assert unsupported["ok"] is False and unsupported["error"] == "unsupported_text"
    gui.write.assert_not_called()



def test_screenshot_is_written_without_a_backend_import(computer, tmp_path):
    output = tmp_path / "screen.png"
    with patch.object(computer, "_grab_png", return_value=(b"png", (1280, 720), None)):
        result = computer.run_action(action="screenshot", output=str(output))
    assert result["ok"] and output.read_bytes() == b"png"
    assert (result["width"], result["height"]) == (1280, 720)
    assert "read_image" in result["text"]

def test_capture_failure_and_output_errors_are_explicit(computer, tmp_path):
    with patch.object(computer, "_grab_png", return_value=(None, None, "no desktop")):
        result = computer.run_action(action="screenshot", output=str(tmp_path / "screen.png"))
    assert not result["ok"] and result["error"] == "capture_failed"
    assert not (tmp_path / "screen.png").exists()
    with patch.object(computer, "_grab_png") as capture:
        assert computer.run_action(action="screenshot")["error"] == "output_required"
        assert computer.run_action(action="screenshot", output="bad.txt")["error"] == "invalid_output"
        capture.assert_not_called()

def test_real_cli_missing_output_returns_json_without_desktop_input():
    result = subprocess.run([sys.executable, str(SCRIPT), "screenshot"], capture_output=True,
                            text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout)["error"] == "output_required"

def test_catalog_and_no_desktop_group():
    from app.application.code_agent.task_skills import read_package
    from app.application.code_agent.capabilities import CAPABILITY_GROUPS
    assert read_package("computer-use", SCRIPT.parent)["name"] == "computer-use"
    assert "desktop" not in CAPABILITY_GROUPS
    assert "from app." not in SCRIPT.read_text(encoding="utf-8")
