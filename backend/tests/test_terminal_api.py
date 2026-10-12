"""Console panel API and Elira's run_bash(terminal=…) (John 2026-10-12: Elira has full control)."""
from __future__ import annotations

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

pytestmark = pytest.mark.skipif(os.name != "nt", reason="ConPTY terminal tabs are Windows-only")

from app.api.routes import terminal_routes  # noqa: E402
from app.application.agent_kernel.builtin_arguments import validate_builtin_arguments  # noqa: E402
from app.application.agent_kernel.impact_policy import tool_call_is_change  # noqa: E402
from app.application.code_agent.tools._run import tool_run_bash  # noqa: E402
from app.application.terminal import sessions  # noqa: E402


@pytest.fixture
def manager(monkeypatch):
    owner = sessions.TerminalManager()
    monkeypatch.setattr(sessions, "manager", owner)
    monkeypatch.setattr(terminal_routes, "manager", owner)
    yield owner
    owner.close_all()


@pytest.fixture
def client(manager):
    app = FastAPI()
    app.include_router(terminal_routes.router)
    return TestClient(app)


def _receive_until(socket, needle: str, limit: float = 15) -> str:
    seen, deadline = "", time.monotonic() + limit
    while needle not in seen and time.monotonic() < deadline:
        seen += socket.receive_text()
    return seen


def test_a_foreign_page_gets_no_shell(client, tmp_path):
    created = client.post("/api/terminal/sessions", json={"profile": "cmd", "cwd": str(tmp_path)}).json()
    session_id = created["session"]["id"]
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(f"/api/terminal/sessions/{session_id}/ws",
                                      headers={"origin": "https://evil.example"}) as socket:
            socket.receive_text()
    assert refused.value.code == 4401


def test_the_app_types_into_a_tab_and_sees_its_output(client, tmp_path):
    state = client.post("/api/terminal/sessions", json={"profile": "powershell", "cwd": str(tmp_path)}).json()
    session_id = state["session"]["id"]
    assert {item["id"] for item in state["profiles"]} >= {"powershell", "cmd"}
    with client.websocket_connect(f"/api/terminal/sessions/{session_id}/ws",
                                  headers={"origin": "tauri://localhost"}) as socket:
        assert "133;A" in _receive_until(socket, "133;A")  # the replay shows the prompt
        socket.send_json({"type": "resize", "cols": 100, "rows": 20})
        socket.send_json({"type": "input", "data": "echo from-the-user\r"})
        assert "from-the-user" in _receive_until(socket, "from-the-user\r\n")
    listed = client.get("/api/terminal/sessions").json()["sessions"]
    assert [item["id"] for item in listed] == [session_id] and listed[0]["active"] is True
    assert client.delete(f"/api/terminal/sessions/{session_id}").json()["sessions"] == []


def test_elira_runs_in_a_tab_and_reads_its_screen(manager, tmp_path):
    ran = tool_run_bash(tmp_path, command="echo 'от Elira'", terminal="new")
    assert ran["ok"] is True and ran["exit_code"] == 0 and ran["text"].endswith("exit=0\nот Elira")
    screen = tool_run_bash(tmp_path, command="", terminal=ran["terminal"])
    assert screen["ok"] is True and "от Elira" in screen["text"]
    again = tool_run_bash(tmp_path, command="Get-Item C:\\no-such-item", terminal="active")
    assert again["terminal"] == ran["terminal"] and again["ok"] is False and again["exit_code"] == 1
    missing = tool_run_bash(tmp_path, command="echo x", terminal="deadbeef")
    assert missing["error"] == "terminal_not_found" and ran["terminal"] in missing["text"]


def test_reading_a_tab_needs_no_approval_and_its_arguments_validate():
    assert tool_call_is_change("run_bash", {"command": "", "terminal": "active"}) is False
    assert tool_call_is_change("run_bash", {"command": "nslookup ya.ru", "terminal": "active"}) is True
    assert validate_builtin_arguments("run_bash", {"command": "ipconfig", "terminal": "active"}) is None


def test_open_tabs_are_named_in_the_run_instructions(manager, tmp_path):
    # Live run 6ec5e95a: "посмотри в терминале" started a fresh PowerShell instead of reading the tab.
    assert sessions.open_tabs_note() == ""
    tab = manager.create("cmd", tmp_path, title="Мой cmd")
    note = sessions.open_tabs_note()
    assert f"«Мой cmd» {tab.id} — cmd, активная" in note and 'terminal="active"' in note
