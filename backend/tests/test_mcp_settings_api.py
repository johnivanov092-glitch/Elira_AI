from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes import mcp_routes
from app.application.tool_providers import mcp_runtime
from app.core.auth import make_auth_middleware


@pytest.fixture
def client(tmp_path, monkeypatch):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "id": "test", "command": sys.executable,
        "args": [str(Path(__file__).with_name("_mcp_fake_server.py"))],
        "env": {"SECRET": "private-value"}, "enabled": True,
    }]}), encoding="utf-8")
    monkeypatch.setattr(mcp_runtime, "CONFIG_PATH", config)
    monkeypatch.setattr(mcp_runtime, "_LIVE_CLIENTS", {})
    monkeypatch.setattr(mcp_runtime, "_LIVE_SPECS", {})
    monkeypatch.setattr(mcp_runtime, "_LAST_ERROR", {})
    app = FastAPI()
    app.middleware("http")(make_auth_middleware(set()))
    app.include_router(mcp_routes.router)
    with TestClient(app) as test_client:
        yield test_client
    mcp_runtime.stop_all_servers()


def test_real_process_lifecycle_uses_same_runtime(client):
    assert client.get("/api/mcp/servers").json()["servers"][0]["status"] == "stopped"
    path = "/api/mcp/servers/test/lifecycle"
    assert client.post(path, json={"action": "start"}).status_code == 200
    live = mcp_runtime.get_live_client("test")
    assert live is not None
    assert client.get("/api/mcp/servers").json()["servers"][0]["status"] == "running"
    assert client.post(path, json={"action": "start"}).status_code == 200
    assert mcp_runtime.get_live_client("test") is live
    assert client.post(path, json={"action": "restart"}).status_code == 200
    assert not live.is_alive()
    assert mcp_runtime.get_live_client("test") is not live
    assert client.post(path, json={"action": "stop"}).status_code == 200
    assert client.post(path, json={"action": "stop"}).status_code == 200
    assert mcp_runtime.get_live_client("test") is None


def test_list_does_not_expose_config_or_credentials(client):
    mcp_runtime._LAST_ERROR["test"] = "transport said private-value token=another-secret"
    response = client.get("/api/mcp/servers")
    assert "private-value" not in response.text
    assert "another-secret" not in response.text
    row = response.json()["servers"][0]
    assert set(row) == {"id", "description", "transport", "enabled", "status", "last_error"}
    assert row["status"] == "error"
    client.post("/api/mcp/servers/test/lifecycle", json={"action": "stop"})
    assert client.get("/api/mcp/servers").json()["servers"][0]["status"] == "stopped"


def test_unknown_server_and_operations_are_rejected(client):
    assert client.post("/api/mcp/servers/missing/lifecycle", json={"action": "start"}).status_code == 404
    assert client.post("/api/mcp/servers/test/lifecycle", json={"action": "shell"}).status_code == 422
    assert client.post("/api/mcp/servers/test/lifecycle", json={"action": "start", "command": "bad"}).status_code == 422


def test_failure_stays_failed_and_redacts_error(client, monkeypatch):
    monkeypatch.setattr(mcp_runtime, "start_server", lambda sid: {"ok": False, "error": "private-value"})
    response = client.post("/api/mcp/servers/test/lifecycle", json={"action": "start"})
    assert response.status_code == 409
    assert "private-value" not in response.text


def test_locked_vault_returns_actionable_error(client, monkeypatch):
    from app.infrastructure.secrets import vault
    specs = mcp_runtime.list_servers()
    specs[0]["env_secret_refs"] = {"TOKEN": "vault:example"}
    mcp_runtime.save_servers(specs)
    monkeypatch.setattr(vault, "status", lambda: {"locked": True})
    response = client.post("/api/mcp/servers/test/lifecycle", json={"action": "start"})
    assert response.status_code == 409
    assert "Секреты" in response.json()["detail"]
    assert "vault:example" not in response.text


def test_vault_value_echo_is_not_exposed_after_vault_locks(client):
    specs = mcp_runtime.list_servers()
    specs[0]["env_secret_refs"] = {"TOKEN": "vault:example"}
    mcp_runtime.save_servers(specs)
    mcp_runtime._LAST_ERROR["test"] = "unlabelled-vault-credential-echo"
    response = client.get("/api/mcp/servers")
    assert "unlabelled-vault-credential-echo" not in response.text


def test_editor_sees_masked_config_and_keeps_stored_secret(client):
    config = client.get("/api/mcp/servers/test/config").json()["config"]
    assert config["env"] == {"SECRET": "●●●"} and "status" not in config
    config["description"] = "Тестовый сервер"
    assert client.put("/api/mcp/servers/test", json={"config": config}).status_code == 200
    stored = json.loads(mcp_runtime.CONFIG_PATH.read_text(encoding="utf-8"))["servers"][0]
    assert stored["description"] == "Тестовый сервер"
    assert stored["env"] == {"SECRET": "private-value"}
    assert client.get("/api/mcp/servers").json()["servers"][0]["description"] == "Тестовый сервер"


def test_new_plaintext_credential_is_refused(client):
    config = client.get("/api/mcp/servers/test/config").json()["config"]
    config["env"]["GITHUB_TOKEN"] = "ghp_plain"
    response = client.put("/api/mcp/servers/test", json={"config": config})
    assert response.status_code == 422 and "Секреты" in response.json()["detail"]
    assert "ghp_plain" not in mcp_runtime.CONFIG_PATH.read_text(encoding="utf-8")


def test_add_switch_off_and_delete(client):
    new = {"id": "docs", "transport": "http", "url": "https://example.org/mcp", "description": "Документация"}
    assert client.post("/api/mcp/servers", json={"config": new}).status_code == 200
    assert client.post("/api/mcp/servers", json={"config": new}).status_code == 409
    assert client.post("/api/mcp/servers", json={"config": {"id": "bad", "command": ""}}).status_code == 422
    assert client.post("/api/mcp/servers/docs/enabled", json={"enabled": False}).status_code == 200
    response = client.post("/api/mcp/servers/docs/lifecycle", json={"action": "start"})
    assert response.status_code == 409 and "выключен" in response.json()["detail"]
    assert client.delete("/api/mcp/servers/docs").status_code == 200
    assert [row["id"] for row in client.get("/api/mcp/servers").json()["servers"]] == ["test"]


def test_changed_entry_restarts_the_live_process(client):
    path = "/api/mcp/servers/test/lifecycle"
    assert client.post(path, json={"action": "start"}).status_code == 200
    live = mcp_runtime.get_live_client("test")
    raw = json.loads(mcp_runtime.CONFIG_PATH.read_text(encoding="utf-8"))
    raw["servers"][0]["args"].append("--changed")
    mcp_runtime.CONFIG_PATH.write_text(json.dumps(raw), encoding="utf-8")  # edited past save_servers
    result = mcp_runtime.start_server("test")
    assert result["ok"] and result.get("restarted_for_changed_config") is True
    assert not live.is_alive() and mcp_runtime.get_live_client("test") is not live
    assert mcp_runtime.start_server("test")["already_running"] is True
