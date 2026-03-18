from __future__ import annotations

import os
from uuid import uuid4

import pytest

from ledger.event_store import EventStore


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

        replay = [event async for event in store.load_all(from_position=0)]
        assert any(event["stream_id"] == stream_id for event in replay)
    finally:
        await store.close()


