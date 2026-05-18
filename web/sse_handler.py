from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


def format_sse(event_name: str, data: dict, event_id: int) -> str:
    """Serialize a single SSE event to wire format."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n"


@dataclass
class EventBuffer:
    """Per-job event buffer: stores all events for replay and exposes an asyncio.Queue."""
    events: list[dict] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _next_id: int = 1

    def add(self, event_name: str, data: dict[str, Any]) -> dict:
        """Append an event to the buffer (called from SSE handler or background thread via queue)."""
        event = {"id": self._next_id, "type": event_name, "data": data}
        self.events.append(event)
        self._next_id += 1
        return event

    def get_events_after(self, last_id: int) -> list[dict]:
        """Return all events with id > last_id (for reconnect replay)."""
        return [e for e in self.events if e["id"] > last_id]
