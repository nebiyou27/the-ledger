from __future__ import annotations

import pytest

from ledger.agents.credit_analysis_agent import CreditAnalysisAgent
from ledger.registry.client import CompanyProfile, ComplianceFlag, FinancialYear


class DummyStore:
    def __init__(self, streams: dict[str, list[dict]] | None = None):
        self.streams = streams or {}

    async def load_stream(self, stream_id: str):
        return list(self.streams.get(stream_id, []))


class DummyRegistry:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    async def get_company(self, company_id: str):
        self.calls.append(("get_company", company_id))
        return CompanyProfile(
            company_id=company_id,
            name="Acme Inc",
            industry="manufacturing",
            naics="332710",
            jurisdiction="TX",
            legal_type="LLC",
            founded_year=2012,
            employee_count=50,
            risk_segment="MEDIUM",
            trajectory="STABLE",
            submission_channel="web",
            ip_region="US-Central",
        )

    async def get_financial_history(self, company_id: str, years=None):
        self.calls.append(("get_financial_history", (company_id, years)))
        return [
            FinancialYear(
                fiscal_year=2022,
                total_revenue=1000000.0,
                gross_profit=400000.0,
                operating_income=120000.0,
                ebitda=140000.0,
                net_income=90000.0,
                total_assets=1800000.0,
                total_liabilities=800000.0,
                total_equity=1000000.0,
                long_term_debt=500000.0,
                cash_and_equivalents=130000.0,
                current_assets=600000.0,
                current_liabilities=300000.0,
                accounts_receivable=200000.0,
                inventory=100000.0,
                debt_to_equity=0.8,
                current_ratio=2.0,
                debt_to_ebitda=5.7,
                interest_coverage_ratio=3.1,
                gross_margin=0.4,
                ebitda_margin=0.14,
                net_margin=0.09,
            )
        ]

    async def get_compliance_flags(self, company_id: str, active_only: bool = False):
        self.calls.append(("get_compliance_flags", (company_id, active_only)))
        return [
            ComplianceFlag(
                flag_type="AML_WATCH",
                severity="MEDIUM",
                is_active=True,
                added_date="2025-01-10",
                note="watch",
            )
        ]

    async def get_loan_relationships(self, company_id: str):
        self.calls.append(("get_loan_relationships", company_id))
        return [{"id": 1, "loan_year": 2020, "default_occurred": True, "was_repaid": False, "loan_amount": 100000.0, "note": "default"}]


class MissingProfileRegistry(DummyRegistry):
    async def get_company(self, company_id: str):
        self.calls.append(("get_company", company_id))
        return None


@pytest.mark.asyncio
async def test_prepare_application_state_hydrates_from_loan_and_document_streams():
    store = DummyStore(
        {
            "loan-APEX-0001": [
                {
                    "event_type": "ApplicationSubmitted",
                    "payload": {
                        "application_id": "APEX-0001",
                        "applicant_id": "COMP-001",
                        "requested_amount_usd": "250000.00",
                        "loan_purpose": "equipment_financing",
                        "loan_term_months": 36,
                    },
                }
            ],
            "docpkg-APEX-0001": [
                {
                    "event_type": "ExtractionCompleted",
                    "payload": {
                        "facts": {
                            "total_revenue": 900000.0,
                        }
                    },
                }
            ],
        }
    )
    registry = DummyRegistry()
    agent = CreditAnalysisAgent(
        agent_id="agent-1",
        agent_type="credit_analysis",
        store=store,
        registry=registry,
        client=None,
    )

    prepared = await agent._prepare_application_state("APEX-0001")

    assert prepared["applicant_id"] == "COMP-001"
    assert prepared["loan_amount"] == 250000.0
    assert prepared["loan_term_months"] == 36
    assert prepared["loan_purpose"] == "equipment_financing"
    assert prepared["annual_income"] == 900000.0


@pytest.mark.asyncio
async def test_node_load_registry_uses_real_registry_and_appends_consumed_event():
    registry = DummyRegistry()
    agent = CreditAnalysisAgent(
        agent_id="agent-1",
        agent_type="credit_analysis",
        store=DummyStore(),
        registry=registry,
        client=None,
    )
    agent.session_id = "sess-cre-123"

    append_calls = []

    async def _append(stream_id, events, causation_id=None):
        append_calls.append((stream_id, events, causation_id))

    async def _noop(*args, **kwargs):
        return None

    agent._append_with_retry = _append
    agent._record_tool_call = _noop
    agent._record_node_execution = _noop

    state = {
        "application_id": "APEX-0001",
        "session_id": "sess-cre-123",
        "applicant_id": "COMP-001",
        "requested_amount_usd": 500000.0,
        "loan_purpose": "working_capital",
        "company_profile": None,
        "historical_financials": None,
        "compliance_flags": None,
        "loan_history": None,
        "extracted_facts": None,
        "quality_flags": None,
        "document_ids_consumed": None,
        "credit_decision": None,
        "policy_violations": None,
        "errors": [],
        "output_events": [],
        "next_agent": None,
    }

    out = await agent._node_load_registry(state)

    assert out["company_profile"]["company_id"] == "COMP-001"
    assert len(out["historical_financials"]) == 1
    assert len(out["compliance_flags"]) == 1
    assert len(out["loan_history"]) == 1

    assert ("get_company", "COMP-001") in registry.calls
    assert ("get_financial_history", ("COMP-001", [2022, 2023, 2024])) in registry.calls
    assert ("get_compliance_flags", ("COMP-001", False)) in registry.calls
    assert ("get_loan_relationships", "COMP-001") in registry.calls

    assert len(append_calls) == 1
    stream_id, events, _ = append_calls[0]
    assert stream_id == "credit-APEX-0001"
    assert events[0]["event_type"] == "HistoricalProfileConsumed"


@pytest.mark.asyncio
async def test_node_load_registry_raises_if_company_missing():
    registry = MissingProfileRegistry()
    agent = CreditAnalysisAgent(
        agent_id="agent-1",
        agent_type="credit_analysis",
        store=DummyStore(),
        registry=registry,
        client=None,
    )
    agent.session_id = "sess-cre-123"

    async def _noop(*args, **kwargs):
        return None

    agent._record_tool_call = _noop
    agent._record_node_execution = _noop
    agent._append_with_retry = _noop

    state = {
        "application_id": "APEX-0001",
        "session_id": "sess-cre-123",
        "applicant_id": "COMP-404",
        "requested_amount_usd": 500000.0,
        "loan_purpose": "working_capital",
        "company_profile": None,
        "historical_financials": None,
        "compliance_flags": None,
        "loan_history": None,
        "extracted_facts": None,
        "quality_flags": None,
        "document_ids_consumed": None,
        "credit_decision": None,
        "policy_violations": None,
        "errors": [],
        "output_events": [],
        "next_agent": None,
    }

    with pytest.raises(ValueError):
        await agent._node_load_registry(state)
