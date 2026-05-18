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
    """Per-job durable event log plus a wakeup signal for SSE generators.

    Events are appended (and assigned a monotonic id) at emit time by the
    background thread, marshalled onto the event loop. `events` is the single
    source of truth: SSE generators read via get_events_after(cursor), so
    reconnects replay correctly regardless of connection lifecycle.
    """
    events: list[dict] = field(default_factory=list)
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    _next_id: int = 1

    def add(self, event_name: str, data: dict[str, Any]) -> dict:
        """Append an event (monotonic id) and wake any waiting generator."""
        event = {"id": self._next_id, "type": event_name, "data": data}
        self.events.append(event)
        self._next_id += 1
        self.wakeup.set()
        return event

    def get_events_after(self, last_id: int) -> list[dict]:
        """Return all events with id > last_id (replay and live tailing)."""
        return [e for e in self.events if e["id"] > last_id]


async def sse_stream(buf: EventBuffer, last_id: int = 0):
    """Yield SSE-formatted strings for events after last_id, then live-tail
    until a terminal (done/error) event. Emits a keepalive comment every 30s
    of idleness. Reconnect-safe: reads the durable buffer, never consumes it.
    """
    cursor = last_id
    while True:
        pending = buf.get_events_after(cursor)
        if pending:
            for event in pending:
                yield format_sse(event["type"], event["data"], event["id"])
                cursor = event["id"]
                if event["type"] in ("done", "error"):
                    return
            continue
        buf.wakeup.clear()
        # Re-check after clear to avoid a lost-wakeup race.
        if buf.get_events_after(cursor):
            continue
        try:
            await asyncio.wait_for(buf.wakeup.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
