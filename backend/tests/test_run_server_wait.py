"""Stage 1 (John 2026-10-07): readable child output and waiting for a job without sleep loops."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

from app.application.code_agent.tools import _background_jobs, _run


def test_python_children_get_utf8_unless_set_explicitly(monkeypatch):
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    env = _run._agent_child_env()
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    monkeypatch.setenv("PYTHONIOENCODING", "cp1251")
    assert _run._agent_child_env()["PYTHONIOENCODING"] == "cp1251"  # explicit setting wins
    assert "PYTHONUTF8" not in _run._agent_child_env({"PYTHONUTF8": None})


def test_logs_wait_returns_when_the_job_finishes_with_readable_cyrillic(tmp_path: Path):
    script = tmp_path / "job.py"
    script.write_text("import time\ntime.sleep(2)\nprint('Загрузка модели… готово')\n", encoding="utf-8")
    with mock.patch.object(_background_jobs, "_state_dir", return_value=tmp_path / "background_jobs"):
        started = _run.tool_run_server(tmp_path, action="start", command=f'"{sys.executable}" "{script}"', kind="job")
        try:
            begin = time.monotonic()
            logs = _run.tool_run_server(tmp_path, action="logs", pid=int(started["pid"]), kind="job", wait_seconds=60)
            elapsed = time.monotonic() - begin
            assert logs["status"] == "completed", logs["text"]
            assert elapsed < 30  # returned when the job ended, not after the whole wait
            assert "Загрузка модели… готово" in logs["text"]
            assert "(ожидание" in logs["text"]
            quick = _run.tool_run_server(tmp_path, action="logs", pid=int(started["pid"]), kind="job")
            assert "(ожидание" not in quick["text"]  # no wait unless asked
        finally:
            _run.tool_run_server(tmp_path, action="stop", pid=int(started["pid"]), kind="job")
            with _run._SERVERS_LOCK:
                _run._LIVE_SERVERS.clear()


def test_wait_is_bounded_and_ignores_garbage():
    handle = mock.Mock(kind="server", log_path=Path("missing.log"))
    handle.proc.poll.return_value = None
    with mock.patch.object(_run, "_MAX_LOG_WAIT_SECONDS", 1.5):
        assert 1.0 <= _run._wait_for_process(handle, 999) < 4  # capped
    assert _run._wait_for_process(handle, "abc") == 0.0
    assert _run._wait_for_process(handle, None) == 0.0
