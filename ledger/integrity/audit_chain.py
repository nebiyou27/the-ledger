from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ledger.schema.events import AuditIntegrityCheckRun


def _canonical_event_hash(event: dict[str, Any]) -> str:
    material = {
        "event_id": str(event.get("event_id")),
        "stream_id": event.get("stream_id"),
        "stream_position": event.get("stream_position"),
        "event_type": event.get("event_type"),
        "event_version": event.get("event_version"),
        "payload": event.get("payload") or {},
        "metadata": event.get("metadata") or {},
        "recorded_at": str(event.get("recorded_at")),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_event_sequence(events: list[dict[str, Any]], previous_hash: str | None = None) -> str:
    event_hashes = "".join(_canonical_event_hash(event) for event in events)
    base = (previous_hash or "") + event_hashes
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


@dataclass
class IntegrityCheckResult:
    entity_type: str
    entity_id: str
    events_verified: int
    chain_valid: bool
    tamper_detected: bool
    integrity_hash: str
    previous_hash: str | None
    audit_stream_version: int


async def run_integrity_check(store, entity_type: str, entity_id: str) -> IntegrityCheckResult:
    primary_stream = f"{entity_type}-{entity_id}"
    audit_stream = f"audit-{entity_type}-{entity_id}"

    domain_events = await store.load_stream(primary_stream)
    audit_events = await store.load_stream(audit_stream)

    previous_hash = None
    previously_verified = 0
    if audit_events:
        last = audit_events[-1]
        payload = last.get("payload") or {}
        previous_hash = payload.get("integrity_hash")
        previously_verified = int(payload.get("events_verified_count", 0))

    verified_prefix = domain_events[:previously_verified]
    prefix_hash = _hash_event_sequence(verified_prefix) if verified_prefix else None
    tamper_detected = previous_hash is not None and prefix_hash != previous_hash
    chain_valid = not tamper_detected

    events_to_verify = domain_events[previously_verified:]
    base_hash = prefix_hash if tamper_detected and prefix_hash is not None else previous_hash
    integrity_hash = _hash_event_sequence(events_to_verify, previous_hash=base_hash)

    audit_event = AuditIntegrityCheckRun(
        entity_type=entity_type,
        entity_id=entity_id,
        check_timestamp=datetime.now(timezone.utc),
        events_verified_count=len(domain_events),
        integrity_hash=integrity_hash,
        previous_hash=previous_hash,
        chain_valid=chain_valid,
        tamper_detected=tamper_detected,
    ).to_store_dict()

    audit_version = await store.stream_version(audit_stream)
    positions = await store.append(audit_stream, [audit_event], expected_version=audit_version)

    return IntegrityCheckResult(
        entity_type=entity_type,
        entity_id=entity_id,
        events_verified=len(events_to_verify),
        chain_valid=chain_valid,
        tamper_detected=tamper_detected,
        integrity_hash=integrity_hash,
        previous_hash=previous_hash,
        audit_stream_version=positions[-1] if positions else audit_version,
    )
