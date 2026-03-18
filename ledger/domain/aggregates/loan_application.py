"""
Loan application aggregate replay and state transitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ApplicationState(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    DOCUMENTS_PENDING = "DOCUMENTS_PENDING"
    DOCUMENTS_UPLOADED = "DOCUMENTS_UPLOADED"
    DOCUMENTS_PROCESSED = "DOCUMENTS_PROCESSED"
    CREDIT_ANALYSIS_REQUESTED = "CREDIT_ANALYSIS_REQUESTED"
    CREDIT_ANALYSIS_COMPLETE = "CREDIT_ANALYSIS_COMPLETE"
    FRAUD_SCREENING_REQUESTED = "FRAUD_SCREENING_REQUESTED"
    FRAUD_SCREENING_COMPLETE = "FRAUD_SCREENING_COMPLETE"
    COMPLIANCE_CHECK_REQUESTED = "COMPLIANCE_CHECK_REQUESTED"
    COMPLIANCE_CHECK_COMPLETE = "COMPLIANCE_CHECK_COMPLETE"
    PENDING_DECISION = "PENDING_DECISION"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    DECLINED_COMPLIANCE = "DECLINED_COMPLIANCE"
    REFERRED = "REFERRED"


VALID_TRANSITIONS = {
    ApplicationState.NEW: [ApplicationState.SUBMITTED],
    ApplicationState.SUBMITTED: [ApplicationState.DOCUMENTS_PENDING],
    ApplicationState.DOCUMENTS_PENDING: [ApplicationState.DOCUMENTS_UPLOADED],
    ApplicationState.DOCUMENTS_UPLOADED: [ApplicationState.DOCUMENTS_PROCESSED],
    ApplicationState.DOCUMENTS_PROCESSED: [ApplicationState.CREDIT_ANALYSIS_REQUESTED],
    ApplicationState.CREDIT_ANALYSIS_REQUESTED: [ApplicationState.CREDIT_ANALYSIS_COMPLETE],
    ApplicationState.CREDIT_ANALYSIS_COMPLETE: [ApplicationState.FRAUD_SCREENING_REQUESTED],
    ApplicationState.FRAUD_SCREENING_REQUESTED: [ApplicationState.FRAUD_SCREENING_COMPLETE],
    ApplicationState.FRAUD_SCREENING_COMPLETE: [ApplicationState.COMPLIANCE_CHECK_REQUESTED],
    ApplicationState.COMPLIANCE_CHECK_REQUESTED: [ApplicationState.COMPLIANCE_CHECK_COMPLETE],
    ApplicationState.COMPLIANCE_CHECK_COMPLETE: [ApplicationState.PENDING_DECISION, ApplicationState.DECLINED_COMPLIANCE],
    ApplicationState.PENDING_DECISION: [ApplicationState.APPROVED, ApplicationState.DECLINED, ApplicationState.PENDING_HUMAN_REVIEW],
    ApplicationState.PENDING_HUMAN_REVIEW: [ApplicationState.APPROVED, ApplicationState.DECLINED],
}


@dataclass
class LoanApplicationAggregate:
    application_id: str
    state: ApplicationState = ApplicationState.NEW
    applicant_id: str | None = None
    requested_amount_usd: float | None = None
    loan_purpose: str | None = None
    version: int = -1
    events: list[dict] = field(default_factory=list)

    @classmethod
    async def load(cls, store, application_id: str) -> "LoanApplicationAggregate":
        """Load and replay loan stream events to rebuild aggregate state."""
        agg = cls(application_id=application_id)
        stream_events = await store.load_stream(f"loan-{application_id}")
        for event in stream_events:
            agg.apply(event)
        return agg

    def apply(self, event: dict) -> None:
        """Apply one loan-stream event to update aggregate state."""
        event_type = event.get("event_type")
        payload = event.get("payload", {})
        self.events.append(event)

        stream_position = event.get("stream_position")
        if isinstance(stream_position, int):
            self.version = stream_position
        else:
            self.version += 1

        if event_type == "ApplicationSubmitted":
            self.assert_valid_transition(ApplicationState.SUBMITTED)
            self.state = ApplicationState.SUBMITTED
            self.applicant_id = payload.get("applicant_id")
            self.requested_amount_usd = payload.get("requested_amount_usd")
            self.loan_purpose = payload.get("loan_purpose")
            return

        if event_type == "DocumentUploadRequested":
            self.assert_valid_transition(ApplicationState.DOCUMENTS_PENDING)
            self.state = ApplicationState.DOCUMENTS_PENDING
            return

        if event_type == "DocumentUploaded":
            # Multiple uploads are expected; keep this state stable.
            if self.state != ApplicationState.DOCUMENTS_UPLOADED:
                self.assert_valid_transition(ApplicationState.DOCUMENTS_UPLOADED)
            self.state = ApplicationState.DOCUMENTS_UPLOADED
            return

        if event_type == "DocumentUploadFailed":
            self.state = ApplicationState.DOCUMENTS_PENDING
            return

        if event_type == "CreditAnalysisRequested":
            self.state = ApplicationState.CREDIT_ANALYSIS_REQUESTED
            return

        if event_type == "CreditAnalysisCompleted":
            self.state = ApplicationState.CREDIT_ANALYSIS_COMPLETE
            return

        if event_type == "FraudScreeningRequested":
            self.state = ApplicationState.FRAUD_SCREENING_REQUESTED
            return

        if event_type == "FraudScreeningCompleted":
            self.state = ApplicationState.FRAUD_SCREENING_COMPLETE
            return

        if event_type == "ComplianceCheckRequested":
            self.state = ApplicationState.COMPLIANCE_CHECK_REQUESTED
            return

        if event_type == "ComplianceCheckCompleted":
            verdict = str(payload.get("overall_verdict", "")).upper()
            if verdict == "BLOCKED":
                self.state = ApplicationState.DECLINED_COMPLIANCE
            else:
                self.state = ApplicationState.COMPLIANCE_CHECK_COMPLETE
            return

        if event_type == "DecisionRequested":
            self.state = ApplicationState.PENDING_DECISION
            return

        if event_type == "DecisionGenerated":
            recommendation = str(payload.get("recommendation", "")).upper()
            if recommendation == "REFER":
                self.state = ApplicationState.REFERRED
            else:
                self.state = ApplicationState.PENDING_DECISION
            return

        if event_type == "HumanReviewRequested":
            self.state = ApplicationState.PENDING_HUMAN_REVIEW
            return

        if event_type == "HumanReviewCompleted":
            final_decision = str(payload.get("final_decision", "")).upper()
            if final_decision == "APPROVE":
                self.state = ApplicationState.APPROVED
            elif final_decision == "DECLINE":
                self.state = ApplicationState.DECLINED
            elif final_decision == "REFER":
                self.state = ApplicationState.REFERRED
            return

        if event_type == "ApplicationApproved":
            self.state = ApplicationState.APPROVED
            return

        if event_type == "ApplicationDeclined":
            codes = [str(code) for code in (payload.get("adverse_action_codes") or [])]
            if "COMPLIANCE_BLOCK" in codes:
                self.state = ApplicationState.DECLINED_COMPLIANCE
            else:
                self.state = ApplicationState.DECLINED

    def assert_valid_transition(self, target: ApplicationState) -> None:
        allowed = VALID_TRANSITIONS.get(self.state, [])
        if target not in allowed:
            raise ValueError(f"Invalid transition {self.state} -> {target}. Allowed: {allowed}")
