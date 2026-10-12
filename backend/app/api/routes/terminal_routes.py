"""Console panel: shell tabs over WebSocket (John 2026-10-12: Elira has full control).

A tab is a shell in a pseudo-console (application/terminal/sessions.py). The
WebSocket carries keystrokes and resizes in and raw output out. The HTTP auth
middleware does not see WebSockets, so the socket checks the same rule itself:
a loopback caller from the app's own origin, or the API token (?token=). A page
in the user's browser must not get a shell.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from app.application.terminal.sessions import manager, profiles
from app.core.auth import is_authorized

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = "powershell"
    cwd: str = ""
    cols: int = Field(default=120, ge=20, le=400)
    rows: int = Field(default=30, ge=5, le=200)


class ActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str


def _state() -> dict:
    return {"sessions": manager.list(),
            "profiles": [{"id": key, "title": value["title"]} for key, value in profiles().items()]}


@router.get("/sessions")
def list_sessions() -> dict:
    return _state()


@router.post("/sessions")
def create_session(request: CreateRequest) -> dict:
    if request.profile not in profiles():
        raise HTTPException(status_code=400, detail=f"Нет оболочки «{request.profile}» на этом компьютере")
    try:
        session = manager.create(request.profile, Path(request.cwd) if request.cwd else None,
                                 cols=request.cols, rows=request.rows)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"session": session.info(), **_state()}


@router.delete("/sessions/{session_id}")
def close_session(session_id: str) -> dict:
    if not manager.close(session_id):
        raise HTTPException(status_code=404, detail="Терминал не найден")
    return _state()


@router.post("/active")
def set_active(request: ActiveRequest) -> dict:
    if not manager.set_active(request.id):
        raise HTTPException(status_code=404, detail="Терминал не найден")
    return _state()


def _websocket_allowed(websocket: WebSocket) -> bool:
    token = websocket.query_params.get("token", "")
    return is_authorized(websocket.client.host if websocket.client else None,
                         f"Bearer {token}" if token else None, origin=websocket.headers.get("origin"))


@router.websocket("/sessions/{session_id}/ws")
async def terminal_socket(websocket: WebSocket, session_id: str) -> None:
    if not _websocket_allowed(websocket):
        await websocket.close(code=4401)
        return
    session = manager.get(session_id)
    if session is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    replay, unsubscribe = session.subscribe(lambda data: loop.call_soon_threadsafe(queue.put_nowait, data))

    async def send() -> None:
        if replay:
            await websocket.send_text(replay)
        while (data := await queue.get()) is not None:
            await websocket.send_text(data)
        await websocket.close(code=1000)

    async def receive() -> None:
        while True:
            message = json.loads(await websocket.receive_text())
            kind: Literal["input", "resize"] | None = message.get("type")
            if kind == "input" and isinstance(message.get("data"), str):
                await asyncio.to_thread(session.write, message["data"])
            elif kind == "resize":
                await asyncio.to_thread(session.resize, message.get("cols", 120), message.get("rows", 30))

    tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
        for task in tasks:
            task.cancel()
