from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ledger.domain.compliance_rules import REGULATIONS

from .registry import UpcasterRegistry


def _parse_recorded_at(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime(2025, 1, 1, tzinfo=timezone.utc)


def _historical_regulatory_basis(recorded_at: datetime) -> list[str]:
    # Coarse but deterministic mapping from record time to the rule set active then.
    # The project only exposes one concrete catalog, so we map earlier events to a
    # smaller legacy slice and later events to the full current catalog.
    early_cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    q1_cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
    if recorded_at < early_cutoff:
        return ["REG-001", "REG-002", "REG-003"]
    if recorded_at < q1_cutoff:
        return ["REG-001", "REG-002", "REG-003", "REG-004"]
    return list(REGULATIONS.keys())


def _infer_legacy_model_version(recorded_at: datetime) -> str:
    # Coarse inference for historical compatibility when older events did not capture model metadata.
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return "legacy-pre-2026" if recorded_at < cutoff else "legacy-2026"


def _normalize_agent_key(agent_type: str | None) -> str | None:
    if not agent_type:
        return None
    mapping = {
        "credit_analysis": "credit",
        "credit": "credit",
        "fraud_detection": "fraud",
        "fraud": "fraud",
        "compliance": "compliance",
        "decision_orchestrator": "orchestrator",
        "orchestrator": "orchestrator",
        "document_processing": "document_processing",
        "document": "document_processing",
    }
    return mapping.get(agent_type, agent_type)


def _context_history(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not context:
        return []
    history = context.get("history_events") or context.get("related_events") or []
    return [dict(event) for event in history if isinstance(event, dict)]


def _collect_model_versions_from_history(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, str]:
    versions: dict[str, str] = {}
    history = _context_history(context)
    if not history:
        return versions

    application_id = ""
    if payload.get("application_id"):
        application_id = str(payload.get("application_id"))
    elif context and context.get("application_id"):
        application_id = str(context.get("application_id"))
    target_sessions = {
        str(session)
        for session in (payload.get("contributing_sessions") or payload.get("contributing_agent_sessions") or [])
        if str(session)
    }
    target_sessions.add(str(payload.get("orchestrator_session_id") or ""))
    target_sessions.discard("")

    for event in history:
        event_type = str(event.get("event_type", ""))
        event_payload = event.get("payload") or {}
        event_app_id = str(event_payload.get("application_id") or "")
        if application_id and event_app_id and event_app_id != application_id:
            continue

        session_id = str(event_payload.get("session_id") or "")
        agent_type = _normalize_agent_key(str(event_payload.get("agent_type") or "") or None)
        if event_type == "AgentSessionStarted":
            model_version = str(event_payload.get("model_version") or "")
            if not model_version:
                continue
            if agent_type:
                versions.setdefault(agent_type, model_version)
            if session_id:
                versions.setdefault(session_id, model_version)
            continue

        if event_type == "CreditAnalysisCompleted":
            model_version = str(event_payload.get("model_version") or "")
            if model_version:
                versions.setdefault("credit", model_version)
            if session_id and model_version:
                versions.setdefault(session_id, model_version)
            continue

        if event_type == "FraudScreeningCompleted":
            model_version = str(event_payload.get("screening_model_version") or "")
            if model_version:
                versions.setdefault("fraud", model_version)
            if session_id and model_version:
                versions.setdefault(session_id, model_version)
            continue

        if event_type == "ComplianceCheckCompleted":
            if session_id:
                versions.setdefault("compliance", str(event_payload.get("model_version") or "unknown-historical"))
            continue

        if event_type == "DecisionGenerated":
            model_version = str(event_payload.get("model_version") or "")
            if model_version:
                versions.setdefault("orchestrator", model_version)
            if session_id and model_version:
                versions.setdefault(session_id, model_version)

    for session_id in sorted(target_sessions):
        if session_id in versions:
            continue
        for event in history:
            event_payload = event.get("payload") or {}
            if str(event_payload.get("session_id") or "") != session_id:
                continue
            event_type = str(event.get("event_type", ""))
            if event_type == "AgentSessionStarted":
                model_version = str(event_payload.get("model_version") or "")
            elif event_type == "CreditAnalysisCompleted":
                model_version = str(event_payload.get("model_version") or "")
            elif event_type == "FraudScreeningCompleted":
                model_version = str(event_payload.get("screening_model_version") or "")
            else:
                model_version = ""
            if model_version:
                versions[session_id] = model_version
            break

    return versions


def build_default_upcaster_registry() -> UpcasterRegistry:
    registry = UpcasterRegistry()

    @registry.register("CreditAnalysisCompleted", from_version=1)
    def _credit_v1_to_v2(
        payload: dict[str, Any],
        event: dict[str, Any],
        _registry: UpcasterRegistry,
        _context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        next_payload = dict(payload or {})
        recorded_at = _parse_recorded_at(event.get("recorded_at"))
        next_payload.setdefault("model_version", _infer_legacy_model_version(recorded_at))
        next_payload.setdefault("confidence_score", None)
        next_payload.setdefault("regulatory_basis", _historical_regulatory_basis(recorded_at))
        return next_payload

    @registry.register("DecisionGenerated", from_version=1)
    def _decision_v1_to_v2(
        payload: dict[str, Any],
        _event: dict[str, Any],
        _registry: UpcasterRegistry,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        next_payload = dict(payload or {})
        if "model_versions" not in next_payload or not next_payload["model_versions"]:
            next_payload["model_versions"] = _collect_model_versions_from_history(next_payload, context)
        return next_payload

    return registry
