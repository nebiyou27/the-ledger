from __future__ import annotations

import pytest

from ledger.event_store import InMemoryEventStore
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
async def test_projection_daemon_does_not_advance_checkpoint_on_failure():
    store = InMemoryEventStore()
    await store.append("loan-OBS-3", [_ev("SmokeStarted")], expected_version=-1)

    projection = _AlwaysFailProjection()
    daemon = ProjectionDaemon(store, [projection], max_retries=0)

    processed = await daemon._process_batch()

    assert processed == 0
    assert daemon.get_error_counts()["always_fail"] == 1
    assert await store.load_checkpoint("always_fail") == 0
