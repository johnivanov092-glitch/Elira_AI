from pydantic import BaseModel


class LibraryActivateRequest(BaseModel):
    filename: str
    active: bool
