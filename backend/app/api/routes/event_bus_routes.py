from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

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
from app.application.event_bus import runtime as bus


router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])

_STREAM_POLL_SECONDS = 0.5
_STREAM_HEARTBEAT_SECONDS = 10.0


def _stream_frame(*, event: str, data: dict, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def _stream_cursor(after_id: int | None, last_event_id: str | None) -> int:
    if last_event_id is not None:
        try:
            cursor = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be a non-negative integer") from exc
        if cursor < 0:
            raise HTTPException(400, "Last-Event-ID must be a non-negative integer")
        return cursor
    if after_id is not None:
        return after_id
    return bus.latest_event_id()


async def _stream_events(
    request: Request,
    *,
    initial_cursor: int,
    replay_through: int | None,
):
    cursor = initial_cursor
    last_heartbeat = time.monotonic()
    yield _stream_frame(
        event="stream/ready",
        event_id=cursor,
        data={"type": "stream/ready", "cursor": cursor},
    )
    while True:
        events = bus.list_events_after(
            after_id=cursor,
            through_id=replay_through,
            limit=100,
        )
        for item in events:
            cursor = int(item["id"])
            yield _stream_frame(
                event=str(item["event_type"]),
                event_id=cursor,
                data=item,
            )
        if replay_through is not None and (cursor >= replay_through or not events):
            return
        if await request.is_disconnected():
            return
        now = time.monotonic()
        if now - last_heartbeat >= _STREAM_HEARTBEAT_SECONDS:
            yield ": heartbeat\n\n"
            last_heartbeat = now
        await asyncio.sleep(_STREAM_POLL_SECONDS)


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


@router.get("/events/stream", summary="Stream durable events")
def stream_events(
    request: Request,
    after_id: int | None = Query(None, ge=0),
    follow: bool = Query(True),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    initial_cursor = _stream_cursor(after_id, last_event_id)
    replay_through = None if follow else bus.latest_event_id()

    return StreamingResponse(
        _stream_events(
            request,
            initial_cursor=initial_cursor,
            replay_through=replay_through,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
