"""Deliverable-path shim to the project EventStore implementation."""

from ledger.event_store import EventStore, InMemoryEventStore, OptimisticConcurrencyError

__all__ = ["EventStore", "InMemoryEventStore", "OptimisticConcurrencyError"]

