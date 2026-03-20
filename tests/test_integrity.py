from __future__ import annotations

import pytest

from ledger.event_store import InMemoryEventStore
from src.integrity.audit_chain import run_integrity_check


@pytest.mark.asyncio
async def test_run_integrity_check_appends_audit_event_and_hashes_incrementally():
    store = InMemoryEventStore()
    await store.append(
        "loan-APEX-55",
        [
            {"event_type": "ApplicationSubmitted", "payload": {"application_id": "APEX-55", "applicant_id": "COMP-55", "requested_amount_usd": 9000, "loan_purpose": "working_capital"}},
            {"event_type": "DecisionRequested", "payload": {"application_id": "APEX-55"}},
        ],
        expected_version=-1,
    )

    first = await run_integrity_check(store, entity_type="loan", entity_id="APEX-55")
    assert first.chain_valid is True
    assert first.events_verified == 2
    assert first.previous_hash is None

    await store.append(
        "loan-APEX-55",
        [
            {"event_type": "ApplicationDeclined", "payload": {"application_id": "APEX-55", "decline_reasons": ["policy"], "declined_by": "auto", "adverse_action_notice_required": True}},
        ],
        expected_version=1,
    )
    second = await run_integrity_check(store, entity_type="loan", entity_id="APEX-55")
    assert second.chain_valid is True
    assert second.events_verified == 1
    assert second.previous_hash == first.integrity_hash

    audit_events = await store.load_stream("audit-loan-APEX-55")
    assert len(audit_events) == 2
    assert audit_events[-1]["payload"]["previous_hash"] == first.integrity_hash


@pytest.mark.asyncio
async def test_run_integrity_check_detects_tampered_verified_prefix():
    store = InMemoryEventStore()
    await store.append(
        "loan-APEX-56",
        [
            {"event_type": "ApplicationSubmitted", "payload": {"application_id": "APEX-56", "applicant_id": "COMP-56", "requested_amount_usd": 9000, "loan_purpose": "working_capital"}},
            {"event_type": "DecisionRequested", "payload": {"application_id": "APEX-56"}},
        ],
        expected_version=-1,
    )

    first = await run_integrity_check(store, entity_type="loan", entity_id="APEX-56")
    assert first.chain_valid is True

    store._streams["loan-APEX-56"][0]["payload"]["requested_amount_usd"] = 12345

    second = await run_integrity_check(store, entity_type="loan", entity_id="APEX-56")
    assert second.chain_valid is False
    assert second.tamper_detected is True
