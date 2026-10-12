"""Shell tabs of the console panel on a real pseudo-console (John 2026-10-12: Elira has full control)."""
from __future__ import annotations

import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="ConPTY terminal tabs are Windows-only")

from app.application.terminal.sessions import TerminalManager, profiles  # noqa: E402


@pytest.fixture
def manager(tmp_path):
    owner = TerminalManager()
    yield owner
    owner.close_all()


def test_powershell_command_output_and_exit_code_come_back_clean(manager, tmp_path):
    session = manager.create("powershell", tmp_path)
    first = session.run("echo 'Привет, терминал'")
    assert first == {"ok": True, "text": "Привет, терминал", "exit_code": 0}
    failed = session.run("Get-Item C:\\definitely-not-here")
    assert failed["ok"] is False and failed["exit_code"] == 1 and "definitely-not-here" in failed["text"]
    long = session.run("Write-Output ('x' * 10); " + "echo " + "'" + "долгая команда " * 12 + "'")
    assert long["text"].splitlines()[0] == "xxxxxxxxxx"  # the wrapped echo is not part of the output
    assert "Привет, терминал" in session.read()


def test_cmd_tab_runs_without_exit_codes(manager, tmp_path):
    session = manager.create("cmd", tmp_path)
    result = session.run("echo Привет & ping -n 1 127.0.0.1")
    assert result["ok"] is True and result["exit_code"] is None
    assert result["text"].startswith("Привет") and "127.0.0.1" in result["text"]


def test_stop_sends_ctrl_c_and_the_shell_stays(manager, tmp_path):
    session = manager.create("powershell", tmp_path)
    started = time.monotonic()
    result = session.run("ping -t 127.0.0.1", should_stop=lambda: time.monotonic() - started > 2)
    assert result["error"] == "cancelled" and "127.0.0.1" in result["text"]
    assert session.run("echo still-here")["text"] == "still-here"


def test_a_running_command_makes_the_tab_busy(manager, tmp_path):
    session = manager.create("powershell", tmp_path)
    session.run("echo ready")
    session.write("Start-Sleep -Seconds 3\r")
    busy = session.run("echo other")
    assert busy["error"] == "terminal_busy"
    time.sleep(3.5)
    assert session.run("echo free")["text"] == "free"


def test_listeners_get_a_replay_then_live_output(manager, tmp_path):
    session = manager.create("powershell", tmp_path)
    session.run("echo before")
    chunks: list[str] = []
    seen = threading.Event()

    def listen(data):
        if data:
            chunks.append(data)
            if "after" in "".join(chunks):
                seen.set()
    replay, unsubscribe = session.subscribe(listen)
    assert "before" in replay
    session.run("echo after")
    assert seen.wait(5)
    unsubscribe()


def test_manager_tracks_the_active_tab(manager, tmp_path):
    first = manager.create("powershell", tmp_path)
    second = manager.create("cmd", tmp_path)
    assert manager.get("active") is second
    assert manager.set_active(first.id) and manager.get() is first
    assert {item["id"] for item in manager.list()} == {first.id, second.id}
    assert manager.close(second.id) and manager.get(second.id) is None
    assert "powershell" in profiles() and "cmd" in profiles()
