from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ledger.event_store import InMemoryEventStore
from ledger.schema.events import AgentSessionStarted, AgentType


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event(event_type: str, payload: dict[str, Any], recorded_at: datetime) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "payload": payload,
        "recorded_at": _iso(recorded_at),
    }


async def build_narr05_demo_store() -> tuple[InMemoryEventStore, str]:
    """Build a deterministic in-memory store for the NARR-05 human-override demo."""

    store = InMemoryEventStore()
    application_id = "NARR-05"
    session_credit = "sess-credit-narr05"
    session_fraud = "sess-fraud-narr05"
    session_compliance = "sess-compliance-narr05"
    session_orchestrator = "sess-orchestrator-narr05"
    session_reviewer = "LO-Sarah-Chen"

    t0 = datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc)

    async def seed() -> None:
        await store.append(
            f"loan-{application_id}",
            [
                _event(
                    "ApplicationSubmitted",
                    {
                        "application_id": application_id,
                        "applicant_id": "COMP-007",
                        "requested_amount_usd": 750000,
                        "loan_purpose": "expansion",
                        "loan_term_months": 48,
                        "submission_channel": "relationship-manager",
                        "contact_email": "finance@apex-industrial.example",
                        "contact_name": "Apex Industrial Finance",
                        "application_reference": application_id,
                    },
                    t0,
                ),
            ],
            expected_version=-1,
        )

        await store.append(
            f"agent-{AgentType.DECISION_ORCHESTRATOR.value}-{session_orchestrator}",
            [
                AgentSessionStarted(
                    session_id=session_orchestrator,
                    agent_type=AgentType.DECISION_ORCHESTRATOR,
                    agent_id="orchestrator-agent-01",
                    application_id=application_id,
                    model_version="orchestrator-v1",
                    langgraph_graph_version="demo-v1",
                    context_source="fresh",
                    context_token_count=1024,
                    started_at=t0.replace(hour=10, minute=4),
                ).to_store_dict()
            ],
            expected_version=-1,
        )

        await store.append(
            f"agent-{AgentType.CREDIT_ANALYSIS.value}-{session_credit}",
            [
                AgentSessionStarted(
                    session_id=session_credit,
                    agent_type=AgentType.CREDIT_ANALYSIS,
                    agent_id="credit-agent-01",
                    application_id=application_id,
                    model_version="credit-v5",
                    langgraph_graph_version="demo-v1",
                    context_source="fresh",
                    context_token_count=812,
                    started_at=t0.replace(hour=9, minute=4),
                ).to_store_dict()
            ],
            expected_version=-1,
        )

        await store.append(
            f"credit-{application_id}",
            [
                _event(
                    "CreditRecordOpened",
                    {
                        "application_id": application_id,
                        "applicant_id": "COMP-007",
                        "opened_at": _iso(t0.replace(hour=9, minute=5)),
                    },
                    t0.replace(hour=9, minute=5),
                ),
                _event(
                    "CreditAnalysisCompleted",
                    {
                        "application_id": application_id,
                        "session_id": session_credit,
                        "decision": {
                            "risk_tier": "LOW",
                            "recommended_limit_usd": 820000,
                            "confidence": 0.91,
                            "rationale": (
                                "Healthy leverage, stable historical growth, and strong liquidity"
                                " support a generous facility."
                            ),
                            "key_concerns": ["Large facility relative to current revenue"],
                            "data_quality_caveats": [],
                            "policy_overrides_applied": [],
                        },
                        "model_version": "credit-v5",
                        "model_deployment_id": "credit-deploy-01",
                        "input_data_hash": "credit-hash-01",
                        "analysis_duration_ms": 1480,
                        "regulatory_basis": ["capital-adequacy", "ability-to-repay"],
                        "completed_at": _iso(t0.replace(hour=9, minute=12)),
                    },
                    t0.replace(hour=9, minute=12),
                ),
            ],
            expected_version=-1,
        )

        await store.append(
            f"agent-{AgentType.FRAUD_DETECTION.value}-{session_fraud}",
            [
                AgentSessionStarted(
                    session_id=session_fraud,
                    agent_type=AgentType.FRAUD_DETECTION,
                    agent_id="fraud-agent-01",
                    application_id=application_id,
                    model_version="fraud-v2",
                    langgraph_graph_version="demo-v1",
                    context_source="fresh",
                    context_token_count=640,
                    started_at=t0.replace(hour=9, minute=14),
                ).to_store_dict()
            ],
            expected_version=-1,
        )

        await store.append(
            f"loan-{application_id}",
            [
                _event(
                    "DecisionGenerated",
                    {
                        "application_id": application_id,
                        "orchestrator_session_id": session_orchestrator,
                        "recommendation": "DECLINE",
                        "confidence": 0.64,
                        "approved_amount_usd": None,
                        "conditions": [],
                        "executive_summary": (
                            "The automated decision engine recommends decline because the deal"
                            " is outside its preferred exposure band, even though the company"
                            " remains fundamentally healthy."
                        ),
                        "key_risks": ["Large requested amount relative to policy appetite"],
                        "contributing_sessions": [
                            f"credit-{application_id}",
                            f"fraud-{application_id}",
                            f"compliance-{application_id}",
                        ],
                        "model_versions": {
                            "credit": "credit-v5",
                            "fraud": "fraud-v2",
                            "compliance": "compliance-v3",
                            "orchestrator": "orchestrator-v1",
                        },
                        "generated_at": _iso(t0.replace(hour=10, minute=5)),
                    },
                    t0.replace(hour=10, minute=5),
                ),
                _event(
                    "HumanReviewCompleted",
                    {
                        "application_id": application_id,
                        "reviewer_id": session_reviewer,
                        "override": True,
                        "original_recommendation": "DECLINE",
                        "final_decision": "APPROVE",
                        "override_reason": "Relationship history, strong cash flow, and strategic retention value.",
                        "reviewed_at": _iso(t0.replace(hour=10, minute=20)),
                    },
                    t0.replace(hour=10, minute=20),
                ),
                _event(
                    "ApplicationApproved",
                    {
                        "application_id": application_id,
                        "approved_amount_usd": 750000,
                        "interest_rate_pct": 11.25,
                        "term_months": 48,
                        "conditions": [
                            "Quarterly covenant reporting",
                            "Maintain minimum cash balance above 300000 USD",
                        ],
                        "approved_by": session_reviewer,
                        "effective_date": "2026-03-20",
                        "approved_at": _iso(t0.replace(hour=10, minute=20, second=30)),
                    },
                    t0.replace(hour=10, minute=20, second=30),
                ),
            ],
            expected_version=0,
        )

        await store.append(
            f"fraud-{application_id}",
            [
                _event(
                    "FraudScreeningCompleted",
                    {
                        "application_id": application_id,
                        "session_id": session_fraud,
                        "fraud_score": 0.14,
                        "risk_level": "LOW",
                        "anomalies_found": 0,
                        "recommendation": "PROCEED",
                        "screening_model_version": "fraud-v2",
                        "input_data_hash": "fraud-hash-01",
                        "completed_at": _iso(t0.replace(hour=9, minute=15)),
                    },
                    t0.replace(hour=9, minute=15),
                )
            ],
            expected_version=-1,
        )

        await store.append(
            f"agent-{AgentType.COMPLIANCE.value}-{session_compliance}",
            [
                AgentSessionStarted(
                    session_id=session_compliance,
                    agent_type=AgentType.COMPLIANCE,
                    agent_id="compliance-agent-01",
                    application_id=application_id,
                    model_version="compliance-v3",
                    langgraph_graph_version="demo-v1",
                    context_source="fresh",
                    context_token_count=588,
                    started_at=t0.replace(hour=9, minute=17),
                ).to_store_dict()
            ],
            expected_version=-1,
        )

        await store.append(
            f"compliance-{application_id}",
            [
                _event(
                    "ComplianceCheckInitiated",
                    {
                        "application_id": application_id,
                        "session_id": session_compliance,
                        "regulation_set_version": "2026-Q1",
                        "rules_to_evaluate": ["REG-001", "REG-002", "REG-004"],
                    },
                    t0.replace(hour=9, minute=18),
                ),
                _event(
                    "ComplianceRulePassed",
                    {
                        "application_id": application_id,
                        "session_id": session_compliance,
                        "rule_id": "REG-001",
                        "rule_name": "KYC",
                        "rule_version": "1.0",
                        "evidence_hash": "rule-001",
                        "evaluation_notes": "Identity and beneficial ownership validated.",
                    },
                    t0.replace(hour=9, minute=19),
                ),
                _event(
                    "ComplianceRulePassed",
                    {
                        "application_id": application_id,
                        "session_id": session_compliance,
                        "rule_id": "REG-002",
                        "rule_name": "AML",
                        "rule_version": "1.0",
                        "evidence_hash": "rule-002",
                        "evaluation_notes": "No adverse AML signals detected.",
                    },
                    t0.replace(hour=9, minute=20),
                ),
                _event(
                    "ComplianceCheckCompleted",
                    {
                        "application_id": application_id,
                        "session_id": session_compliance,
                        "rules_evaluated": 3,
                        "rules_passed": 2,
                        "rules_failed": 0,
                        "rules_noted": 1,
                        "has_hard_block": False,
                        "overall_verdict": "CLEAR",
                        "completed_at": _iso(t0.replace(hour=9, minute=21)),
                    },
                    t0.replace(hour=9, minute=21),
                ),
            ],
            expected_version=-1,
        )

    await seed()
    return store, application_id
