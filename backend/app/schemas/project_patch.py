from __future__ import annotations

from pydantic import BaseModel, Field


class PreviewProjectPatchRequest(BaseModel):
    path: str = Field(..., min_length=1)
    new_content: str = Field(..., min_length=0)
    max_chars: int = Field(default=20000, ge=1000, le=500000)


class ApplyProjectPatchRequest(BaseModel):
    path: str = Field(..., min_length=1)
    new_content: str = Field(..., min_length=0)


class ReplaceInFileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    old_text: str = Field(..., min_length=1)
    new_text: str = Field(..., min_length=0)
    max_chars: int = Field(default=20000, ge=1000, le=500000)


class RollbackProjectPatchRequest(BaseModel):
    path: str = Field(..., min_length=1)
    backup_id: str = Field(..., min_length=3)


class PatchBackupsRequest(BaseModel):
    path: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
