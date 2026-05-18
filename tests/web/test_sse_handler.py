import asyncio
import json
import pytest
from web.sse_handler import EventBuffer, format_sse, to_sse_dict, sse_stream


def test_format_sse_basic():
    result = format_sse("agent_status", {"agent": "市场分析师", "status": "in_progress"}, event_id=1)
    assert "event: agent_status\n" in result
    assert '"agent": "市场分析师"' in result
    assert "id: 1\n" in result
    assert result.endswith("\n\n")


def test_to_sse_dict_shape():
    d = to_sse_dict({"id": 7, "type": "agent_status", "data": {"agent": "市场分析师", "status": "completed"}})
    assert d["event"] == "agent_status"
    assert d["id"] == "7"
    parsed = json.loads(d["data"])
    assert parsed == {"agent": "市场分析师", "status": "completed"}
    assert "市场分析师" in d["data"]  # ensure_ascii=False keeps CJK


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


def test_add_sets_wakeup():
    buf = EventBuffer()
    assert not buf.wakeup.is_set()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    assert buf.wakeup.is_set()


@pytest.mark.asyncio
async def test_sse_stream_replays_after_last_id():
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("report_section", {"section": "market_report", "content": "x"})
    buf.add("done", {"job_id": "j"})
    out = [chunk async for chunk in sse_stream(buf, last_id=1)]
    # events 2 and 3 only, then return on done
    assert len(out) == 2
    assert out[0]["event"] == "report_section"
    assert out[0]["id"] == "2"
    assert out[1]["event"] == "done"


@pytest.mark.asyncio
async def test_sse_stream_terminates_on_error():
    buf = EventBuffer()
    buf.add("error", {"message": "boom"})
    out = [c async for c in sse_stream(buf, 0)]
    assert len(out) == 1
    assert out[0]["event"] == "error"
    assert json.loads(out[0]["data"]) == {"message": "boom"}


@pytest.mark.asyncio
async def test_sse_stream_no_event_loss_across_reconnect():
    # First connection drains events 1..2 then "disconnects" (generator closed)
    buf = EventBuffer()
    buf.add("agent_status", {"agent": "A", "status": "pending"})
    buf.add("agent_status", {"agent": "A", "status": "in_progress"})
    first = []
    gen = sse_stream(buf, 0)
    async for c in gen:
        first.append(c)
        if len(first) >= 2:
            break
    await gen.aclose()  # simulate client disconnect
    # Events emitted DURING the disconnect window must survive (C2 fix)
    buf.add("report_section", {"section": "news_report", "content": "y"})
    buf.add("done", {"job_id": "j"})
    # Reconnect with Last-Event-ID = 2
    second = [c async for c in sse_stream(buf, 2)]
    assert len(second) == 2          # exactly events 3 and 4, no duplication
    assert second[0]["id"] == "3"
    assert second[0]["event"] == "report_section"
    assert second[1]["event"] == "done"
