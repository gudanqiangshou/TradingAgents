import asyncio
import pytest
from web.sse_handler import EventBuffer, format_sse


def test_format_sse_basic():
    result = format_sse("agent_status", {"agent": "市场分析师", "status": "in_progress"}, event_id=1)
    assert "event: agent_status\n" in result
    assert '"agent": "市场分析师"' in result
    assert "id: 1\n" in result
    assert result.endswith("\n\n")


def test_event_buffer_stores_events():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("agent_status", {"agent": "A", "status": "in_progress"})
    assert len(buf.events) == 2


def test_event_buffer_ids_are_sequential():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("agent_status", {"agent": "A", "status": "in_progress"})
    assert buf.events[0]["id"] == 1
    assert buf.events[1]["id"] == 2


def test_event_buffer_replay_from_id():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("report_section", {"section": "market_report", "content": "hello"})
    buf.add("done", {"job_id": "x"})
    replayed = buf.get_events_after(1)
    assert len(replayed) == 2
    assert replayed[0]["id"] == 2


def test_event_buffer_replay_from_zero_returns_all():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("done", {"job_id": "x"})
    assert len(buf.get_events_after(0)) == 2


@pytest.mark.asyncio
async def test_queue_receives_event():
    buf = EventBuffer()
    loop = asyncio.get_event_loop()

    loop.call_soon_threadsafe(buf.queue.put_nowait, {"type": "agent_status", "data": {"agent": "A", "status": "pending"}})
    event = await asyncio.wait_for(buf.queue.get(), timeout=1.0)
    assert event["type"] == "agent_status"
