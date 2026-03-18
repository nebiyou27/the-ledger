import asyncio

import pytest

from ledger.event_store import InMemoryEventStore, OptimisticConcurrencyError


def _ev(event_type: str, **payload):
    return {"event_type": event_type, "event_version": 1, "payload": payload}


@pytest.mark.asyncio
async def test_double_decision_concurrency_expected_version_3():
    """
    Seed the stream to version 3, then race two appends at expected_version=3.
    Exactly one append must win and one must fail with OCC.
    """
    store = InMemoryEventStore()
    stream_id = "loan-CONC-001"

    # Seed to version=3 (positions: 0,1,2,3)
    for i in range(4):
        current = await store.stream_version(stream_id)
        await store.append(stream_id, [_ev(f"Seed{i}")], expected_version=current)

    assert await store.stream_version(stream_id) == 3

    async def attempt() -> str:
        try:
            await store.append(
                stream_id,
                [_ev("DecisionGenerated", recommendation="APPROVE", confidence=0.82)],
                expected_version=3,
            )
            return "success"
        except OptimisticConcurrencyError:
            return "occ"

    outcomes = await asyncio.gather(attempt(), attempt())
    assert outcomes.count("success") == 1
    assert outcomes.count("occ") == 1

    events = await store.load_stream(stream_id)
    # This repo uses 0-based stream positions, so final length is 5 (version 4).
    assert len(events) == 5
    assert await store.stream_version(stream_id) == 4

