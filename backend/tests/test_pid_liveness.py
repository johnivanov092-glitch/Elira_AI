"""An exited process whose handle is still open is not alive (stale agent.lock / job)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.application.code_agent.run_journal import _pid_alive
from app.application.code_agent.tools._background_jobs import process_identity, process_matches


def test_running_process_is_alive_and_exited_one_is_not_while_its_handle_is_open() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = process_identity(proc.pid)
        assert _pid_alive(proc.pid) is True
        assert identity is not None and process_matches(proc.pid, identity)
    finally:
        proc.kill()
        proc.wait()
    # Popen still holds the process handle here, as a supervisor would.
    assert _pid_alive(proc.pid) is False
    assert process_identity(proc.pid) is None
    assert process_matches(proc.pid, identity) is False


@pytest.mark.skipif(os.name != "nt", reason="the open-handle case is specific to Windows")
def test_handle_is_really_still_open_on_windows() -> None:
    import ctypes

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, proc.pid)
    try:
        assert handle, "the scenario needs the exited process object to still exist"
    finally:
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
    assert _pid_alive(proc.pid) is False
