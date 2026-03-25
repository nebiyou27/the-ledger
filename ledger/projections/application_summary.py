from __future__ import annotations

from copy import deepcopy
from typing import Any

from ledger.projections.base import Projection


class ApplicationSummaryProjection(Projection):
    def __init__(self):
        super().__init__("application_summary")
        self._rows: dict[str, dict[str, Any]] = {}

    def handles(self, event_type: str) -> bool:
        return event_type in {
            "ApplicationSubmitted",
            "CreditAnalysisCompleted",
            "CREDIT_ANALYSIS_COMPLETED",
            "FraudScreeningCompleted",
            "ComplianceCheckCompleted",
            "DecisionGenerated",
            "HumanReviewCompleted",
            "ApplicationApproved",
            "ApplicationDeclined",
            "LOAN_APPROVED",
            "LOAN_REJECTED",
            "AgentSessionCompleted",
            "DecisionRequested",
            "HumanReviewRequested",
            "MANUAL_REVIEW_REQUIRED",
        }

    async def process_event(self, event: dict[str, Any]) -> None:
        etype = str(event.get("event_type"))
        payload = event.get("payload") or {}

        application_id = payload.get("application_id")
        if not application_id and isinstance(payload.get("events_written"), list):
            return
        if not application_id:
            return

        row = self._rows.setdefault(
            str(application_id),
            {
                "application_id": str(application_id),
                "state": "UNKNOWN",
                "applicant_id": None,
                "requested_amount_usd": None,
                "approved_amount_usd": None,
                "risk_tier": None,
                "fraud_score": None,
                "compliance_status": None,
                "decision": None,
                "agent_sessions_completed": [],
                "last_event_type": None,
                "last_event_at": None,
                "human_reviewer_id": None,
                "final_decision_at": None,
            },
        )

        if etype == "ApplicationSubmitted":
            row["state"] = "SUBMITTED"
            row["applicant_id"] = payload.get("applicant_id")
            row["requested_amount_usd"] = payload.get("requested_amount_usd")
        elif etype in {"CreditAnalysisCompleted", "CREDIT_ANALYSIS_COMPLETED"}:
            decision = payload.get("decision") or {}
            row["risk_tier"] = decision.get("risk_tier") or payload.get("risk_tier")
            row["state"] = "ANALYSIS_COMPLETE"
        elif etype == "FraudScreeningCompleted":
            row["fraud_score"] = payload.get("fraud_score")
            row["state"] = "FRAUD_SCREENED"
        elif etype == "ComplianceCheckCompleted":
            row["compliance_status"] = payload.get("overall_verdict")
            row["state"] = "COMPLIANCE_COMPLETE"
        elif etype == "DecisionRequested":
            row["state"] = "PENDING_DECISION"
        elif etype in {"DecisionGenerated", "LOAN_APPROVED", "LOAN_REJECTED", "MANUAL_REVIEW_REQUIRED"}:
            row["decision"] = payload.get("recommendation")
            if etype == "LOAN_APPROVED":
                row["state"] = "FINAL_APPROVED"
                row["approved_amount_usd"] = payload.get("approved_amount_usd")
                row["final_decision_at"] = event.get("recorded_at")
            elif etype == "LOAN_REJECTED":
                row["state"] = "FINAL_DECLINED"
                row["final_decision_at"] = event.get("recorded_at")
            else:
                row["state"] = "PENDING_HUMAN_REVIEW"
        elif etype == "HumanReviewRequested":
            row["state"] = "PENDING_HUMAN_REVIEW"
        elif etype == "HumanReviewCompleted":
            row["human_reviewer_id"] = payload.get("reviewer_id")
            row["state"] = "HUMAN_REVIEW_COMPLETE"
            row["final_decision_at"] = event.get("recorded_at")
        elif etype == "ApplicationApproved":
            row["approved_amount_usd"] = payload.get("approved_amount_usd")
            row["state"] = "FINAL_APPROVED"
            row["final_decision_at"] = event.get("recorded_at")
        elif etype == "ApplicationDeclined":
            row["state"] = "FINAL_DECLINED"
            row["final_decision_at"] = event.get("recorded_at")
        elif etype == "AgentSessionCompleted":
            session_id = payload.get("session_id")
            if session_id and session_id not in row["agent_sessions_completed"]:
                row["agent_sessions_completed"].append(session_id)

        row["last_event_type"] = etype
        row["last_event_at"] = event.get("recorded_at")

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        row = self._rows.get(application_id)
        return deepcopy(row) if row else None

    def all_rows(self) -> list[dict[str, Any]]:
        return [deepcopy(v) for v in self._rows.values()]
