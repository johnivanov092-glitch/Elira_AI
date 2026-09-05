import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent import agent_loop
from app.application.code_agent.tools import _shell, _web


@pytest.mark.parametrize("entrypoint", ["browser", "fallback", "batch_fallback"])
def test_stop_owns_browser_worker_and_prevents_late_actions(monkeypatch, entrypoint):
    entered = threading.Event()
    closed = threading.Event()
    driver_closed = threading.Event()
    async def goto(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    page = SimpleNamespace(goto=goto)
    browser = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock(side_effect=lambda: closed.set()))
    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        async def __aexit__(self, *args):
            driver_closed.set()
    monkeypatch.setattr("playwright.async_api.async_playwright", Playwright)
    monkeypatch.setattr("app.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    action = AsyncMock(return_value=True)
    monkeypatch.setattr(_web, "_apply_action", action)
    monkeypatch.setattr(_web, "_fetch_one", lambda url, limit: _web._render_fallback(url, limit))
    run_id = "browser-stop-" + entrypoint
    agent_loop._register_run(run_id)
    results = []
    def worker():
        token = _shell.set_current_run_id(run_id)
        try:
            if entrypoint == "browser":
                results.append(_web.tool_browser(url="https://example.test", actions=[{"click": "Submit"}]))
            elif entrypoint == "batch_fallback":
                results.append(_web.tool_web_fetch(urls=["https://example.test"], store=False))
            else:
                results.append(_web._render_fallback("https://example.test", 500))
        finally:
            _shell.reset_current_run_id(token)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        assert agent_loop.request_cancel(run_id)
        assert closed.is_set() and driver_closed.is_set()
        thread.join(3)
        assert not thread.is_alive()
        action.assert_not_awaited()
        assert results
        assert run_id not in _shell._RUN_CANCEL_CALLBACKS
    finally:
        agent_loop.request_cancel(run_id)
        thread.join(3)
        agent_loop._unregister_run(run_id)


def test_stop_before_browser_registration_never_launches(monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(_web, "_browser_render_async", launch)
    run_id = "browser-stopped-before-setup"
    agent_loop._register_run(run_id)
    agent_loop.request_cancel(run_id)
    token = _shell.set_current_run_id(run_id)
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            _web._browser_render("https://example.test", None, 500)
        launch.assert_not_awaited()
    finally:
        _shell.reset_current_run_id(token)
        agent_loop._unregister_run(run_id)
