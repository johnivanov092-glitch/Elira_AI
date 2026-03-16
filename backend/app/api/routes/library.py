from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pydantic import BaseModel

from app.services.library_service import (
    list_library_files,
    set_library_active,
    delete_library_file,
    build_library_context,
)

router = APIRouter(prefix="/api/library", tags=["library"])

class LibraryActivateRequest(BaseModel):
    filename: str
    active: bool

@router.get("/files")
def library_files():
    return JSONResponse(content=list_library_files(), media_type="application/json; charset=utf-8")

@router.get("/context")
def library_context():
    return JSONResponse(content=build_library_context(), media_type="application/json; charset=utf-8")

@router.post("/activate")
def library_activate(payload: LibraryActivateRequest):
    return JSONResponse(content=set_library_active(payload.filename, payload.active), media_type="application/json; charset=utf-8")

@router.delete("/files/{filename}")
def library_delete(filename: str):
    return JSONResponse(content=delete_library_file(filename), media_type="application/json; charset=utf-8")
