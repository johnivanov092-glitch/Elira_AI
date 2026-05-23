from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.event_bus import (
    AgentMessage,
    AgentMessageCreate,
    AgentMessageListResponse,
    AgentMessageReadUpdate,
    Event,
    EventCreate,
    EventListResponse,
    Subscription,
    SubscriptionCreate,
    SubscriptionListResponse,
)
import app.application.event_bus as bus


router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])


@router.post("/events", response_model=Event, summary="Emit event")
def create_event(body: EventCreate):
    try:
        return bus.emit_event(
            event_id=body.event_id or None,
            event_type=body.event_type,
            payload=body.payload,
            source_agent_id=body.source_agent_id or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/events", response_model=EventListResponse, summary="List events")
def get_events(
    event_type: str | None = Query(None),
    source_agent_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    events, total = bus.list_events(
        event_type=event_type,
        source_agent_id=source_agent_id,
        limit=limit,
        offset=offset,
    )
    return EventListResponse(events=events, total=total)


@router.post("/messages", response_model=AgentMessage, summary="Send message to agent")
def create_message(body: AgentMessageCreate):
    try:
        return bus.send_message(
            message_id=body.message_id or None,
            from_agent=body.from_agent or None,
            to_agent=body.to_agent,
            content=body.content,
            reply_to=body.reply_to or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/agents/{agent_id}/messages", response_model=AgentMessageListResponse, summary="List agent inbox")
def get_agent_messages(
    agent_id: str,
    unread_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    messages, total = bus.get_agent_messages(
        agent_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return AgentMessageListResponse(messages=messages, total=total)


@router.patch("/messages/{message_id}/read", response_model=AgentMessage, summary="Mark message read/unread")
def patch_message_read(message_id: str, body: AgentMessageReadUpdate):
    message = bus.mark_message_read(message_id, read=body.read)
    if not message:
        raise HTTPException(404, f"Message '{message_id}' not found")
    return message


@router.post("/subscriptions", response_model=Subscription, summary="Subscribe to event type")
def create_subscription(body: SubscriptionCreate):
    try:
        return bus.subscribe(
            subscriber_id=body.subscriber_id,
            event_type=body.event_type,
            handler_name=body.handler_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/subscriptions", response_model=SubscriptionListResponse, summary="List subscriptions")
def get_subscriptions(
    subscriber_id: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    subscriptions, total = bus.list_subscriptions(
        subscriber_id=subscriber_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return SubscriptionListResponse(subscriptions=subscriptions, total=total)


@router.delete("/subscriptions", summary="Remove subscription")
def delete_subscription(
    subscriber_id: str = Query(..., min_length=1),
    event_type: str = Query(..., min_length=1),
):
    return bus.unsubscribe(subscriber_id, event_type)
