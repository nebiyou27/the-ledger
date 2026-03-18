"""
ledger/upcasters.py — UpcasterRegistry
=======================================
Upcasters transform old event versions to the current version ON READ.
They NEVER write to the events table. Immutability is non-negotiable.

IMPLEMENT:
  CreditAnalysisCompleted v1 → v2: add regulatory_basis=[] if absent
  DecisionGenerated v1 → v2: add model_versions={} if absent

RULE: if event_version == current version, return unchanged.
      if event_version < current version, apply the chain of upcasters.
"""
from __future__ import annotations

from ledger.event_store import UpcasterRegistry as _BaseRegistry


def build_default_upcaster_registry() -> _BaseRegistry:
    """Create the default upcaster chain used by the ledger."""
    registry = _BaseRegistry()

    @registry.upcaster("CreditAnalysisCompleted", from_version=1, to_version=2)
    def _credit_v1_to_v2(payload: dict) -> dict:
        next_payload = dict(payload or {})
        next_payload.setdefault("regulatory_basis", [])
        return next_payload

    @registry.upcaster("DecisionGenerated", from_version=1, to_version=2)
    def _decision_v1_to_v2(payload: dict) -> dict:
        next_payload = dict(payload or {})
        next_payload.setdefault("model_versions", {})
        return next_payload

    return registry


class UpcasterRegistry:
    """Compatibility wrapper exposing the same `upcast(event)` contract."""

    def __init__(self):
        self._registry = build_default_upcaster_registry()

    def upcast(self, event: dict) -> dict:
        return self._registry.upcast(event)
