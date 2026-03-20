from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.projections import (
    AgentPerformanceProjection,
    ApplicationSummaryProjection,
    ComplianceAuditProjection,
    ManualReviewsProjection,
    ProjectionDaemon,
    WhatIfProjector,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return getattr(value, "value")
    return value


async def _sync_projections(store) -> tuple[
    ApplicationSummaryProjection,
    AgentPerformanceProjection,
    ComplianceAuditProjection,
    ManualReviewsProjection,
]:
    app = ApplicationSummaryProjection()
    perf = AgentPerformanceProjection()
    comp = ComplianceAuditProjection()
    reviews = ManualReviewsProjection()
    daemon = ProjectionDaemon(store, [app, perf, comp, reviews])
    while True:
        processed = await daemon._process_batch()
        if not processed:
            break
    return app, perf, comp, reviews


async def generate_regulatory_package(
    store,
    application_id: str,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    app_proj, perf_proj, comp_proj, review_proj = await _sync_projections(store)
    loan_stream = await store.load_stream(f"loan-{application_id}")
    credit_stream = await store.load_stream(f"credit-{application_id}")
    fraud_stream = await store.load_stream(f"fraud-{application_id}")
    compliance_stream = await store.load_stream(f"compliance-{application_id}")

    summary = app_proj.get_application(application_id) or {}
    compliance = comp_proj.get_current_compliance(application_id) or {}
    what_if = WhatIfProjector().project(
        application_id=application_id,
        loan_events=loan_stream,
        application_summary=summary,
        compliance_audit=compliance,
    )

    decision = _first_event(loan_stream, "DecisionGenerated")
    review = _first_event(loan_stream, "HumanReviewCompleted")
    approval = _first_event(loan_stream, "ApplicationApproved")
    submission = _first_event(loan_stream, "ApplicationSubmitted")

    package = {
        "package_type": "regulatory_package",
        "application_id": application_id,
        "generated_at": _utcnow(),
        "application_summary": summary,
        "compliance_audit": compliance,
        "agent_performance": perf_proj.all_rows(),
        "manual_reviews": review_proj.all_rows(),
        "timeline": [
            {
                "stream_id": event.get("stream_id"),
                "stream_position": event.get("stream_position"),
                "event_type": event.get("event_type"),
                "recorded_at": event.get("recorded_at"),
                "payload": _json_safe(event.get("payload") or {}),
            }
            for event in loan_stream
        ],
        "underwriting": {
            "requested_amount_usd": _json_safe((submission or {}).get("payload", {}).get("requested_amount_usd")),
            "approved_amount_usd": _json_safe((approval or {}).get("payload", {}).get("approved_amount_usd")),
            "decision_recommendation": _json_safe((decision or {}).get("payload", {}).get("recommendation")),
            "override_used": bool((review or {}).get("payload", {}).get("override")),
            "reviewer_id": (review or {}).get("payload", {}).get("reviewer_id"),
            "approval_terms": {
                "interest_rate_pct": _json_safe((approval or {}).get("payload", {}).get("interest_rate_pct")),
                "term_months": _json_safe((approval or {}).get("payload", {}).get("term_months")),
                "conditions": _json_safe((approval or {}).get("payload", {}).get("conditions") or []),
            },
            "risk_snapshot": {
                "risk_tier": summary.get("risk_tier"),
                "fraud_score": summary.get("fraud_score"),
                "compliance_verdict": compliance.get("overall_verdict"),
                "state": summary.get("state"),
            },
        },
        "what_if": what_if,
        "evidence": {
            "loan_events": len(loan_stream),
            "credit_events": len(credit_stream),
            "fraud_events": len(fraud_stream),
            "compliance_events": len(compliance_stream),
        },
    }

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_json_safe(package), indent=2), encoding="utf-8")

    return package


def _first_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    for event in events:
        if event.get("event_type") == event_type:
            return event
    return None
