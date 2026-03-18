"""Command handlers using load -> validate -> determine -> append."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ledger.domain.aggregates.loan_application import (
    ApplicationState,
    LoanApplicationAggregate,
)
from ledger.schema.events import (
    ApplicationSubmitted,
    CreditAnalysisCompleted,
    CreditDecision,
    CreditRecordOpened,
    DocumentUploadRequested,
    LoanPurpose,
    RiskTier,
)


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return datetime.utcnow()


async def handle_submit_application(store, command: dict[str, Any]) -> list[int]:
    """
    Submit a new loan application and request required document uploads.

    Pattern:
      1) load aggregate
      2) validate command + invariant
      3) determine events
      4) append atomically with expected_version
    """
    application_id = str(command["application_id"])

    # 1) load
    agg = await LoanApplicationAggregate.load(store, application_id)

    # 2) validate
    if agg.state != ApplicationState.NEW:
        raise ValueError(f"Application '{application_id}' already exists in state {agg.state.value}")

    requested_amount = _as_decimal(command["requested_amount_usd"])
    if requested_amount <= 0:
        raise ValueError("requested_amount_usd must be > 0")

    # 3) determine
    submitted_event = ApplicationSubmitted(
        application_id=application_id,
        applicant_id=str(command["applicant_id"]),
        requested_amount_usd=requested_amount,
        loan_purpose=LoanPurpose(str(command.get("loan_purpose", LoanPurpose.WORKING_CAPITAL.value))),
        loan_term_months=int(command.get("loan_term_months", 36)),
        submission_channel=str(command.get("submission_channel", "web")),
        contact_email=str(command.get("contact_email", "unknown@example.com")),
        contact_name=str(command.get("contact_name", "Unknown")),
        submitted_at=_as_datetime(command.get("submitted_at")),
        application_reference=str(command.get("application_reference", application_id)),
    ).to_store_dict()

    upload_requested_event = DocumentUploadRequested(
        application_id=application_id,
        required_document_types=command.get(
            "required_document_types",
            ["application_proposal", "income_statement", "balance_sheet"],
        ),
        deadline=_as_datetime(command.get("deadline")) + timedelta(days=7),
        requested_by=str(command.get("requested_by", "system")),
    ).to_store_dict()

    # 4) append
    stream_id = f"loan-{application_id}"
    return await store.append(
        stream_id=stream_id,
        events=[submitted_event, upload_requested_event],
        expected_version=agg.version,
        correlation_id=str(command.get("correlation_id", "")) or None,
        causation_id=str(command.get("causation_id", "")) or None,
    )


async def handle_credit_analysis_completed(store, command: dict[str, Any]) -> list[int]:
    """
    Record completed credit analysis on the credit stream.

    Pattern:
      1) load aggregate (loan) to confirm lifecycle precondition
      2) validate command + invariant
      3) determine event
      4) append with expected_version
    """
    application_id = str(command["application_id"])

    # 1) load
    loan = await LoanApplicationAggregate.load(store, application_id)

    # 2) validate
    if loan.state not in (
        ApplicationState.DOCUMENTS_PROCESSED,
        ApplicationState.CREDIT_ANALYSIS_REQUESTED,
        ApplicationState.CREDIT_ANALYSIS_COMPLETE,
    ):
        raise ValueError(
            "Credit analysis completion is not allowed when loan state is "
            f"{loan.state.value}"
        )

    decision = CreditDecision(
        risk_tier=RiskTier(str(command["risk_tier"])),
        recommended_limit_usd=_as_decimal(command["recommended_limit_usd"]),
        confidence=float(command["confidence"]),
        rationale=str(command.get("rationale", "")),
        key_concerns=list(command.get("key_concerns", [])),
        data_quality_caveats=list(command.get("data_quality_caveats", [])),
        policy_overrides_applied=list(command.get("policy_overrides_applied", [])),
    )

    # 3) determine
    event = CreditAnalysisCompleted(
        application_id=application_id,
        session_id=str(command["session_id"]),
        decision=decision,
        model_version=str(command.get("model_version", "unknown-model")),
        model_deployment_id=str(command.get("model_deployment_id", "unknown-deployment")),
        input_data_hash=str(command.get("input_data_hash", "unknown-hash")),
        analysis_duration_ms=int(command.get("analysis_duration_ms", 0)),
        regulatory_basis=list(command.get("regulatory_basis", [])),
        completed_at=_as_datetime(command.get("completed_at")),
    ).to_store_dict()

    stream_id = f"credit-{application_id}"
    current_version = await store.stream_version(stream_id)

    # First credit event for some runs can be CreditRecordOpened.
    if current_version == -1:
        open_event = CreditRecordOpened(
            application_id=application_id,
            applicant_id=loan.applicant_id or "unknown",
            opened_at=_as_datetime(command.get("completed_at")),
        ).to_store_dict()
        return await store.append(stream_id, [open_event, event], expected_version=-1)

    # 4) append
    return await store.append(stream_id, [event], expected_version=current_version)

