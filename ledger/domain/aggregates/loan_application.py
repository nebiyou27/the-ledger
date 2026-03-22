"""
Loan application aggregate replay and state transitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from src.models.events import DomainError


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
    compliance_verdict: str | None = None
    decision_recommendation: str | None = None
    decision_confidence: float | None = None
    events: list[dict] = field(default_factory=list)

    def assert_can_record_credit_analysis_completed(
        self,
        existing_credit_events: list[dict] | None = None,
        superseded_by_human_review: bool = False,
    ) -> None:
        if self.state in (
            ApplicationState.NEW,
            ApplicationState.APPROVED,
            ApplicationState.DECLINED,
            ApplicationState.DECLINED_COMPLIANCE,
            ApplicationState.REFERRED,
        ):
            raise DomainError("CreditAnalysisCompleted requires an active, non-terminal application")

        already_completed = any(
            event.get("event_type") == "CreditAnalysisCompleted"
            for event in (existing_credit_events or [])
        )
        if already_completed and not superseded_by_human_review:
            raise DomainError("CreditAnalysisCompleted already exists for this application")

    def assert_can_generate_decision(self, recommendation: str, confidence: float) -> str:
        normalized_recommendation = str(recommendation).upper()
        if confidence < 0.60:
            normalized_recommendation = "REFER"

        if self.compliance_verdict == "BLOCKED" and normalized_recommendation != "DECLINE":
            raise DomainError("Compliance BLOCKED only allows DECLINE recommendation")

        return normalized_recommendation

    def assert_can_approve(self, required_rules: set[str], passed_rules: set[str], verdict: str | None) -> None:
        normalized_verdict = (verdict or "").upper()
        if normalized_verdict in ("", "BLOCKED"):
            raise DomainError("ApplicationApproved requires completed non-blocked compliance")
        missing = sorted(set(required_rules) - set(passed_rules))
        if missing:
            raise DomainError(
                f"ApplicationApproved requires all required compliance rules passed: {missing}"
            )

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

        handler = self._event_handlers().get(event_type)
        if handler is not None:
            handler(payload)

    def _event_handlers(self) -> dict[str, Callable[[dict], None]]:
        return {
            "ApplicationSubmitted": self._apply_application_submitted,
            "DocumentUploadRequested": self._apply_document_upload_requested,
            "DocumentUploaded": self._apply_document_uploaded,
            "DocumentUploadFailed": self._apply_document_upload_failed,
            "CreditAnalysisRequested": self._apply_credit_analysis_requested,
            "CreditAnalysisCompleted": self._apply_credit_analysis_completed,
            "FraudScreeningRequested": self._apply_fraud_screening_requested,
            "FraudScreeningCompleted": self._apply_fraud_screening_completed,
            "ComplianceCheckRequested": self._apply_compliance_check_requested,
            "ComplianceCheckCompleted": self._apply_compliance_check_completed,
            "DecisionRequested": self._apply_decision_requested,
            "DecisionGenerated": self._apply_decision_generated,
            "HumanReviewRequested": self._apply_human_review_requested,
            "HumanReviewCompleted": self._apply_human_review_completed,
            "ApplicationApproved": self._apply_application_approved,
            "ApplicationDeclined": self._apply_application_declined,
        }

    def _apply_application_submitted(self, payload: dict) -> None:
        self.assert_valid_transition(ApplicationState.SUBMITTED)
        self.state = ApplicationState.SUBMITTED
        self.applicant_id = payload.get("applicant_id")
        self.requested_amount_usd = payload.get("requested_amount_usd")
        self.loan_purpose = payload.get("loan_purpose")

    def _apply_document_upload_requested(self, payload: dict) -> None:
        self.assert_valid_transition(ApplicationState.DOCUMENTS_PENDING)
        self.state = ApplicationState.DOCUMENTS_PENDING

    def _apply_document_uploaded(self, payload: dict) -> None:
        # Multiple uploads are expected; keep this state stable.
        if self.state != ApplicationState.DOCUMENTS_UPLOADED:
            self.assert_valid_transition(ApplicationState.DOCUMENTS_UPLOADED)
        self.state = ApplicationState.DOCUMENTS_UPLOADED

    def _apply_document_upload_failed(self, payload: dict) -> None:
        self.state = ApplicationState.DOCUMENTS_PENDING

    def _apply_credit_analysis_requested(self, payload: dict) -> None:
        self.state = ApplicationState.CREDIT_ANALYSIS_REQUESTED

    def _apply_credit_analysis_completed(self, payload: dict) -> None:
        self.state = ApplicationState.CREDIT_ANALYSIS_COMPLETE

    def _apply_fraud_screening_requested(self, payload: dict) -> None:
        self.state = ApplicationState.FRAUD_SCREENING_REQUESTED

    def _apply_fraud_screening_completed(self, payload: dict) -> None:
        self.state = ApplicationState.FRAUD_SCREENING_COMPLETE

    def _apply_compliance_check_requested(self, payload: dict) -> None:
        self.state = ApplicationState.COMPLIANCE_CHECK_REQUESTED

    def _apply_compliance_check_completed(self, payload: dict) -> None:
        verdict = str(payload.get("overall_verdict", "")).upper()
        self.compliance_verdict = verdict
        if verdict == "BLOCKED":
            self.state = ApplicationState.DECLINED_COMPLIANCE
        else:
            self.state = ApplicationState.COMPLIANCE_CHECK_COMPLETE

    def _apply_decision_requested(self, payload: dict) -> None:
        self.state = ApplicationState.PENDING_DECISION

    def _apply_decision_generated(self, payload: dict) -> None:
        recommendation = str(payload.get("recommendation", "")).upper()
        confidence_raw = payload.get("confidence")
        confidence = float(confidence_raw) if confidence_raw is not None else None

        if confidence is not None and confidence < 0.60 and recommendation != "REFER":
            raise DomainError("DecisionGenerated with confidence < 0.60 must use REFER recommendation")

        if self.compliance_verdict == "BLOCKED" and recommendation != "DECLINE":
            raise DomainError("Compliance BLOCKED only allows DECLINE recommendation")

        self.decision_recommendation = recommendation
        self.decision_confidence = confidence
        if recommendation == "REFER":
            self.state = ApplicationState.REFERRED
        else:
            self.state = ApplicationState.PENDING_DECISION

    def _apply_human_review_requested(self, payload: dict) -> None:
        self.state = ApplicationState.PENDING_HUMAN_REVIEW

    def _apply_human_review_completed(self, payload: dict) -> None:
        final_decision = str(payload.get("final_decision", "")).upper()
        if final_decision == "APPROVE":
            self.state = ApplicationState.APPROVED
        elif final_decision == "DECLINE":
            self.state = ApplicationState.DECLINED
        elif final_decision == "REFER":
            self.state = ApplicationState.REFERRED

    def _apply_application_approved(self, payload: dict) -> None:
        if self.compliance_verdict in (None, "", "BLOCKED"):
            raise DomainError("ApplicationApproved requires completed non-blocked compliance")
        self.state = ApplicationState.APPROVED

    def _apply_application_declined(self, payload: dict) -> None:
        codes = [str(code) for code in (payload.get("adverse_action_codes") or [])]
        if "COMPLIANCE_BLOCK" in codes:
            self.state = ApplicationState.DECLINED_COMPLIANCE
        else:
            self.state = ApplicationState.DECLINED

    def assert_valid_transition(self, target: ApplicationState) -> None:
        allowed = VALID_TRANSITIONS.get(self.state, [])
        if target not in allowed:
            raise DomainError(f"Invalid transition {self.state} -> {target}. Allowed: {allowed}")
