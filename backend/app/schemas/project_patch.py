from __future__ import annotations

from pydantic import BaseModel, Field


class PreviewProjectPatchRequest(BaseModel):
    path: str = Field(..., min_length=1)
    new_content: str = Field(..., min_length=0)
    max_chars: int = Field(default=20000, ge=1000, le=200000)


class ApplyProjectPatchRequest(BaseModel):
    path: str = Field(..., min_length=1)
    new_content: str = Field(..., min_length=0)


class ReplaceInFileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    old_text: str = Field(..., min_length=1)
    new_text: str = Field(..., min_length=0)
    max_chars: int = Field(default=20000, ge=1000, le=200000)
