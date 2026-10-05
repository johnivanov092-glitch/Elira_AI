"""Снимок state.json не обрывает прогон и не перезаписывается на каждый токен (решение Джона 2026-10-05).

2026-10-05 прогоны рабочей Elira падали с WinError 5: посторонний наблюдатель открывал state.json
в момент его атомарной замены. Журнал больше не пишет снимок на потоковых кусках, а сбой замены
не обрывает прогон — снимок досылается на следующем событии и в конце прогона.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from app.application.code_agent import run_journal as run_journal_module
from app.application.code_agent.run_journal import RunJournal


def _journal(tmp_path: Path, run_id: str = "snapshot-run") -> RunJournal:
    journal = RunJournal(run_id, runs_root=tmp_path / "runs")
    journal.start({"user_message": "Проверь сайт."}, {"missing": []})
    return journal


def test_streamed_tokens_do_not_rewrite_the_snapshot(tmp_path, monkeypatch):
    journal = _journal(tmp_path)
    writes = []
    real = run_journal_module._atomic_json
    monkeypatch.setattr(run_journal_module, "_atomic_json",
                        lambda path, payload, **kw: writes.append(Path(path).name) or real(path, payload, **kw))
    journal.append_event({"type": "step_started", "step": 1})
    for index in range(200):
        journal.append_event({"type": "delta", "step": 1, "text": f"кусок {index} "})
        journal.append_event({"type": "reasoning_delta", "step": 1, "text": "мысль"})
    journal.append_event({"type": "final_response", "step": 1, "text": "Готово."})
    assert writes.count("state.json") == 2  # step_started и final_response, не 400 потоковых кусков
    events = journal.events_path.read_text(encoding="utf-8")
    assert events.count('"type":"delta"') == 200  # текст по-прежнему в журнале событий


def test_failed_snapshot_replace_keeps_the_run_alive_and_is_resent(tmp_path, monkeypatch):
    journal = _journal(tmp_path)
    real = run_journal_module._atomic_json
    blocked = {"on": True}

    def flaky(path, payload, **kw):
        if Path(path).name == "state.json" and blocked["on"]:
            raise PermissionError(5, "Отказано в доступе")
        return real(path, payload, **kw)

    monkeypatch.setattr(run_journal_module, "_atomic_json", flaky)
    journal.append_event({"type": "step_started", "step": 1})  # не падает
    journal.append_event({"type": "tool_call", "step": 1, "tool": "web_fetch", "ok": True})
    blocked["on"] = False
    journal.append_event({"type": "delta", "step": 2, "text": "ответ"})  # грязный снимок досылается
    assert RunJournal.load("snapshot-run", runs_root=tmp_path / "runs").state["step"] == 2

    blocked["on"] = True
    journal.append_event({"type": "final_response", "step": 2, "text": "Готово."})
    journal.append_event({"type": "done", "ok": True, "stop_reason": "answer", "steps": 2})
    attempts = {"n": 0}

    def unblock_later(path, payload, **kw):
        if Path(path).name == "state.json":
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise PermissionError(5, "Отказано в доступе")
        return real(path, payload, **kw)

    monkeypatch.setattr(run_journal_module, "_atomic_json", unblock_later)
    journal.finish()  # итоговый статус не теряется: короткие повторы в конце прогона
    state = RunJournal.load("snapshot-run", runs_root=tmp_path / "runs").state
    assert state["stop_reason"] == "answer" and state["last_response"] == "Готово."


@pytest.mark.skipif(os.name != "nt", reason="блокировка замены открытого файла — поведение Windows")
def test_real_concurrent_reader_cannot_abort_a_streaming_run(tmp_path):
    """Как 05.10: другой поток всё время трогает state.json (resolve открывает файл)."""
    journal = _journal(tmp_path, "reader-run")
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                journal.state_path.resolve()
                journal.state_path.read_bytes()
            except OSError:
                pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        for step in range(1, 60):
            journal.append_event({"type": "step_started", "step": step})
            journal.append_event({"type": "tool_call", "step": step, "tool": "web_fetch", "ok": True})
            for index in range(20):
                journal.append_event({"type": "delta", "step": step, "text": f"{index} "})
        journal.append_event({"type": "final_response", "step": 59, "text": "Готово."})
        journal.append_event({"type": "done", "ok": True, "stop_reason": "answer", "steps": 59})
    finally:
        stop.set()
        thread.join()
    journal.finish()
    state = RunJournal.load("reader-run", runs_root=tmp_path / "runs").state
    assert state["stop_reason"] == "answer" and state["step"] == 59
