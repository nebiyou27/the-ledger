from __future__ import annotations

import os
from uuid import uuid4

import pytest

from ledger.event_store import EventStore
from ledger.event_store import InMemoryEventStore


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DB_URL"),
    reason="Set TEST_DB_URL to run PostgreSQL smoke test",
)


@pytest.mark.asyncio
async def test_postgres_event_store_smoke():
    store = EventStore(os.environ["TEST_DB_URL"])
    await store.connect()
    try:
        await store.initialize_schema()

        stream_id = f"loan-smoke-{uuid4()}"
        await store.append(
            stream_id,
            [{"event_type": "SmokeStarted", "event_version": 1, "payload": {"ok": True}}],
            expected_version=-1,
            correlation_id="smoke-corr-1",
            causation_id="smoke-cause-1",
        )

        assert await store.stream_version(stream_id) == 0

        events = await store.load_stream(stream_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "SmokeStarted"
        assert events[0]["metadata"]["correlation_id"] == "smoke-corr-1"

        typed_events = await store.load_stream_records(stream_id)
        assert typed_events[0].stream_id == stream_id
        assert typed_events[0].event_type == "SmokeStarted"

        meta = await store.get_stream_metadata_record(stream_id)
        assert meta is not None
        assert meta.stream_id == stream_id
        assert meta.current_version == 0
        assert meta.archived_at is None

        await store.archive_stream(stream_id)
        archived_meta = await store.get_stream_metadata_record(stream_id)
        assert archived_meta is not None
        assert archived_meta.archived_at is not None

        replay = [event async for event in store.load_all(from_position=0)]
        assert any(event["stream_id"] == stream_id for event in replay)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_inmemory_event_store_replays_identical_append_idempotently():
    store = InMemoryEventStore()
    stream_id = "loan-idempotent-1"
    events = [{"event_type": "SmokeStarted", "event_version": 1, "payload": {"ok": True}}]

    first = await store.append(stream_id, events, expected_version=-1, causation_id="smoke-cause-1")
    second = await store.append(stream_id, events, expected_version=-1, causation_id="smoke-cause-1")

    assert first == [0]
    assert second == [0]
    loaded = await store.load_stream(stream_id)
    assert [event["event_type"] for event in loaded] == ["SmokeStarted"]


