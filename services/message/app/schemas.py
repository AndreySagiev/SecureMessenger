from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime

class MessageCreate(BaseModel):
    conversation_id: UUID | None = None
    recipient_id: UUID | None = None
    content: str = Field(..., min_length=1, max_length=4000)

class MessageOut(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    content: str
    created_at: datetime
    is_deleted: bool = False

    class Config:
        from_attributes = True

class ConversationOut(BaseModel):
    id: UUID
    name: str | None
    is_group: bool
    created_at: datetime

    class Config:
        from_attributes = True

class ConversationCreate(BaseModel):
    is_group: bool = False
    member_ids: list[UUID] = []
    my_display_name: str | None = None
    peer_display_name: str | None = None

class ConversationUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
