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

from ledger.upcasting.upcasters import build_default_upcaster_registry


class UpcasterRegistry:
    """Compatibility wrapper exposing default chain via `upcast(event)`."""

    def __init__(self):
        self._registry = build_default_upcaster_registry()

    def upcast(self, event: dict) -> dict:
        return self._registry.upcast(event)


__all__ = ["UpcasterRegistry", "build_default_upcaster_registry"]
