from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .registry import UpcasterRegistry


def _parse_recorded_at(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _infer_legacy_model_version(recorded_at: datetime) -> str:
    # Coarse inference for historical compatibility when older events did not capture model metadata.
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return "legacy-pre-2026" if recorded_at < cutoff else "legacy-2026"


def _reconstruct_model_versions_from_sessions(payload: dict[str, Any]) -> dict[str, str]:
    # Read-only inference based on payload information available during upcast.
    # Full store lookups are intentionally omitted in the hot read path.
    versions: dict[str, str] = {}
    sessions = payload.get("contributing_sessions") or payload.get("contributing_agent_sessions") or []
    for session in sessions:
        versions[str(session)] = "unknown-historical"
    return versions


def build_default_upcaster_registry() -> UpcasterRegistry:
    registry = UpcasterRegistry()

    @registry.register("CreditAnalysisCompleted", from_version=1)
    def _credit_v1_to_v2(payload: dict[str, Any], event: dict[str, Any], _registry: UpcasterRegistry) -> dict[str, Any]:
        next_payload = dict(payload or {})
        recorded_at = _parse_recorded_at(event.get("recorded_at"))
        next_payload.setdefault("model_version", _infer_legacy_model_version(recorded_at))
        next_payload.setdefault("confidence_score", None)
        next_payload.setdefault("regulatory_basis", None)
        return next_payload

    @registry.register("DecisionGenerated", from_version=1)
    def _decision_v1_to_v2(payload: dict[str, Any], _event: dict[str, Any], _registry: UpcasterRegistry) -> dict[str, Any]:
        next_payload = dict(payload or {})
        next_payload.setdefault("model_versions", _reconstruct_model_versions_from_sessions(next_payload))
        return next_payload

    return registry
