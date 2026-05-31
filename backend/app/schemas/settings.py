from pydantic import BaseModel, Field


class SettingsUpdateRequest(BaseModel):
    model_name: str = Field(..., min_length=1)
    profile_name: str = Field(..., min_length=1)
