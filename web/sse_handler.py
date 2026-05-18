from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


def format_sse(event_name: str, data: dict, event_id: int) -> str:
    """Serialize a single SSE event to wire format.

    Retained for unit tests and any non-EventSourceResponse caller. The live
    response path does NOT use this — sse_stream yields dicts so sse_starlette
    formats the frame (yielding pre-formatted strings would be double-wrapped).
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_name}\ndata: {payload}\n\n"


def to_sse_dict(event: dict) -> dict:
    """Convert an internal event {id,type,data} to an sse_starlette dict.

    sse_starlette builds ServerSentEvent(**dict); keys event/id/data map to
    the SSE event:/id:/data: fields. data is JSON-encoded (ensure_ascii=False
    keeps CJK readable).
    """
    return {
        "event": event["type"],
        "id": str(event["id"]),
        "data": json.dumps(event["data"], ensure_ascii=False),
    }


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
    """Yield sse_starlette event dicts for events after last_id, then live-tail
    until a terminal (done/error) event. Reconnect-safe: reads the durable
    buffer, never consumes it. Keepalive is handled by EventSourceResponse's
    built-in ping, so this generator emits no keepalive frames itself.
    """
    cursor = last_id
    while True:
        pending = buf.get_events_after(cursor)
        if pending:
            for event in pending:
                yield to_sse_dict(event)
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
            # Idle: loop back and re-check. EventSourceResponse sends pings.
            continue
