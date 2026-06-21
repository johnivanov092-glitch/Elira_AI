"""HTTP error-status contract for sync routes.

Regression guard: these endpoints used to return HTTP 200 with an
``{"ok": false, ...}`` body on error states. The frontend ``request()`` helper
only throws on non-2xx, so a 200-on-error was silently treated as success.
These tests pin the corrected status codes (404 for not-found, 5xx for upstream
faults). SSE/streaming routes are intentionally NOT covered here — their 200 is
legitimate because the error travels in the streamed body, not the status line.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.skills_routes import router as skills_router
from app.api.routes.advanced_routes import router as advanced_router


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── skills: file serving ────────────────────────────────────────────────────

def test_download_missing_file_returns_404():
    client = _client(skills_router)
    resp = client.get("/api/skills/download/does-not-exist.bin")
    assert resp.status_code == 404
    assert "does-not-exist.bin" in resp.json()["detail"]


def test_view_missing_file_returns_404():
    client = _client(skills_router)
    resp = client.get("/api/skills/view/does-not-exist.png")
    assert resp.status_code == 404
    assert "does-not-exist.png" in resp.json()["detail"]


# ── advanced: open saved project ────────────────────────────────────────────

def test_open_unknown_project_returns_404(monkeypatch):
    # Isolate from real SQLite state: stub the registry lookups so the route
    # deterministically reaches its not-found → 404 branch regardless of which
    # other tests ran first (the registry uses a shared on-disk DB).
    from app.application.advanced import projects_registry
    monkeypatch.setattr(projects_registry, "list_projects", lambda: {"projects": []})
    monkeypatch.setattr(projects_registry, "resolve_project", lambda _name: None)
    client = _client(advanced_router)
    resp = client.post("/api/advanced/projects/open", json={"id": "", "name": "no-such-project"})
    assert resp.status_code == 404
    assert "не найден" in resp.json()["detail"].lower()


# ── advanced: multi-agent fault mapping ─────────────────────────────────────

def test_multi_agent_non_dict_result_returns_502(monkeypatch):
    import app.application.workflows.multi_agent as ma
    monkeypatch.setattr(ma, "run_multi_agent_workflow", lambda **_: "not-a-dict")
    client = _client(advanced_router)
    resp = client.post("/api/advanced/multi-agent", json={"query": "x"})
    assert resp.status_code == 502
    assert resp.json()["ok"] is False


def test_multi_agent_exception_returns_500(monkeypatch):
    import app.application.workflows.multi_agent as ma

    def _boom(**_):
        raise RuntimeError("upstream blew up")

    monkeypatch.setattr(ma, "run_multi_agent_workflow", _boom)
    client = _client(advanced_router)
    resp = client.post("/api/advanced/multi-agent", json={"query": "x"})
    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert "upstream blew up" in body["error"]


def test_multi_agent_success_still_returns_200(monkeypatch):
    import app.application.workflows.multi_agent as ma
    monkeypatch.setattr(ma, "run_multi_agent_workflow", lambda **_: {"answer": "ok"})
    client = _client(advanced_router)
    resp = client.post("/api/advanced/multi-agent", json={"query": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["answer"] == "ok"
