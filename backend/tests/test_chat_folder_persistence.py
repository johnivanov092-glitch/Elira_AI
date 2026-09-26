"""One shared folder lifecycle across client origins, using the real session database."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import code_agent_routes as routes
from app.application.code_agent import sessions


def test_shared_folder_init_race_atomic_operations_and_preserved_chats(tmp_path, monkeypatch) -> None:
    database = tmp_path / "code_agent_sessions.db"
    monkeypatch.setattr(sessions, "DB_PATH", database)
    sessions.init_db()
    chat = sessions.create_session(title="Сохранённая переписка", model="local-model")
    sessions.update_session(chat["id"], {"turns": [{"kind": "user", "text": "Не удалять историю"}], "pinned": True})
    original_chat = sessions.get_session(chat["id"])
    app = FastAPI()
    app.include_router(routes.router)
    endpoint = "/api/code-agent/chat-folders"
    with TestClient(app) as client:
        assert client.get(endpoint).json() == {"ok": True, "state": None}
        seeds = [{
            "folders": [{"id": f"opaque/{index}", "name": f"Папка {index}"}],
            "assign": {chat["id"]: f"opaque/{index}"},
            "collapsed": {f"opaque/{index}": False},
        } for index in range(2)]
        barrier = threading.Barrier(2)

        def import_origin(seed):
            barrier.wait(timeout=5)
            return sessions.init_chat_folders(seed)

        with ThreadPoolExecutor(max_workers=2) as executor:
            imported = list(executor.map(import_origin, seeds))
        assert imported[0] == imported[1] and imported[0] in seeds
        initial = imported[0]
        folder_id = initial["folders"][0]["id"]
        empty = {"folders": [], "assign": {}, "collapsed": {}}
        # An empty new-origin cache never overwrites the first import.
        assert client.put(endpoint, json={"state": empty}).json() == {"ok": True, "state": initial}
        assert client.get(endpoint).json()["state"] == initial

        def patch(**operation):
            response = client.patch(endpoint, json=operation)
            assert response.status_code == 200, response.text
            return response.json()["state"]

        created = patch(operation="create", folder_id="folder:new", name="  Работа  ")
        assert created["folders"][-1] == {"id": "folder:new", "name": "Работа"}
        patch(operation="rename", folder_id="folder:new", name="Проекты")
        patch(operation="assign", session_id=chat["id"], folder_id="folder:new")
        folded = patch(operation="collapse", folder_id="folder:new", collapsed=True)
        assert folded["collapsed"]["folder:new"] is True
        assert folded["assign"][chat["id"]] == "folder:new"
        unassigned = patch(operation="assign", session_id=chat["id"], folder_id=None)
        assert chat["id"] not in unassigned["assign"]
        patch(operation="assign", session_id=chat["id"], folder_id="folder:new")

        for invalid in (
            {"operation": "collapse", "folder_id": folder_id, "collapsed": "false"},
            {"operation": "collapse", "folder_id": folder_id, "collapsed": 1},
            {"operation": "rename", "folder_id": folder_id, "name": "   "},
            {"operation": "assign", "session_id": chat["id"]},
        ):
            assert client.patch(endpoint, json=invalid).status_code == 422
        assert client.patch(endpoint, json={"operation": "assign", "session_id": chat["id"], "folder_id": "missing"}).status_code == 404
        replayed = patch(operation="create", folder_id=folder_id, name="Must not rename")
        assert replayed["folders"][0] == initial["folders"][0]
        for invalid in (
            {**empty, "folders": [{"id": "x", "name": "A"}, {"id": "x", "name": "B"}]},
            {**empty, "assign": {chat["id"]: "missing"}},
            {**empty, "collapsed": {"missing": False}},
            {**initial, "collapsed": {folder_id: "false"}},
        ):
            assert client.put(endpoint, json={"state": invalid}).status_code == 422

        # Concurrent different edits operate on the latest row, not stale copies.
        barrier = threading.Barrier(2)

        def create_concurrently(index):
            barrier.wait(timeout=5)
            return sessions.patch_chat_folders({"operation": "create", "folder_id": f"parallel-{index}", "name": f"Parallel {index}"})

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(create_concurrently, range(2)))
        assert {"parallel-0", "parallel-1"} <= {item["id"] for item in client.get(endpoint).json()["state"]["folders"]}

        deleted = patch(operation="delete", folder_id="folder:new")
        assert "folder:new" not in {item["id"] for item in deleted["folders"]}
        assert "folder:new" not in deleted["collapsed"]
        assert chat["id"] not in deleted["assign"]
        assert patch(operation="delete", folder_id="folder:new") == deleted
        assert sessions.get_session(chat["id"]) == original_chat
        sessions.init_db()  # Reopening the store preserves canonical state.
        assert client.get(endpoint).json()["state"] == deleted

        # An explicitly initialized empty state is distinct from no record.
        monkeypatch.setattr(sessions, "DB_PATH", tmp_path / "empty.db")
        sessions.init_db()
        assert client.put(endpoint, json={"state": empty}).json() == {"ok": True, "state": empty}
        assert client.put(endpoint, json={"state": initial}).json()["state"] == empty
