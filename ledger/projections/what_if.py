from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping


def _to_utc_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def _safe_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except Exception:
        return 0.0


class WhatIfProjector:
    """Builds counterfactual underwriting views from the current loan narrative."""

    def project(
        self,
        application_id: str,
        loan_events: list[dict[str, Any]],
        application_summary: dict[str, Any] | None = None,
        compliance_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = dict(application_summary or {})
        compliance = dict(compliance_audit or {})
        submitted = self._find_event(loan_events, "ApplicationSubmitted")
        decision = self._find_event(loan_events, "DecisionGenerated")
        review = self._find_event(loan_events, "HumanReviewCompleted")
        approved = self._find_event(loan_events, "ApplicationApproved")
        declined = self._find_event(loan_events, "ApplicationDeclined")

        requested_amount = _safe_number((submitted or {}).get("payload", {}).get("requested_amount_usd"))
        approved_amount = _safe_number((approved or {}).get("payload", {}).get("approved_amount_usd"))
        recommendation = str((decision or {}).get("payload", {}).get("recommendation") or "UNKNOWN").upper()
        final_decision = "APPROVED" if approved else "DECLINED" if declined else "PENDING"
        override_used = bool((review or {}).get("payload", {}).get("override"))
        reviewer_id = (review or {}).get("payload", {}).get("reviewer_id")
        conditions = list((approved or {}).get("payload", {}).get("conditions") or [])
        confidence = _safe_number((decision or {}).get("payload", {}).get("confidence"))

        underwriting = {
            "application_id": application_id,
            "requested_amount_usd": requested_amount,
            "approved_amount_usd": approved_amount,
            "approval_ratio": round(approved_amount / requested_amount, 4) if requested_amount else None,
            "decision_recommendation": recommendation,
            "confidence": confidence,
            "human_override_used": override_used,
            "reviewer_id": reviewer_id,
            "conditions": deepcopy(conditions),
            "conditions_count": len(conditions),
            "interest_rate_pct": _safe_number((approved or {}).get("payload", {}).get("interest_rate_pct")),
            "term_months": int((approved or {}).get("payload", {}).get("term_months") or 0),
            "effective_date": (approved or {}).get("payload", {}).get("effective_date"),
            "compliance_verdict": compliance.get("overall_verdict"),
            "risk_tier": (summary.get("risk_tier") or (decision or {}).get("payload", {}).get("risk_tier")),
            "fraud_score": summary.get("fraud_score"),
        }

        narrative = self._build_narrative(summary, underwriting, decision, review, approved)

        return {
            "actual": {
                "final_decision": final_decision,
                "approved_amount_usd": approved_amount,
                "override_used": override_used,
                "reviewer_id": reviewer_id,
                "loan_state": summary.get("state"),
                "last_event_type": summary.get("last_event_type"),
                "final_decision_at": _to_utc_text(summary.get("final_decision_at")),
            },
            "counterfactuals": [
                {
                    "name": "no_human_override",
                    "assumption": "If the reviewer had not overridden the decline, the loan would have been declined.",
                    "final_decision": "DECLINED",
                    "approved_amount_usd": 0.0,
                    "decision_recommendation": recommendation,
                    "capital_preserved_usd": approved_amount,
                },
                {
                    "name": "decline_followed_by_manual_hard_stop",
                    "assumption": "If compliance or reviewer intervention had not changed the recommendation, the application would remain closed.",
                    "final_decision": "DECLINED",
                    "approved_amount_usd": 0.0,
                    "decision_recommendation": recommendation,
                    "capital_preserved_usd": approved_amount,
                },
            ],
            "underwriting": underwriting,
            "narrative": narrative,
        }

    @staticmethod
    def _find_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
        for event in events:
            if event.get("event_type") == event_type:
                return event
        return None

    @staticmethod
    def _build_narrative(
        summary: dict[str, Any],
        underwriting: dict[str, Any],
        decision: dict[str, Any] | None,
        review: dict[str, Any] | None,
        approved: dict[str, Any] | None,
    ) -> str:
        company = summary.get("applicant_id") or "the applicant"
        recommendation = underwriting.get("decision_recommendation", "UNKNOWN")
        reviewer = underwriting.get("reviewer_id") or "the loan officer"
        approved_amount = underwriting.get("approved_amount_usd") or 0.0
        compliance = underwriting.get("compliance_verdict") or "UNKNOWN"
        confidence = underwriting.get("confidence") or 0.0
        reason = (review or {}).get("payload", {}).get("override_reason") if review else None
        approved_conditions = ", ".join(underwriting.get("conditions") or []) or "no special conditions"
        final_decision = "approved" if approved else "declined"

        parts = [
            f"{company} moved through credit, fraud, and compliance review with compliance verdict {compliance}.",
            f"The orchestrator's recommendation was {recommendation} at {confidence:.0%} confidence.",
            f"{reviewer} overrode that recommendation and the facility was {final_decision} for ${approved_amount:,.0f}.",
            f"Final terms included {approved_conditions}.",
        ]
        if reason:
            parts.append(f"Override rationale: {reason}.")
        return " ".join(parts)


def _resolve_projection_snapshot(
    projections: Any,
    application_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    application_summary: dict[str, Any] | None = None
    compliance_audit: dict[str, Any] | None = None

    if projections is None:
        return application_summary, compliance_audit

    candidates: list[Any] = []
    if isinstance(projections, Mapping):
        candidates.extend(
            [
                projections.get("application_summary"),
                projections.get("application_summary_projection"),
                projections.get("compliance_audit"),
                projections.get("compliance_audit_projection"),
            ]
        )
    elif isinstance(projections, (list, tuple, set)):
        candidates.extend(list(projections))
    else:
        candidates.append(projections)

    for candidate in candidates:
        if candidate is None:
            continue
        if application_summary is None and hasattr(candidate, "get_application"):
            application_summary = candidate.get_application(application_id)
        if compliance_audit is None and hasattr(candidate, "get_current_compliance"):
            compliance_audit = candidate.get_current_compliance(application_id)

    return application_summary, compliance_audit


async def run_what_if(
    store,
    application_id: str,
    branch_at_event_type: str | None,
    counterfactual_events: list[dict[str, Any]] | None,
    projections,
) -> dict[str, Any]:
    loan_events = await store.load_stream(f"loan-{application_id}")
    branch_index = None
    if branch_at_event_type:
        for index, event in enumerate(loan_events):
            if event.get("event_type") == branch_at_event_type:
                branch_index = index
                break

    if branch_index is not None:
        loan_events = loan_events[: branch_index + 1]

    for event in counterfactual_events or []:
        loan_events.append(dict(event))

    application_summary, compliance_audit = _resolve_projection_snapshot(projections, application_id)
    return WhatIfProjector().project(
        application_id=application_id,
        loan_events=loan_events,
        application_summary=application_summary,
        compliance_audit=compliance_audit,
    )
