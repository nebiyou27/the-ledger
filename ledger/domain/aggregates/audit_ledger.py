from __future__ import annotations

from dataclasses import dataclass, field

from src.models.events import DomainError


@dataclass
class AuditLedgerAggregate:
    entity_id: str
    version: int = -1
    checks_run: int = 0
    last_hash: str | None = None
    chain_valid: bool = True
    tamper_detected: bool = False
    events: list[dict] = field(default_factory=list)

    @classmethod
    async def load(cls, store, entity_id: str) -> "AuditLedgerAggregate":
        agg = cls(entity_id=entity_id)
        stream_events = await store.load_stream(f"audit-{entity_id}")
        for event in stream_events:
            agg.apply(event)
        return agg

    def apply(self, event: dict) -> None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        self.events.append(event)

        stream_position = event.get("stream_position")
        if isinstance(stream_position, int):
            self.version = stream_position
        else:
            self.version += 1

        if event_type != "AuditIntegrityCheckRun":
            return

        previous_hash = payload.get("previous_hash")
        integrity_hash = payload.get("integrity_hash")
        chain_valid = bool(payload.get("chain_valid"))
        tamper_detected = bool(payload.get("tamper_detected"))

        if self.last_hash is None and previous_hash not in (None, ""):
            raise DomainError("First audit integrity event must have null previous_hash")
        if self.last_hash is not None and previous_hash != self.last_hash:
            self.chain_valid = False
            self.tamper_detected = True
            raise DomainError("Audit chain broken: previous_hash does not match")

        if tamper_detected or not chain_valid:
            self.chain_valid = False
            self.tamper_detected = True

        self.last_hash = integrity_hash
        self.checks_run += 1
