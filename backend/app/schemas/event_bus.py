from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    event_id: str = Field("", description="Optional stable event id")
    event_type: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_agent_id: str = ""


class Event(BaseModel):
    id: int
    event_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source_agent_id: str = ""
    created_at: str


class EventListResponse(BaseModel):
    events: list[Event]
    total: int


class AgentMessageCreate(BaseModel):
    message_id: str = Field("", description="Optional stable message id")
    from_agent: str = ""
    to_agent: str = Field(..., min_length=1)
    content: Any = Field(default_factory=dict)
    reply_to: str = ""


class AgentMessage(BaseModel):
    id: int
    message_id: str
    from_agent: str = ""
    to_agent: str
    content: Any = Field(default_factory=dict)
    reply_to: str = ""
    read: bool = False
    created_at: str


class AgentMessageListResponse(BaseModel):
    messages: list[AgentMessage]
    total: int


class AgentMessageReadUpdate(BaseModel):
    read: bool = True


class SubscriptionCreate(BaseModel):
    subscriber_id: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    handler_name: str = ""


class Subscription(BaseModel):
    id: int
    subscriber_id: str
    event_type: str
    handler_name: str = ""
    created_at: str


class SubscriptionListResponse(BaseModel):
    subscriptions: list[Subscription]
    total: int
