from pydantic import BaseModel, Field


class MemoryAddRequest(BaseModel):
    profile: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    source: str = "manual"


class MemorySearchRequest(BaseModel):
    profile: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    limit: int = 10
