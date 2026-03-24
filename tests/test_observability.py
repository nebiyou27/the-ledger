from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ledger.event_store import InMemoryEventStore
from ledger.metrics import build_event_throughput_snapshot
from ledger.observability import StoreMetrics
from ledger.projections.base import Projection
from ledger.projections.daemon import ProjectionDaemon


def _ev(event_type: str, **payload):
    return {"event_type": event_type, "event_version": 1, "payload": payload}


@pytest.mark.asyncio
async def test_inmemory_store_metrics_snapshot_tracks_core_calls():
    metrics = StoreMetrics()
    store = InMemoryEventStore(metrics=metrics)

    await store.append("loan-OBS-1", [_ev("SmokeStarted")], expected_version=-1)
    await store.load_stream("loan-OBS-1")
    await store.save_checkpoint("proj-a", 7)
    await store.load_checkpoint("proj-a")
    await store.archive_stream("loan-OBS-1")
    await store.get_stream_metadata("loan-OBS-1")

    snapshot = store.get_metrics_snapshot()
    assert snapshot["append_calls"] == 1
    assert snapshot["append_events"] == 1
    assert snapshot["load_stream_calls"] == 1
    assert snapshot["save_checkpoint_calls"] == 1
    assert snapshot["load_checkpoint_calls"] == 1
    assert snapshot["archive_stream_calls"] == 1
    assert snapshot["get_stream_metadata_calls"] == 1


class _FlakyProjection(Projection):
    def __init__(self):
        super().__init__("flaky")
        self._failed_once = False
        self.seen: list[str] = []

    async def process_event(self, event: dict) -> None:
        if not self._failed_once:
            self._failed_once = True
            raise RuntimeError("transient projection failure")
        self.seen.append(str(event["event_type"]))


class _AlwaysFailProjection(Projection):
    def __init__(self):
        super().__init__("always_fail")

    async def process_event(self, event: dict) -> None:
        raise RuntimeError("permanent projection failure")


@pytest.mark.asyncio
async def test_projection_daemon_retries_transient_projection_failures():
    store = InMemoryEventStore()
    await store.append("loan-OBS-2", [_ev("SmokeStarted")], expected_version=-1)

    projection = _FlakyProjection()
    daemon = ProjectionDaemon(store, [projection], max_retries=1)

    processed = await daemon._process_batch()

    assert processed == 1
    assert projection.seen == ["SmokeStarted"]
    assert daemon.get_error_counts()["flaky"] == 0
    metrics = daemon.get_metrics()
    assert metrics["batches_processed"] == 1
    assert metrics["retries"] >= 1


@pytest.mark.asyncio
async def test_projection_daemon_dead_letters_permanent_failures_and_advances_checkpoint():
    store = InMemoryEventStore()
    await store.append("loan-OBS-3", [_ev("SmokeStarted")], expected_version=-1)

    projection = _AlwaysFailProjection()
    daemon = ProjectionDaemon(store, [projection], max_retries=0)

    processed = await daemon._process_batch()

    assert processed == 1
    assert daemon.get_error_counts()["always_fail"] == 1
    assert daemon.get_dead_letter_counts()["always_fail"] == 1
    assert await store.load_checkpoint("always_fail") == 1

    dead_letters = await store.load_projection_dead_letters("always_fail")
    assert len(dead_letters) == 1
    record = dead_letters[0]
    assert record["projection_name"] == "always_fail"
    assert record["event_type"] == "SmokeStarted"
    assert record["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_event_throughput_snapshot_uses_recent_window_and_buckets():
    store = InMemoryEventStore()
    events = [
        {"event_type": "SmokeStarted", "event_version": 1, "payload": {}, "recorded_at": datetime(2026, 3, 24, 9, 30, tzinfo=timezone.utc)},
        {"event_type": "SmokeStarted", "event_version": 1, "payload": {}, "recorded_at": datetime(2026, 3, 24, 10, 24, tzinfo=timezone.utc)},
        {"event_type": "SmokeStarted", "event_version": 1, "payload": {}, "recorded_at": datetime(2026, 3, 24, 10, 29, tzinfo=timezone.utc)},
    ]
    await store.append("loan-OBS-4", events, expected_version=-1)

    snapshot = await build_event_throughput_snapshot(store, window_minutes=60, bucket_minutes=5)

    assert snapshot["totalEvents"] == 3
    assert snapshot["eventsPerMinute"] == 0.05
    assert snapshot["eventsPerHour"] == 3.0
    assert snapshot["peakBucketEvents"] == 2
    assert len(snapshot["buckets"]) == 12
