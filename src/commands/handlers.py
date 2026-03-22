"""Phase-2 command handlers using load -> validate -> determine -> append."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ledger.domain.aggregates.agent_session import (
    AgentSessionAggregate,
    AgentSessionState,
)
from ledger.domain.aggregates.compliance_record import ComplianceRecordAggregate
from ledger.domain.aggregates.loan_application import (
    ApplicationState,
    LoanApplicationAggregate,
)
from src.models.events import DomainError
from ledger.schema.events import (
    AgentSessionStarted,
    AgentType,
    ApplicationApproved,
    ApplicationDeclined,
    ApplicationSubmitted,
    ComplianceCheckCompleted,
    ComplianceCheckInitiated,
    ComplianceRuleFailed,
    ComplianceRuleNoted,
    ComplianceRulePassed,
    ComplianceVerdict,
    CreditAnalysisCompleted,
    CreditDecision,
    CreditRecordOpened,
    DecisionGenerated,
    DecisionRequested,
    FraudScreeningCompleted,
    ComplianceCheckRequested,
    DocumentUploadRequested,
    HumanReviewCompleted,
    HumanReviewRequested,
    LoanPurpose,
    RiskTier,
)


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _as_datetime(value: Any | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _replay_loan_validation(loan: LoanApplicationAggregate, events: list[dict[str, Any]]) -> None:
    test_copy = deepcopy(loan)
    for event in events:
        test_copy.apply(event)


def _agent_stream_id(agent_type: str, session_id: str) -> str:
    return f"agent-{agent_type}-{session_id}"


async def _validate_contributing_sessions(
    store,
    application_id: str,
    contributing_sessions: list[str],
) -> None:
    decision_event_types = {
        "CreditAnalysisCompleted",
        "FraudScreeningCompleted",
        "ComplianceCheckCompleted",
        "DecisionGenerated",
    }
    for stream_id in contributing_sessions:
        events = await store.load_stream(stream_id)
        if not events:
            raise DomainError(f"Contributing session stream not found: {stream_id}")
        has_decision_event = any(
            ev.get("event_type") in decision_event_types
            and str((ev.get("payload") or {}).get("application_id", "")) == application_id
            for ev in events
        )
        if not has_decision_event:
            raise DomainError(
                f"Contributing session '{stream_id}' has no decision event for application '{application_id}'"
            )


async def _ensure_compliance_ready_for_approval(store, application_id: str) -> None:
    compliance = await ComplianceRecordAggregate.load(store, application_id)
    loan = await LoanApplicationAggregate.load(store, application_id)
    if not compliance.completed:
        raise DomainError("ApplicationApproved requires completed compliance record")
    loan.assert_can_approve(
        required_rules=compliance.required_rules,
        passed_rules=compliance.passed_rules,
        verdict=compliance.verdict,
    )


async def handle_submit_application(store, command: dict[str, Any]) -> list[int]:
    application_id = str(command["application_id"])
    loan = await LoanApplicationAggregate.load(store, application_id)

    if loan.state != ApplicationState.NEW:
        raise DomainError(f"Application '{application_id}' already exists in state {loan.state.value}")

    requested_amount = _as_decimal(command["requested_amount_usd"])
    if requested_amount <= 0:
        raise DomainError("requested_amount_usd must be > 0")

    now = _as_datetime(command.get("submitted_at"))
    deadline = _as_datetime(command.get("deadline")) if command.get("deadline") else now + timedelta(days=7)

    new_events = [
        ApplicationSubmitted(
            application_id=application_id,
            applicant_id=str(command["applicant_id"]),
            requested_amount_usd=requested_amount,
            loan_purpose=LoanPurpose(str(command.get("loan_purpose", LoanPurpose.WORKING_CAPITAL.value))),
            loan_term_months=int(command.get("loan_term_months", 36)),
            submission_channel=str(command.get("submission_channel", "web")),
            contact_email=str(command.get("contact_email", "unknown@example.com")),
            contact_name=str(command.get("contact_name", "Unknown")),
            submitted_at=now,
            application_reference=str(command.get("application_reference", application_id)),
        ).to_store_dict(),
        DocumentUploadRequested(
            application_id=application_id,
            required_document_types=command.get(
                "required_document_types",
                ["application_proposal", "income_statement", "balance_sheet"],
            ),
            deadline=deadline,
            requested_by=str(command.get("requested_by", "system")),
        ).to_store_dict(),
    ]
    _replay_loan_validation(loan, new_events)

    return await store.append(
        stream_id=f"loan-{application_id}",
        events=new_events,
        expected_version=loan.version,
        correlation_id=str(command.get("correlation_id", "")) or None,
        causation_id=str(command.get("causation_id", "")) or None,
    )


async def handle_start_agent_session(store, command: dict[str, Any]) -> list[int]:
    application_id = str(command["application_id"])
    session_id = str(command["session_id"])
    agent_id = str(command["agent_id"])
    agent_type = str(command["agent_type"])

    stream_id = _agent_stream_id(agent_type, session_id)
    session = await AgentSessionAggregate.load(store, stream_id)
    if session.state != AgentSessionState.NEW:
        raise DomainError(f"Agent session already exists: {stream_id}")

    start_event = AgentSessionStarted(
        session_id=session_id,
        agent_type=AgentType(agent_type),
        agent_id=agent_id,
        application_id=application_id,
        model_version=str(command["model_version"]),
        langgraph_graph_version=str(command.get("langgraph_graph_version", "unknown")),
        context_source=str(command.get("context_source", "fresh")),
        context_token_count=int(command.get("context_token_count", 0)),
        started_at=_as_datetime(command.get("started_at")),
    ).to_store_dict()

    return await store.append(
        stream_id=stream_id,
        events=[start_event],
        expected_version=session.version,
        correlation_id=str(command.get("correlation_id", "")) or None,
        causation_id=str(command.get("causation_id", "")) or None,
    )


async def handle_credit_analysis_completed(store, command: dict[str, Any]) -> list[int]:
    application_id = str(command["application_id"])
    session_id = str(command["session_id"])
    agent_type = str(command.get("agent_type", AgentType.CREDIT_ANALYSIS.value))
    loan = await LoanApplicationAggregate.load(store, application_id)
    session = await AgentSessionAggregate.load(store, _agent_stream_id(agent_type, session_id))

    session.assert_context_loaded()
    model_version = str(command["model_version"])
    session.assert_model_version_matches(model_version)

    credit_stream = f"credit-{application_id}"
    existing_credit_events = await store.load_stream(credit_stream)
    loan.assert_can_record_credit_analysis_completed(
        existing_credit_events=existing_credit_events,
        superseded_by_human_review=bool(command.get("superseded_by_human_review", False)),
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

    event = CreditAnalysisCompleted(
        application_id=application_id,
        session_id=session_id,
        decision=decision,
        model_version=model_version,
        model_deployment_id=str(command.get("model_deployment_id", "unknown-deployment")),
        input_data_hash=str(command.get("input_data_hash", "unknown-hash")),
        analysis_duration_ms=int(command.get("analysis_duration_ms", 0)),
        regulatory_basis=list(command.get("regulatory_basis", [])),
        completed_at=_as_datetime(command.get("completed_at")),
    ).to_store_dict()

    current_version = await store.stream_version(credit_stream)
    if current_version == -1:
        open_event = CreditRecordOpened(
            application_id=application_id,
            applicant_id=loan.applicant_id or "unknown",
            opened_at=_as_datetime(command.get("completed_at")),
        ).to_store_dict()
        return await store.append(credit_stream, [open_event, event], expected_version=-1)
    return await store.append(credit_stream, [event], expected_version=current_version)


async def handle_compliance_check(store, command: dict[str, Any]) -> list[int]:
    application_id = str(command["application_id"])
    session_id = str(command["session_id"])
    now = _as_datetime(command.get("completed_at"))

    compliance_stream = f"compliance-{application_id}"
    compliance_version = await store.stream_version(compliance_stream)
    compliance_events: list[dict[str, Any]] = []

    if compliance_version == -1:
        compliance_events.append(
            ComplianceCheckInitiated(
                application_id=application_id,
                session_id=session_id,
                regulation_set_version=str(command.get("regulation_set_version", "2026-Q1")),
                rules_to_evaluate=list(command.get("rules_to_evaluate", [])),
                initiated_at=now,
            ).to_store_dict()
        )

    for rule in command.get("rules_passed", []):
        compliance_events.append(
            ComplianceRulePassed(
                application_id=application_id,
                session_id=session_id,
                rule_id=str(rule["rule_id"]),
                rule_name=str(rule.get("rule_name", rule["rule_id"])),
                rule_version=str(rule.get("rule_version", "1.0")),
                evidence_hash=str(rule.get("evidence_hash", "unknown")),
                evaluation_notes=str(rule.get("evaluation_notes", "")),
                evaluated_at=_as_datetime(rule.get("evaluated_at")),
            ).to_store_dict()
        )

    for rule in command.get("rules_failed", []):
        compliance_events.append(
            ComplianceRuleFailed(
                application_id=application_id,
                session_id=session_id,
                rule_id=str(rule["rule_id"]),
                rule_name=str(rule.get("rule_name", rule["rule_id"])),
                rule_version=str(rule.get("rule_version", "1.0")),
                failure_reason=str(rule.get("failure_reason", "")),
                is_hard_block=bool(rule.get("is_hard_block", False)),
                remediation_available=bool(rule.get("remediation_available", False)),
                remediation_description=rule.get("remediation_description"),
                evidence_hash=str(rule.get("evidence_hash", "unknown")),
                evaluated_at=_as_datetime(rule.get("evaluated_at")),
            ).to_store_dict()
        )

    for rule in command.get("rules_noted", []):
        compliance_events.append(
            ComplianceRuleNoted(
                application_id=application_id,
                session_id=session_id,
                rule_id=str(rule["rule_id"]),
                rule_name=str(rule.get("rule_name", rule["rule_id"])),
                note_type=str(rule.get("note_type", "INFO")),
                note_text=str(rule.get("note_text", "")),
                evaluated_at=_as_datetime(rule.get("evaluated_at")),
            ).to_store_dict()
        )

    verdict = str(command.get("overall_verdict", "CLEAR")).upper()
    compliance_events.append(
        ComplianceCheckCompleted(
            application_id=application_id,
            session_id=session_id,
            rules_evaluated=int(command.get("rules_evaluated", len(compliance_events))),
            rules_passed=int(command.get("rules_passed_count", len(command.get("rules_passed", [])))),
            rules_failed=int(command.get("rules_failed_count", len(command.get("rules_failed", [])))),
            rules_noted=int(command.get("rules_noted_count", len(command.get("rules_noted", [])))),
            has_hard_block=bool(command.get("has_hard_block", verdict == "BLOCKED")),
            overall_verdict=ComplianceVerdict(verdict),
            completed_at=now,
        ).to_store_dict()
    )

    written = await store.append(compliance_stream, compliance_events, expected_version=compliance_version)

    loan = await LoanApplicationAggregate.load(store, application_id)
    next_events = [
        ComplianceCheckCompleted(
            application_id=application_id,
            session_id=session_id,
            rules_evaluated=int(command.get("rules_evaluated", len(compliance_events))),
            rules_passed=int(command.get("rules_passed_count", len(command.get("rules_passed", [])))),
            rules_failed=int(command.get("rules_failed_count", len(command.get("rules_failed", [])))),
            rules_noted=int(command.get("rules_noted_count", len(command.get("rules_noted", [])))),
            has_hard_block=bool(command.get("has_hard_block", verdict == "BLOCKED")),
            overall_verdict=ComplianceVerdict(verdict),
            completed_at=now,
        ).to_store_dict()
    ]
    if verdict in ("CLEAR", "CONDITIONAL"):
        next_events.append(
            DecisionRequested(
                application_id=application_id,
                requested_at=now,
                all_analyses_complete=True,
                triggered_by_event_id=str(command.get("triggered_by_event_id", "compliance-check-completed")),
            ).to_store_dict()
        )
    else:
        next_events.append(
            ApplicationDeclined(
                application_id=application_id,
                decline_reasons=list(command.get("decline_reasons", ["Compliance hard block"])),
                declined_by=str(command.get("declined_by", "compliance_agent")),
                adverse_action_notice_required=bool(command.get("adverse_action_notice_required", True)),
                adverse_action_codes=list(command.get("adverse_action_codes", ["COMPLIANCE_BLOCK"])),
                declined_at=now,
            ).to_store_dict()
        )
    _replay_loan_validation(loan, next_events)
    await store.append(f"loan-{application_id}", next_events, expected_version=loan.version)
    return written


async def handle_fraud_screening_completed(store, command: dict[str, Any]) -> list[int]:
    application_id = str(command["application_id"])
    session_id = str(command["session_id"])
    now = _as_datetime(command.get("completed_at"))

    fraud_stream = f"fraud-{application_id}"
    fraud_version = await store.stream_version(fraud_stream)
    fraud_event = FraudScreeningCompleted(
        application_id=application_id,
        session_id=session_id,
        fraud_score=float(command.get("fraud_score", 0.0)),
        risk_level=str(command.get("risk_level", "LOW")),
        anomalies_found=int(command.get("anomalies_found", 0)),
        recommendation=str(command.get("recommendation", "PROCEED")),
        screening_model_version=str(command.get("screening_model_version", "unknown")),
        input_data_hash=str(command.get("input_data_hash", "unknown")),
        completed_at=now,
    ).to_store_dict()
    written = await store.append(fraud_stream, [fraud_event], expected_version=fraud_version)

    loan = await LoanApplicationAggregate.load(store, application_id)
    loan_events = [
        FraudScreeningCompleted(
            application_id=application_id,
            session_id=session_id,
            fraud_score=float(command.get("fraud_score", 0.0)),
            risk_level=str(command.get("risk_level", "LOW")),
            anomalies_found=int(command.get("anomalies_found", 0)),
            recommendation=str(command.get("recommendation", "PROCEED")),
            screening_model_version=str(command.get("screening_model_version", "unknown")),
            input_data_hash=str(command.get("input_data_hash", "unknown")),
            completed_at=now,
        ).to_store_dict(),
        ComplianceCheckRequested(
            application_id=application_id,
            requested_at=now,
            triggered_by_event_id=str(command.get("triggered_by_event_id", "fraud-screening-completed")),
            regulation_set_version=str(command.get("regulation_set_version", "2026-Q1")),
            rules_to_evaluate=list(command.get("rules_to_evaluate", [])),
        ).to_store_dict(),
    ]
    _replay_loan_validation(loan, loan_events)
    await store.append(f"loan-{application_id}", loan_events, expected_version=loan.version)
    return written


async def handle_generate_decision(store, command: dict[str, Any]) -> list[int]:
    application_id = str(command["application_id"])
    loan = await LoanApplicationAggregate.load(store, application_id)

    recommendation = str(command["recommendation"]).upper()
    confidence = float(command["confidence"])
    recommendation = loan.assert_can_generate_decision(recommendation, confidence)

    contributing_sessions = list(command.get("contributing_sessions", []))
    await _validate_contributing_sessions(store, application_id, contributing_sessions)

    now = _as_datetime(command.get("generated_at"))
    decision_event = DecisionGenerated(
        application_id=application_id,
        orchestrator_session_id=str(command["orchestrator_session_id"]),
        recommendation=recommendation,
        confidence=confidence,
        approved_amount_usd=_as_decimal(command["approved_amount_usd"])
        if command.get("approved_amount_usd") is not None
        else None,
        conditions=list(command.get("conditions", [])),
        executive_summary=str(command.get("executive_summary", "")),
        key_risks=list(command.get("key_risks", [])),
        contributing_sessions=contributing_sessions,
        model_versions=dict(command.get("model_versions", {})),
        generated_at=now,
    ).to_store_dict()

    events = [decision_event]
    if recommendation == "APPROVE":
        await _ensure_compliance_ready_for_approval(store, application_id)
        events.append(
            ApplicationApproved(
                application_id=application_id,
                approved_amount_usd=_as_decimal(command["approved_amount_usd"]),
                interest_rate_pct=float(command.get("interest_rate_pct", 12.5)),
                term_months=int(command.get("term_months", 36)),
                conditions=list(command.get("conditions", [])),
                approved_by=str(command.get("approved_by", "auto")),
                effective_date=str(command.get("effective_date", now.date().isoformat())),
                approved_at=now,
            ).to_store_dict()
        )
    elif recommendation == "DECLINE":
        events.append(
            ApplicationDeclined(
                application_id=application_id,
                decline_reasons=list(command.get("decline_reasons", ["Risk policy decline"])),
                declined_by=str(command.get("declined_by", "decision_orchestrator")),
                adverse_action_notice_required=bool(command.get("adverse_action_notice_required", True)),
                adverse_action_codes=list(command.get("adverse_action_codes", [])),
                declined_at=now,
            ).to_store_dict()
        )
    else:
        events.append(
            HumanReviewRequested(
                application_id=application_id,
                reason=str(command.get("review_reason", "Recommendation REFER or low confidence")),
                decision_event_id=str(command.get("decision_event_id", "pending")),
                assigned_to=command.get("assigned_to"),
                requested_at=now,
            ).to_store_dict()
        )

    _replay_loan_validation(loan, events)
    return await store.append(
        stream_id=f"loan-{application_id}",
        events=events,
        expected_version=loan.version,
        correlation_id=str(command.get("correlation_id", "")) or None,
        causation_id=str(command.get("causation_id", "")) or None,
    )


async def handle_human_review_completed(store, command: dict[str, Any]) -> list[int]:
    application_id = str(command["application_id"])
    final_decision = str(command["final_decision"]).upper()
    now = _as_datetime(command.get("reviewed_at"))
    loan = await LoanApplicationAggregate.load(store, application_id)

    events = [
        HumanReviewCompleted(
            application_id=application_id,
            reviewer_id=str(command["reviewer_id"]),
            override=bool(command.get("override", False)),
            original_recommendation=str(command.get("original_recommendation", "REFER")),
            final_decision=final_decision,
            override_reason=command.get("override_reason"),
            reviewed_at=now,
        ).to_store_dict()
    ]

    if final_decision == "APPROVE":
        await _ensure_compliance_ready_for_approval(store, application_id)
        events.append(
            ApplicationApproved(
                application_id=application_id,
                approved_amount_usd=_as_decimal(command["approved_amount_usd"]),
                interest_rate_pct=float(command.get("interest_rate_pct", 12.5)),
                term_months=int(command.get("term_months", 36)),
                conditions=list(command.get("conditions", [])),
                approved_by=str(command.get("reviewer_id")),
                effective_date=str(command.get("effective_date", now.date().isoformat())),
                approved_at=now,
            ).to_store_dict()
        )
    elif final_decision == "DECLINE":
        events.append(
            ApplicationDeclined(
                application_id=application_id,
                decline_reasons=list(command.get("decline_reasons", ["Human reviewer decline"])),
                declined_by=str(command.get("reviewer_id")),
                adverse_action_notice_required=bool(command.get("adverse_action_notice_required", True)),
                adverse_action_codes=list(command.get("adverse_action_codes", [])),
                declined_at=now,
            ).to_store_dict()
        )

    _replay_loan_validation(loan, events)
    return await store.append(
        stream_id=f"loan-{application_id}",
        events=events,
        expected_version=loan.version,
        correlation_id=str(command.get("correlation_id", "")) or None,
        causation_id=str(command.get("causation_id", "")) or None,
    )
