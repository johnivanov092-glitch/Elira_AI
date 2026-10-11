"""Mutable web CLI deadline framing at the process/cancellation boundary."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.agent_kernel.runtime_context import bind_runtime_context
from app.application.code_agent.tools import _web


@pytest.fixture(autouse=True)
def example_is_a_user_address():
    """These tests cover transport; the address policy (real addresses only) has its own tests."""
    with bind_runtime_context({"user_urls": json.dumps(["https://example.org"])}):
        yield


@pytest.fixture
def transport(tmp_path, monkeypatch):
    skill = tmp_path / "web-research"
    skill.mkdir()
    (skill / "web.py").write_text("", encoding="utf-8")
    process = SimpleNamespace(returncode=0, communicate=Mock(), stdout=SimpleNamespace(readline=Mock()))
    process.stdout.readline.return_value = b'{"web_deadline_epoch": 101.25}\n'
    popen = Mock(return_value=process)
    kill = Mock()
    unregister = Mock()
    clock = [100.0]
    monkeypatch.setattr(_web, "skill_root", lambda name: skill)
    monkeypatch.setattr(_web, "_skill_python", lambda root: sys.executable)
    monkeypatch.setattr(_web, "_agent_child_env", lambda: {})
    monkeypatch.setattr(_web, "_new_process_group_kwargs", lambda: {})
    monkeypatch.setattr(_web.subprocess, "Popen", popen)
    monkeypatch.setattr(_web, "get_current_run_id", lambda: "transport-run")
    monkeypatch.setattr(_web, "current_runtime_context",
                        lambda: {"step": "7", "user_urls": json.dumps(["https://example.org"])})
    monkeypatch.setattr(_web, "register_run_process", Mock(return_value="transport-run"))
    monkeypatch.setattr(_web, "unregister_run_process", unregister)
    monkeypatch.setattr(_web, "run_was_stopped", lambda rid: False)
    monkeypatch.setattr(_web, "_kill_proc_tree", kill)
    monkeypatch.setattr(_web.time, "time", lambda: clock[0])
    monkeypatch.setattr(_web.time, "monotonic", lambda: clock[0])
    return SimpleNamespace(root=tmp_path, process=process, popen=popen, kill=kill,
                           unregister=unregister, clock=clock)


def test_complete_deadline_line_bounds_pending_process_and_kills_its_tree(transport):
    elapsed = [101.0, 101.25]
    calls = 0

    def communicate(timeout=None):
        nonlocal calls
        calls += 1
        if timeout is None:
            return b"", b""
        transport.clock[0] = elapsed[calls - 1]
        # Windows communicate() provides no partial output while the process
        # remains alive. The deadline must come from the completed first line.
        raise subprocess.TimeoutExpired("web.py", timeout, output=None)

    transport.process.communicate.side_effect = communicate
    result = _web.tool_web_fetch(transport.root, urls=["https://example.org"])
    assert result["error"] == "web_time_budget" and result["web_research"]["stopped"]
    assert [call.kwargs.get("timeout") for call in transport.process.communicate.call_args_list] == [
        1, pytest.approx(0.25), None]
    transport.process.stdout.readline.assert_called_once_with()
    transport.kill.assert_called_once_with(transport.process)
    transport.unregister.assert_called_once_with("transport-run", transport.process)


def test_complete_frame_shortens_next_wait_and_final_result_keeps_sources(transport):
    frame = b'{"web_deadline_epoch": 100.5}\n'
    result_body = {"ok": True, "text": "Official page", "sources": [{"url": "https://example.org"}]}

    def read_line():
        transport.clock[0] = 100.2
        return frame

    transport.process.stdout.readline.side_effect = read_line
    transport.process.communicate.return_value = (json.dumps(result_body).encode(), b"")
    result = _web.tool_web_search(transport.root, queries=["пример", "example"])
    assert {key: value for key, value in result.items() if key != "labels"} == result_body  # labels: S1… map
    assert transport.process.communicate.call_args.kwargs["timeout"] == pytest.approx(0.3)
    command = transport.popen.call_args.args[0]
    assert "--native" in command and command[command.index("--step") + 1] == "7"
    transport.kill.assert_not_called()
    transport.unregister.assert_called_once_with("transport-run", transport.process)


def test_missing_first_frame_is_bounded_and_reader_is_unblocked_on_kill(transport):
    release = threading.Event()

    def read_line():
        transport.clock[0] = 162.0
        assert release.wait(timeout=2)
        return b""

    transport.process.stdout.readline.side_effect = read_line
    transport.kill.side_effect = lambda process: release.set()
    transport.process.communicate.return_value = (b"", b"")
    result = _web.tool_web_fetch(transport.root, urls=["https://example.org"])
    assert result["error"] == "web_time_budget"
    transport.kill.assert_called_once_with(transport.process)
    transport.unregister.assert_called_once_with("transport-run", transport.process)


def test_cancelled_child_with_only_a_deadline_frame_returns_cancelled(transport, monkeypatch):
    transport.process.returncode = 1
    transport.process.communicate.return_value = (b"", b"")
    monkeypatch.setattr(_web, "run_was_stopped", lambda rid: rid == "transport-run")
    result = _web.tool_web_fetch(transport.root, urls=["https://example.org"])
    assert result["error"] == "cancelled"
    transport.unregister.assert_called_once_with("transport-run", transport.process)


def test_single_result_json_without_deadline_frame_remains_compatible(transport):
    transport.process.stdout.readline.return_value = b'{"ok":true,"text":"read"}\n'
    transport.process.communicate.return_value = (b"", b"")
    result = _web.tool_web_fetch(transport.root, urls=["https://example.org"])
    assert result["ok"] is True and result["text"] == "read"
    transport.kill.assert_not_called()


@pytest.mark.parametrize("mode", [None, "quick", "deep"])
def test_fetch_mode_is_forwarded_only_when_declared(transport, mode):
    transport.process.communicate.return_value = (b'{"ok":true,"text":"read"}', b"")
    result = _web.tool_web_fetch(transport.root, urls=["https://example.org"], mode=mode)
    assert result["ok"]
    command = transport.popen.call_args.args[0]
    if mode is None:
        assert "--mode" not in command
    else:
        assert command[command.index("--mode") + 1] == mode


def test_real_pending_child_emits_deadline_and_is_reaped(tmp_path, monkeypatch):
    skill = tmp_path / "web-research"
    skill.mkdir()
    (skill / "web.py").write_text(
        "import json, sys, time\n"
        "frame = json.dumps({'web_deadline_epoch': time.time() + 1.4})\n"
        "sys.stdout.write(frame[:10]); sys.stdout.flush()\n"
        "time.sleep(0.1)\n"
        "sys.stdout.write(frame[10:] + '\\n'); sys.stdout.flush()\n"
        "time.sleep(5)\n", encoding="utf-8", newline="\n",
    )
    original_popen = subprocess.Popen
    children = []

    def popen(command, **kwargs):
        process = original_popen(command, **kwargs)
        if len(command) > 1 and command[1] == str(skill / "web.py"):
            children.append(process)
        return process

    unregister = Mock()
    monkeypatch.setattr(_web, "skill_root", lambda name: skill)
    monkeypatch.setattr(_web, "_skill_python", lambda root: sys.executable)
    monkeypatch.setattr(_web.subprocess, "Popen", popen)
    monkeypatch.setattr(_web, "get_current_run_id", lambda: "real-transport-run")
    monkeypatch.setattr(_web, "register_run_process", Mock(return_value="real-transport-run"))
    monkeypatch.setattr(_web, "unregister_run_process", unregister)
    monkeypatch.setattr(_web, "run_was_stopped", lambda rid: False)
    started = time.monotonic()
    try:
        result = _web.tool_web_fetch(tmp_path, urls=["https://example.org"])
        assert result["error"] == "web_time_budget"
        assert time.monotonic() - started < 4
        assert len(children) == 1 and children[0].poll() is not None
        unregister.assert_called_once_with("real-transport-run", children[0])
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=2)

