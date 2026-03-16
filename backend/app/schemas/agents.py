from typing import Any
from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    model_name: str = Field(..., min_length=1)
    profile_name: str = Field(..., min_length=1)
    user_input: str = Field(..., min_length=1)
    history: list[dict[str, Any]] = Field(default_factory=list)
    use_memory: bool = True
    use_library: bool = True
