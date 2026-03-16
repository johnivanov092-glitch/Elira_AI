from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional


EventHandler = Callable[[dict], Any]


class EventBusService:
    def __init__(self, max_events: int = 1000) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._events: Deque[dict] = deque(maxlen=max_events)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self._subscribers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_name: str, payload: Optional[dict] = None) -> dict:
        event = {
            "id": str(uuid.uuid4()),
            "name": event_name,
            "timestamp": time.time(),
            "payload": payload or {},
        }
        self._events.append(event)

        for handler in list(self._subscribers.get(event_name, [])):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                # Event bus must not crash the app because of a bad subscriber.
                continue
        return event

    def list_events(self, limit: int = 100) -> List[dict]:
        if limit <= 0:
            return []
        return list(self._events)[-limit:]

    def clear(self) -> None:
        self._events.clear()
