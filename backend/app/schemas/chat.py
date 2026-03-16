from typing import Any
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model_name: str
    profile_name: str = "default"
    user_input: str
    history: list[ChatMessage] = Field(default_factory=list)

class ChatResponse(BaseModel):
    ok: bool
    answer: str
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
