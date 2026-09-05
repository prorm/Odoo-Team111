from datetime import datetime
from pydantic import BaseModel


class NoteCreate(BaseModel):
    content: str


class NoteUpdate(BaseModel):
    content: str


class NoteResponse(BaseModel):
    id: str
    content: str
    version: int
    tenant_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = False
