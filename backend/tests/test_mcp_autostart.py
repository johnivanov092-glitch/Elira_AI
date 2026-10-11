"""Warm lifecycle races and prompt visibility over the canonical MCP registry."""
from __future__ import annotations

import threading
import time
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.application.agent_kernel.executor import ToolExecutionRequest, execute_tool
from app.application.code_agent.runtime_activation import RuntimeActivation
from app.application.tool_providers import mcp_provider, mcp_runtime
from app.application.tool_registry import runtime as tool_inventory


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    mcp_runtime.stop_all_servers()
    monkeypatch.setattr(mcp_runtime, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(mcp_runtime, "_WARM_THREAD", None)
    monkeypatch.setattr(mcp_runtime, "_WARM_READY", threading.Event())
    monkeypatch.setattr(mcp_runtime, "_WARM_CANCEL", threading.Event())
    mcp_runtime._LAST_ERROR.clear()
    yield mcp_runtime
    mcp_runtime.stop_all_servers()
    thread = mcp_runtime._WARM_THREAD
    if thread is not None:
        thread.join(3)
        assert not thread.is_alive(), "warm worker leaked from test"


def spec(server_id, **overrides):
    return {"id": server_id, "command": "fake-mcp", "enabled": True, **overrides}


class Client:
    server_info = {"name": "fake"}

    def __init__(self, **kwargs):
        self.alive = False
        self.stopped = False

    def start(self):
        self.alive = True

    def stop(self):
        self.alive = False
        self.stopped = True

    def is_alive(self):
        return self.alive

    def list_tools(self):
        return [{"name": "echo", "inputSchema": {"type": "object"}}]

    def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": name}], "isError": False}


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_autostart_round_trip_is_explicit_boolean(runtime, transport):
    entry = spec("warm", transport=transport, autostart=True, url="http://localhost/mcp")
    saved = runtime.save_servers([entry, spec("manual")])
    assert saved[0]["autostart"] is True
    assert saved[1]["autostart"] is False
    assert runtime.autostart_server_ids() == {"warm"}
    assert runtime._validate_server({**entry, "autostart": "false"}) is None


@pytest.mark.parametrize("interruption", ["stop", "shutdown", "config"])
def test_slow_handshake_keeps_inventory_live_and_cannot_publish_after_stop(
    runtime, monkeypatch, interruption,
):
    entered, release = threading.Event(), threading.Event()
    clients = []

    class Slow(Client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            clients.append(self)

        def start(self):
            entered.set()
            assert release.wait(3)
            super().start()

    monkeypatch.setattr(runtime, "McpClient", Slow)
    runtime.save_servers([spec("slow"), spec("ready")])
    ready = Client()
    ready.start()
    runtime._LIVE_CLIENTS["ready"] = ready
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.start_server, "slow")
        assert entered.wait(1)
        try:
            before = time.monotonic()
            status = {item["id"]: item["status"] for item in runtime.list_servers()}
            assert status == {"slow": "starting", "ready": "running"}
            assert runtime.get_live_client("ready") is ready
            assert time.monotonic() - before < 0.3
            if interruption == "stop":
                runtime.stop_server("slow")
            elif interruption == "shutdown":
                runtime.stop_all_servers()
            else:
                runtime.save_servers([spec("slow", args=["changed"]), spec("ready")])
        finally:
            release.set()
        assert future.result(timeout=2)["ok"] is False
    assert runtime.get_live_client("slow") is None
    assert clients[0].stopped is True


def test_background_warm_is_bounded_and_only_selected_servers_start(runtime, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    started = []

    class Slow(Client):
        def __init__(self, command, **kwargs):
            super().__init__(**kwargs)
            self.command = command

        def start(self):
            started.append(self.command)
            entered.set()
            assert release.wait(3)
            super().start()

    monkeypatch.setattr(runtime, "McpClient", Slow)
    runtime.save_servers([
        spec("warm", command="warm", autostart=True),
        spec("manual", command="manual"),
        spec("disabled", command="disabled", enabled=False, autostart=True),
    ])
    before = time.monotonic()
    result = runtime.start_autostart_servers()
    assert time.monotonic() - before < 0.3 and result["server_ids"] == ["warm"]
    assert entered.wait(1)
    try:
        assert runtime.start_autostart_servers()["already_started"] is True
        before = time.monotonic()
        assert runtime.wait_for_autostart(timeout=0.01) is False
        assert time.monotonic() - before < 0.3
    finally:
        release.set()
    assert runtime.wait_for_autostart(timeout=2) is True
    assert started == ["warm"]


def test_one_failed_warm_server_does_not_prevent_other_tools(runtime, monkeypatch):
    class SometimesBroken(Client):
        def __init__(self, command, **kwargs):
            super().__init__(**kwargs)
            self.command = command

        def start(self):
            if self.command == "broken":
                raise mcp_runtime.McpError("handshake failed")
            super().start()

    monkeypatch.setattr(runtime, "McpClient", SometimesBroken)
    runtime.save_servers([
        spec("broken", command="broken", autostart=True),
        spec("good", command="good", autostart=True),
    ])
    runtime.start_autostart_servers()
    assert runtime.wait_for_autostart(timeout=2) is True
    status = {item["id"]: item for item in runtime.list_servers()}
    assert status["broken"]["status"] == "error"
    assert "handshake failed" in status["broken"]["last_error"]
    assert status["good"]["status"] == "running"


def test_shutdown_before_start_token_cannot_leave_a_warm_client(runtime, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    start = runtime._start_server

    def paused_start(server_id, **kwargs):
        entered.set()
        assert release.wait(3)
        return start(server_id, **kwargs)

    monkeypatch.setattr(runtime, "_start_server", paused_start)
    monkeypatch.setattr(runtime, "McpClient", Client)
    runtime.save_servers([spec("late", autostart=True)])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.start_server, "late", _cancel_event=runtime._WARM_CANCEL)
        assert entered.wait(1)
        runtime.stop_all_servers()
        release.set()
        assert future.result(timeout=2)["ok"] is False
    assert runtime.get_live_client("late") is None


def test_shutdown_interrupts_real_inflight_stdio_handshake(runtime, monkeypatch):
    from app.application.tool_providers.mcp_client import McpClient

    entered = threading.Event()
    clients = []

    class Observed(McpClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            clients.append(self)

        def _request(self, method, *args, **kwargs):
            if method == "initialize":
                entered.set()
            return super()._request(method, *args, **kwargs)

    monkeypatch.setattr(runtime, "McpClient", Observed)
    runtime.save_servers([spec(
        "pending", command=sys.executable, args=[str(Path(__file__).with_name("_mcp_fake_server.py"))],
        env={"FAKE_MCP_HANG_INIT": "1"}, autostart=True,
    )])
    runtime.start_autostart_servers()
    assert entered.wait(2)
    before = time.monotonic()
    runtime.stop_all_servers()
    assert time.monotonic() - before < 4
    assert runtime.wait_for_autostart(timeout=3) is True
    assert clients and all(not client.is_alive() for client in clients)
    assert runtime.list_servers()[0]["status"] == "stopped"


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_client_stopped_before_start_cannot_create_transport(monkeypatch, transport):
    from app.application.tool_providers import mcp_client, mcp_http_client

    if transport == "stdio":
        client = mcp_client.McpClient(command=sys.executable)
        monkeypatch.setattr(mcp_client.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawn after stop"))
    else:
        client = mcp_http_client.McpHttpClient(url="http://localhost/mcp")
        monkeypatch.setattr(mcp_http_client.httpx, "Client", lambda *a, **kw: pytest.fail("HTTP after stop"))
    client.stop()
    with pytest.raises(mcp_client.McpError, match="stopped before start"):
        client.start()


@pytest.mark.parametrize("interruption", ["stop", "config"])
def test_existing_client_discovery_cannot_claim_success_after_stop(
    runtime, monkeypatch, interruption,
):
    runtime.save_servers([spec("ready")])
    monkeypatch.setattr(runtime, "McpClient", Client)
    assert runtime.start_server("ready")["ok"] is True

    def stop_during_discovery(client, server_id):
        if interruption == "stop":
            runtime.stop_server(server_id)
        else:
            runtime.save_servers([spec(server_id, args=["changed"])])
        return {"available_tool_count": 1, "available_tool_names": ["ready__echo"]}

    monkeypatch.setattr(runtime, "_discover_tool_names", stop_during_discovery)
    assert runtime.start_server("ready")["ok"] is False
    assert runtime.get_live_client("ready") is None


def test_autostart_base_schemas_keep_full_inventory_and_on_demand_dispatch(
    runtime, monkeypatch, tmp_path,
):
    names = ["browser_navigate", "browser_snapshot", "browser_click", "browser_type",
             "browser_fill_form", "browser_wait_for", "browser_take_screenshot",
             "browser_console_messages", "browser_tabs", "browser_close"]
    paper_names = ["search_papers", "search_arxiv", "read_arxiv_paper"]

    class Catalog(Client):
        def __init__(self, command, **kwargs):
            super().__init__(**kwargs)
            self.names = paper_names if command == "paper" else names

        def list_tools(self):
            return [{"name": name, "inputSchema": {"type": "object", "properties": {}}}
                    for name in self.names]

    monkeypatch.setattr(runtime, "McpClient", Catalog)
    runtime.save_servers([
        spec("playwright", autostart=True), spec("paper_search", command="paper", autostart=True),
        spec("other"),
    ])
    activation = RuntimeActivation.for_run(tmp_path, "warm-base", restored_state=(set(), {}, set()))
    update = activation.rebuild()
    visible = {item["function"]["name"] for item in update.schemas if "__" in item["function"]["name"]}
    expected = {f"playwright__{name}" for name in names[:7]} | {
        "paper_search__search_papers", "paper_search__search_arxiv",
    }
    assert visible == expected
    assert runtime.get_live_client("other") is None
    assert tool_inventory.get_tool("playwright__browser_close")["enabled"] is True
    assert tool_inventory.get_tool("paper_search__read_arxiv_paper")["enabled"] is True
    # Prompt visibility is not authorization; bypass may dispatch any advertised
    # tool through its existing provider, without a second registry.
    result = execute_tool(ToolExecutionRequest(
        run_id="warm-base", agent_id="code", project_scope_id="p", source="test",
        tool_name="playwright__browser_close", args={}, permission_mode="bypass",
    ), update.registry.dispatch_raw)
    assert result.status == "ok" and result.output["text"] == "browser_close"
    expanded = activation.apply_tool_result(
        "mcp", {"action": "tools", "server_id": "playwright", "query": "browser_console_messages"},
        {"ok": True, "server_id": "playwright"}, status="ok", user_message="Проверь консоль",
    )
    assert expected <= {item["function"]["name"] for item in expanded.schemas}
    assert "playwright__browser_console_messages" in expanded.registry.known_tools()
