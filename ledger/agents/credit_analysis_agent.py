"""
ledger/agents/credit_analysis_agent.py
=======================================
CREDIT ANALYSIS AGENT — complete LangGraph reference implementation.

This is the reference agent. Read this fully before implementing
FraudDetectionAgent, ComplianceAgent, or DecisionOrchestratorAgent.

LangGraph nodes (in order):
  validate_inputs → open_credit_record → load_applicant_registry →
  load_extracted_facts → analyze_credit_risk → apply_policy_constraints →
  write_output

Input streams read:
  docpkg-{id}  → ExtractionCompleted events (current-year GAAP facts)

Databases queried:
  applicant_registry.companies         (read-only)
  applicant_registry.financial_history (read-only)
  applicant_registry.compliance_flags  (read-only)
  applicant_registry.loan_relationships(read-only)

Output events written:
  credit-{id}: CreditRecordOpened, HistoricalProfileConsumed,
               ExtractedFactsConsumed, CreditAnalysisCompleted (or CreditAnalysisDeferred)
  loan-{id}:   FraudScreeningRequested  (triggers next agent)

WHEN THIS WORKS:
  pytest tests/phase2/test_credit_agent.py   # all pass
  python scripts/run_pipeline.py --app APEX-0007 --phase credit
    → CreditAnalysisCompleted event in event store
    → rationale field is non-empty prose
    → confidence between 0.60 and 0.95
    → FraudScreeningRequested event on loan stream
"""
from __future__ import annotations
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Annotated, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, END

from ledger.agents.base_agent import BaseApexAgent
from ledger.domain.aggregates.loan_application import (
    ApplicationState,
    LoanApplicationAggregate,
)
from ledger.schema.events import (
    CreditRecordOpened, HistoricalProfileConsumed, ExtractedFactsConsumed,
    CreditAnalysisCompleted, CreditAnalysisDeferred,
    FraudScreeningRequested,
    CreditDecision, RiskTier, FinancialFacts,
)

try:
    from ledger.agents.ollama_client import OllamaClient
    _ollama = OllamaClient()
except ImportError:
    _ollama = None


# ─── STATE ────────────────────────────────────────────────────────────────────

class CreditState(TypedDict):
    # Identity
    application_id: str
    session_id: str
    applicant_id: str | None
    loan_amount: float | None
    annual_income: float | None
    loan_term_months: int | None
    monthly_debt: float | None
    annual_rate: float | None
    dti_ratio: float | None
    loan_to_income_ratio: float | None
    monthly_payment_estimate: float | None
    prior_defaults: int | None
    prior_loans: int | None
    account_age_months: int | None
    factor_scores: dict | None
    credit_score: float | None
    policy_outcome: str | None
    hard_gate: str | None
    explanation: str | None
    key_factors: list[str] | None
    confidence: str | None
    # Plumbing
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


@dataclass(slots=True)
class CreditAnalysisError(ValueError):
    field: str
    reason: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, f"{self.field}: {self.reason}")


# ─── AGENT ────────────────────────────────────────────────────────────────────

class CreditAnalysisAgent(BaseApexAgent):

    def build_graph(self) -> Any:
        g = StateGraph(CreditState)
        g.add_node("validate_inputs",          self._node_validate_inputs)
        g.add_node("load_applicant_registry",  self._node_load_registry)
        g.add_node("load_extracted_facts",     self._node_load_facts)
        g.add_node("analyze_credit_risk",      self._node_analyze)
        g.add_node("apply_policy_constraints", self._node_policy)
        g.add_node("explain_decision",         self._node_explain_decision)
        g.add_node("write_output",             self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs",          "load_applicant_registry")
        g.add_edge("load_applicant_registry",  "load_extracted_facts")
        g.add_edge("load_extracted_facts",     "analyze_credit_risk")
        g.add_edge("analyze_credit_risk",      "apply_policy_constraints")
        g.add_edge("apply_policy_constraints", "explain_decision")
        g.add_edge("explain_decision",         "write_output")
        g.add_edge("write_output",             END)
        return g.compile()

    def _initial_state(
        self,
        application_id: str,
        recovery_context_text=None,
        recovery_pending_work=None,
        recovered_from_session_id=None,
        recovery_point=None,
        resume_after_sequence: int = -1,
        resume_state_snapshot: dict | None = None,
    ) -> CreditState:
        state = CreditState(
            application_id=application_id, session_id=self.session_id,
            applicant_id=None, loan_amount=None, annual_income=None,
            loan_term_months=None, monthly_debt=None, annual_rate=None,
            dti_ratio=None, loan_to_income_ratio=None, monthly_payment_estimate=None,
            prior_defaults=None, prior_loans=None, account_age_months=None,
            factor_scores=None, credit_score=None, policy_outcome=None,
            hard_gate=None, explanation=None, key_factors=None, confidence=None,
            errors=[], output_events=[], next_agent=None,
        )
        if recovery_context_text is not None:
            state["recovery_context_text"] = recovery_context_text  # type: ignore[index]
        if recovery_pending_work is not None:
            state["recovery_pending_work"] = recovery_pending_work  # type: ignore[index]
        if recovered_from_session_id is not None:
            state["recovered_from_session_id"] = recovered_from_session_id  # type: ignore[index]
        if recovery_point is not None:
            state["recovery_point"] = recovery_point  # type: ignore[index]
        if resume_state_snapshot:
            snapshot_state = dict(resume_state_snapshot)
            snapshot_state.pop("session_id", None)
            snapshot_state.pop("resume_after_sequence", None)
            state.update(snapshot_state)
        return state

    async def _prepare_application_state(
        self,
        application_id: str,
        resume_from_session_id: str | None = None,
    ) -> dict[str, Any]:  # type: ignore[override]
        """Hydrate the credit graph with values already present in the loan/document streams."""
        del resume_from_session_id

        if not hasattr(self.store, "load_stream"):
            return {}

        prepared: dict[str, Any] = {}
        loan_stream_id = f"loan-{application_id}"
        try:
            loan_events = await self.store.load_stream(loan_stream_id)
        except Exception:
            loan_events = []

        submitted_event = next(
            (event for event in loan_events if event.get("event_type") == "ApplicationSubmitted"),
            None,
        )
        if submitted_event is not None:
            payload = dict(submitted_event.get("payload") or {})
            applicant_id = str(payload.get("applicant_id") or "").strip()
            if applicant_id:
                prepared["applicant_id"] = applicant_id

            requested_amount = payload.get("requested_amount_usd")
            if requested_amount is None:
                requested_amount = payload.get("loan_amount")
            loan_amount = self._coerce_float(requested_amount)
            if loan_amount is not None and loan_amount > 0:
                prepared["loan_amount"] = loan_amount

            loan_term_months = payload.get("loan_term_months")
            if loan_term_months is not None:
                try:
                    prepared["loan_term_months"] = int(loan_term_months)
                except (TypeError, ValueError):
                    pass

            loan_purpose = payload.get("loan_purpose")
            if loan_purpose is not None:
                prepared["loan_purpose"] = str(loan_purpose)

        applicant_id = str(prepared.get("applicant_id") or "").strip()
        if applicant_id:
            annual_income = await self._resolve_annual_income(application_id, applicant_id)
            if annual_income is not None and annual_income > 0:
                prepared["annual_income"] = annual_income

        return prepared

    # ── NODE 1: VALIDATE INPUTS ───────────────────────────────────────────────
    async def _node_validate_inputs(self, state: CreditState) -> CreditState:
        t = time.time()
        applicant_id = str(state.get("applicant_id") or "").strip()
        if not applicant_id:
            raise CreditAnalysisError(field="applicant_id", reason="must be a non-empty string")

        try:
            loan_amount = float(state.get("loan_amount"))
        except Exception as exc:
            raise CreditAnalysisError(field="loan_amount", reason="must be > 0") from exc
        if loan_amount <= 0:
            raise CreditAnalysisError(field="loan_amount", reason="must be > 0")

        try:
            annual_income = float(state.get("annual_income"))
        except Exception as exc:
            raise CreditAnalysisError(field="annual_income", reason="must be > 0") from exc
        if annual_income <= 0:
            raise CreditAnalysisError(field="annual_income", reason="must be > 0")

        try:
            loan_term_months = int(state.get("loan_term_months"))
        except Exception as exc:
            raise CreditAnalysisError(field="loan_term_months", reason="must be between 12 and 360 inclusive") from exc
        if not 12 <= loan_term_months <= 360:
            raise CreditAnalysisError(field="loan_term_months", reason="must be between 12 and 360 inclusive")

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "validate_inputs",
            ["applicant_id", "loan_amount", "annual_income", "loan_term_months"],
            ["applicant_id", "loan_amount", "annual_income", "loan_term_months"],
            ms,
        )
        return {
            **state,
            "applicant_id": applicant_id,
            "loan_amount": loan_amount,
            "annual_income": annual_income,
            "loan_term_months": loan_term_months,
        }
    # ── NODE 2: OPEN CREDIT RECORD ────────────────────────────────────────────
    async def _node_open_credit_record(self, state: CreditState) -> CreditState:
        t = time.time()
        app_id = state["application_id"]
        credit_stream = f"credit-{app_id}"

        event = CreditRecordOpened(
            application_id=app_id,
            applicant_id=state["applicant_id"],
            opened_at=datetime.now(),
        ).to_store_dict()

        # New stream — expected_version = -1
        await self.store.append(credit_stream, [event], expected_version=-1)

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "open_credit_record",
            ["application_id", "applicant_id"],
            ["credit_record_opened"],
            ms,
        )
        return state

    # ── NODE 3: LOAD APPLICANT REGISTRY ──────────────────────────────────────
    async def _node_load_registry(self, state: CreditState) -> CreditState:
        t = time.time()
        applicant_id = state["applicant_id"]

        # Query Applicant Registry (read-only external database)
        if self.registry is None:
            raise RuntimeError("Applicant registry client is not configured")

        profile_obj = await self.registry.get_company(applicant_id)
        if profile_obj is None:
            raise ValueError(f"Applicant '{applicant_id}' not found in registry")

        financial_rows = await self.registry.get_financial_history(applicant_id, years=[2022, 2023, 2024])
        flag_rows = await self.registry.get_compliance_flags(applicant_id)
        loan_rows = await self.registry.get_loan_relationships(applicant_id)

        # Keep downstream state shape as plain dict/list for existing node logic.
        profile: dict = asdict(profile_obj)
        financials: list[dict] = [asdict(row) for row in financial_rows]
        flags: list[dict] = [asdict(row) for row in flag_rows]
        loans: list[dict] = [dict(row) for row in loan_rows]

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "query_applicant_registry",
            f"company_id={applicant_id} tables=[companies,financial_history,compliance_flags,loan_relationships]",
            f"Loaded profile, {len(financials)} fiscal years, {len(flags)} flags, {len(loans)} loans",
            ms,
        )

        # Record what was consumed
        has_defaults = any(l.get("default_occurred") for l in loans)
        traj = profile.get("trajectory", "UNKNOWN")
        event = HistoricalProfileConsumed(
            application_id=state["application_id"],
            session_id=self.session_id,
            fiscal_years_loaded=[f["fiscal_year"] for f in financials],
            has_prior_loans=bool(loans),
            has_defaults=has_defaults,
            revenue_trajectory=traj,
            data_hash=self._sha({"fins": financials, "flags": flags}),
            consumed_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(f"credit-{state['application_id']}", [event])

        await self._record_node_execution(
            "load_applicant_registry",
            ["applicant_id"],
            ["company_profile", "historical_financials", "compliance_flags", "loan_history"],
            ms,
        )
        return {
            **state,
            "company_profile":      profile,
            "historical_financials": financials,
            "compliance_flags":     flags,
            "loan_history":         loans,
        }

    # ── NODE 4: LOAD EXTRACTED FACTS ──────────────────────────────────────────
    async def _node_load_facts(self, state: CreditState) -> CreditState:
        t = time.time()
        app_id = state["application_id"]

        # Load ExtractionCompleted events from document package stream
        pkg_events = await self.store.load_stream(f"docpkg-{app_id}")
        extraction_events = [
            e for e in pkg_events
            if e["event_type"] == "ExtractionCompleted"
        ]

        # Merge facts from income statement and balance sheet extractions
        merged_facts: dict = {}
        doc_ids: list[str] = []
        quality_flags: list[str] = []

        for ev in extraction_events:
            payload = ev["payload"]
            doc_ids.append(payload.get("document_id", "unknown"))
            facts = payload.get("facts") or {}
            for k, v in facts.items():
                if v is not None and k not in merged_facts:
                    merged_facts[k] = v
            # Collect quality flags
            if facts.get("extraction_notes"):
                quality_flags.extend(facts["extraction_notes"])

        # Also check for quality assessment anomalies
        qa_events = [e for e in pkg_events if e["event_type"] == "QualityAssessmentCompleted"]
        for ev in qa_events:
            quality_flags.extend(ev["payload"].get("anomalies", []))
            quality_flags.extend([
                f"CRITICAL_MISSING:{f}"
                for f in ev["payload"].get("critical_missing_fields", [])
            ])

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "load_event_store_stream",
            f"stream_id=docpkg-{app_id} filter=ExtractionCompleted",
            f"Loaded {len(extraction_events)} extraction results, {len(quality_flags)} flags",
            ms,
        )

        # Record consumption
        event = ExtractedFactsConsumed(
            application_id=app_id,
            session_id=self.session_id,
            document_ids_consumed=doc_ids,
            facts_summary=f"revenue={merged_facts.get('total_revenue')}, net_income={merged_facts.get('net_income')}",
            quality_flags_present=bool(quality_flags),
            consumed_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(f"credit-{app_id}", [event])

        # Defer if facts are too incomplete
        critical = ["total_revenue", "net_income", "total_assets"]
        missing_critical = [k for k in critical if not merged_facts.get(k)]
        if len(missing_critical) >= 2:
            defer_event = CreditAnalysisDeferred(
                application_id=app_id, session_id=self.session_id,
                deferral_reason="Insufficient document extraction quality",
                quality_issues=[f"Missing critical field: {f}" for f in missing_critical],
                deferred_at=datetime.now(),
            ).to_store_dict()
            await self._append_with_retry(f"credit-{app_id}", [defer_event])
            raise ValueError(f"Credit analysis deferred: missing {missing_critical}")

        await self._record_node_execution(
            "load_extracted_facts",
            ["document_package_events"],
            ["extracted_facts", "quality_flags"],
            ms,
        )
        return {**state, "extracted_facts": merged_facts, "quality_flags": quality_flags,
                "document_ids_consumed": doc_ids}

    # ── NODE 5: ANALYZE CREDIT RISK (LLM) ─────────────────────────────────────
    async def _node_analyze(self, state: CreditState) -> CreditState:
        t = time.time()
        hist      = state.get("historical_financials") or []
        facts     = state.get("extracted_facts") or {}
        flags     = state.get("compliance_flags") or []
        loans     = state.get("loan_history") or []
        profile   = state.get("company_profile") or {}
        q_flags   = state.get("quality_flags") or []

        fins_table = "\n".join([
            f"FY{f['fiscal_year']}: Revenue=${f.get('total_revenue',0):,.0f}"
            f" EBITDA=${f.get('ebitda',0):,.0f}"
            f" Net=${f.get('net_income',0):,.0f}"
            f" D/E={f.get('debt_to_equity',0):.2f}x"
            f" D/EBITDA={f.get('debt_to_ebitda',0):.2f}x"
            for f in hist
        ]) or "No historical data in registry"

        SYSTEM = """You are a commercial credit analyst at Apex Financial Services.
Evaluate the loan application and produce a CreditDecision as a JSON object.

HARD POLICY RULES (enforce regardless of your reasoning):
1. Maximum loan-to-revenue ratio: 0.35  (cap recommended_limit_usd at annual_revenue * 0.35)
2. Minimum debt service coverage: 1.25x  (EBITDA / estimated annual payment)
3. Any prior loan default → risk_tier must be HIGH
4. Any ACTIVE compliance flag severity=HIGH → confidence must be ≤ 0.50

Respond ONLY with this JSON (no preamble):
{
  "risk_tier": "LOW" | "MEDIUM" | "HIGH",
  "recommended_limit_usd": <integer>,
  "confidence": <float 0.0–1.0>,
  "rationale": "<3–5 sentences, plain English, readable by a loan officer>",
  "key_concerns": ["<concern>"],
  "data_quality_caveats": ["<caveat for any field with low extraction confidence>"],
  "policy_overrides_applied": ["<rule ID if triggered>"]
}"""

        USER = f"""LOAN APPLICATION
Applicant: {profile.get('name','Unknown')} ({profile.get('industry','Unknown')}, {profile.get('legal_type','Unknown')})
Jurisdiction: {profile.get('jurisdiction','Unknown')}
Requested Amount: ${state.get('requested_amount_usd',0):,.0f}
Loan Purpose: {state.get('loan_purpose','Unknown')}

HISTORICAL FINANCIAL PROFILE (3 years — from bank registry):
{fins_table}

CURRENT YEAR FACTS (extracted from submitted documents):
{json.dumps({k:str(v) for k,v in facts.items() if v is not None}, indent=2)}

DOCUMENT QUALITY FLAGS:
{json.dumps(q_flags) if q_flags else 'None'}

COMPLIANCE FLAGS:
{json.dumps(flags) if flags else 'None'}

PRIOR LOAN HISTORY:
{json.dumps(loans) if loans else 'No prior loan history on record'}

Provide your analysis as JSON."""

        decision: dict
        ti = to = 0
        cost = 0.0
        try:
            content, ti, to, cost = await self._call_llm(SYSTEM, USER, max_tokens=1024)
            decision = self._parse_json(content)
        except Exception as exc:
            # Safe fallback — confidence < 0.60 forces REFER downstream
            decision = {
                "risk_tier": "MEDIUM",
                "recommended_limit_usd": int((state.get("requested_amount_usd") or 0) * 0.75),
                "confidence": 0.45,
                "rationale": f"Automated analysis failed ({exc!s:.80}). Human review required.",
                "key_concerns": ["Automated analysis error — human review required"],
                "data_quality_caveats": [],
                "policy_overrides_applied": ["ANALYSIS_FALLBACK"],
            }

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "analyze_credit_risk",
            ["historical_financials", "extracted_facts", "company_profile", "loan_request"],
            ["credit_decision"],
            ms, ti, to, cost,
        )
        return {**state, "credit_decision": decision}

    # ── NODE 6: APPLY POLICY CONSTRAINTS (deterministic) ─────────────────────
    async def _node_policy(self, state: CreditState) -> CreditState:
        t = time.time()
        d        = dict(state["credit_decision"])
        hist     = state.get("historical_financials") or []
        req      = state.get("requested_amount_usd") or 0
        flags    = state.get("compliance_flags") or []
        loans    = state.get("loan_history") or []
        viols:  list[str] = []

        # Policy 1: loan-to-revenue cap
        if hist:
            rev = hist[-1].get("total_revenue", 0)
            cap = int(rev * 0.35)
            if cap > 0 and d.get("recommended_limit_usd", 0) > cap:
                d["recommended_limit_usd"] = cap
                viols.append(f"POLICY_REV_CAP: limit capped at 35% of revenue (${cap:,.0f})")

        # Policy 2: prior default → HIGH
        if any(l.get("default_occurred") for l in loans):
            if d.get("risk_tier") != "HIGH":
                d["risk_tier"] = "HIGH"
                viols.append("POLICY_PRIOR_DEFAULT: risk_tier elevated to HIGH")

        # Policy 3: active HIGH flag → confidence cap
        if any(f.get("severity") == "HIGH" and f.get("is_active") for f in flags):
            if d.get("confidence", 0) > 0.50:
                d["confidence"] = 0.50
                viols.append("POLICY_COMPLIANCE_FLAG: confidence capped at 0.50")

        if viols:
            d["policy_overrides_applied"] = d.get("policy_overrides_applied", []) + viols

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "apply_policy_constraints",
            ["credit_decision", "historical_financials", "loan_history", "compliance_flags"],
            ["credit_decision"],
            ms,
        )
        return {**state, "credit_decision": d, "policy_violations": viols}

    # ── NODE 7: WRITE OUTPUT ──────────────────────────────────────────────────
    async def _node_write_output(self, state: CreditState) -> CreditState:
        t = time.time()
        app_id = state["application_id"]
        d      = state["credit_decision"]

        # Build and append CreditAnalysisCompleted
        credit_event = CreditAnalysisCompleted(
            application_id=app_id,
            session_id=self.session_id,
            decision=CreditDecision(
                risk_tier=RiskTier(d["risk_tier"]),
                recommended_limit_usd=Decimal(str(d["recommended_limit_usd"])),
                confidence=float(d["confidence"]),
                rationale=d.get("rationale", ""),
                key_concerns=d.get("key_concerns", []),
                data_quality_caveats=d.get("data_quality_caveats", []),
                policy_overrides_applied=d.get("policy_overrides_applied", []),
            ),
            model_version=self.model,
            model_deployment_id=f"dep-{uuid4().hex[:8]}",
            input_data_hash=self._sha(state),
            analysis_duration_ms=int((time.time() - self._t0) * 1000),
            completed_at=datetime.now(),
        ).to_store_dict()

        # OCC-safe write to credit stream
        positions = await self._append_with_retry(
            f"credit-{app_id}", [credit_event],
            causation_id=self.session_id,
        )

        # Trigger next agent: append FraudScreeningRequested to loan stream
        fraud_trigger = FraudScreeningRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            triggered_by_event_id=self.session_id,
        ).to_store_dict()
        await self._append_with_retry(f"loan-{app_id}", [fraud_trigger])

        events_written = [
            {"stream_id": f"credit-{app_id}", "event_type": "CreditAnalysisCompleted",
             "stream_position": positions[0] if positions else -1},
            {"stream_id": f"loan-{app_id}", "event_type": "FraudScreeningRequested",
             "stream_position": -1},
        ]
        await self._record_output_written(
            events_written,
            f"Credit: {d['risk_tier']} risk, ${d['recommended_limit_usd']:,.0f} limit, "
            f"{d['confidence']:.0%} confidence. Fraud screening triggered.",
        )

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "write_output", ["credit_decision"], ["events_written"], ms
        )
        return {**state, "output_events": events_written, "next_agent": "fraud_detection"}

    # ------------------------------------------------------------------
    # Upgraded deterministic credit-scoring phases.
    # These override the older LangGraph reference implementation above.
    # ------------------------------------------------------------------

    async def _node_validate_inputs(self, state: CreditState) -> CreditState:  # type: ignore[override]
        t = time.time()
        applicant_id = str(state.get("applicant_id") or "").strip()
        if not applicant_id:
            raise CreditAnalysisError(field="applicant_id", reason="must be a non-empty string")

        try:
            loan_amount = float(state.get("loan_amount"))
        except Exception as exc:
            raise CreditAnalysisError(field="loan_amount", reason="must be > 0") from exc
        if loan_amount <= 0:
            raise CreditAnalysisError(field="loan_amount", reason="must be > 0")

        try:
            annual_income = float(state.get("annual_income"))
        except Exception as exc:
            raise CreditAnalysisError(field="annual_income", reason="must be > 0") from exc
        if annual_income <= 0:
            raise CreditAnalysisError(field="annual_income", reason="must be > 0")

        try:
            loan_term_months = int(state.get("loan_term_months"))
        except Exception as exc:
            raise CreditAnalysisError(field="loan_term_months", reason="must be between 12 and 360 inclusive") from exc
        if not 12 <= loan_term_months <= 360:
            raise CreditAnalysisError(field="loan_term_months", reason="must be between 12 and 360 inclusive")

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "validate_inputs",
            ["applicant_id", "loan_amount", "annual_income", "loan_term_months"],
            ["applicant_id", "loan_amount", "annual_income", "loan_term_months"],
            ms,
        )
        return {
            **state,
            "applicant_id": applicant_id,
            "loan_amount": loan_amount,
            "annual_income": annual_income,
            "loan_term_months": loan_term_months,
        }

    async def _node_load_registry(self, state: CreditState) -> CreditState:  # type: ignore[override]
        t = time.time()
        applicant_id = str(state.get("applicant_id") or "").strip()
        if not applicant_id:
            raise CreditAnalysisError(field="applicant_id", reason="must be a non-empty string")

        if not hasattr(self.store, "load_all"):
            if self.registry is None:
                raise RuntimeError("Applicant registry client is not configured")

            profile_obj = await self.registry.get_company(applicant_id)
            if profile_obj is None:
                raise ValueError(f"Applicant '{applicant_id}' not found in registry")

            financial_rows = await self.registry.get_financial_history(applicant_id, years=[2022, 2023, 2024])
            flag_rows = await self.registry.get_compliance_flags(applicant_id)
            loan_rows = await self.registry.get_loan_relationships(applicant_id)

            profile: dict = asdict(profile_obj)
            financials: list[dict] = [asdict(row) for row in financial_rows]
            flags: list[dict] = [asdict(row) for row in flag_rows]
            loans: list[dict] = [dict(row) for row in loan_rows]

            ms = int((time.time() - t) * 1000)
            await self._record_tool_call(
                "query_applicant_registry",
                f"company_id={applicant_id} tables=[companies,financial_history,compliance_flags,loan_relationships]",
                f"Loaded profile, {len(financials)} fiscal years, {len(flags)} flags, {len(loans)} loans",
                ms,
            )

            has_defaults = any(l.get("default_occurred") for l in loans)
            traj = profile.get("trajectory", "UNKNOWN")
            event = HistoricalProfileConsumed(
                application_id=state["application_id"],
                session_id=self.session_id,
                fiscal_years_loaded=[f["fiscal_year"] for f in financials],
                has_prior_loans=bool(loans),
                has_defaults=has_defaults,
                revenue_trajectory=traj,
                data_hash=self._sha({"fins": financials, "flags": flags}),
                consumed_at=datetime.now(),
            ).to_store_dict()
            await self._append_with_retry(f"credit-{state['application_id']}", [event])

            await self._record_node_execution(
                "load_applicant_registry",
                ["applicant_id"],
                ["company_profile", "historical_financials", "compliance_flags", "loan_history"],
                ms,
            )
            return {
                **state,
                "company_profile": profile,
                "historical_financials": financials,
                "compliance_flags": flags,
                "loan_history": loans,
            }

        prior_defaults = 0
        prior_loans = 0
        oldest_timestamp: datetime | None = None

        async for event in self.store.load_all(from_position=0):
            payload = dict(event.get("payload") or {})
            if str(payload.get("applicant_id") or "").strip() != applicant_id:
                continue

            event_type = str(event.get("event_type") or "")
            if payload.get("default_occurred") or event_type in {"LoanDefaulted", "LoanDefaultRecorded"}:
                prior_defaults += 1
            if event_type in {"ApplicationSubmitted", "LoanApproved", "LoanOriginated", "CreditAnalysisCompleted", "LOAN_APPROVED"} or any(
                payload.get(key) is not None for key in ("loan_amount", "requested_amount_usd", "approved_amount_usd")
            ):
                prior_loans += 1

            timestamp = self._extract_event_timestamp(event)
            if timestamp is not None and (oldest_timestamp is None or timestamp < oldest_timestamp):
                oldest_timestamp = timestamp

        history = {
            "prior_defaults": int(prior_defaults),
            "prior_loans": int(prior_loans),
            "account_age_months": int(self._calculate_account_age_months(oldest_timestamp)),
        }

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "load_applicant_registry",
            ["applicant_id"],
            ["prior_defaults", "prior_loans", "account_age_months"],
            ms,
        )
        return {**state, **history, "applicant_registry": history}

    async def _node_load_facts(self, state: CreditState) -> CreditState:  # type: ignore[override]
        t = time.time()
        loan_amount = float(state["loan_amount"])
        annual_income = float(state["annual_income"])
        loan_term_months = int(state["loan_term_months"])
        monthly_debt = float(state.get("monthly_debt") or 0.0)
        annual_rate = float(state.get("annual_rate") or 0.06)
        if annual_rate > 1:
            annual_rate = annual_rate / 100.0

        monthly_income = annual_income / 12.0
        dti_ratio = (monthly_debt / monthly_income) if monthly_income else 0.0
        loan_to_income_ratio = loan_amount / annual_income
        monthly_rate = annual_rate / 12.0
        if monthly_rate == 0:
            monthly_payment_estimate = loan_amount / loan_term_months
        else:
            monthly_payment_estimate = loan_amount * monthly_rate / (1 - (1 + monthly_rate) ** (-loan_term_months))

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "load_extracted_facts",
            ["loan_amount", "annual_income", "loan_term_months", "monthly_debt", "annual_rate"],
            ["dti_ratio", "loan_to_income_ratio", "monthly_payment_estimate"],
            ms,
        )
        return {
            **state,
            "monthly_debt": monthly_debt,
            "annual_rate": annual_rate,
            "dti_ratio": float(dti_ratio),
            "loan_to_income_ratio": float(loan_to_income_ratio),
            "monthly_payment_estimate": float(monthly_payment_estimate),
        }

    async def _node_analyze(self, state: CreditState) -> CreditState:  # type: ignore[override]
        t = time.time()
        dti_ratio = float(state.get("dti_ratio") or 0.0)
        loan_to_income_ratio = float(state.get("loan_to_income_ratio") or 0.0)
        prior_defaults = int(state.get("prior_defaults") or 0)
        account_age_months = int(state.get("account_age_months") or 0)

        factor_scores = {
            "dti": max(0.0, 100.0 - (dti_ratio * 200.0)),
            "loan_to_income": max(0.0, 100.0 - (loan_to_income_ratio * 20.0)),
            "prior_defaults": max(0.0, 100.0 - (prior_defaults * 40.0)),
            "account_age": min(100.0, account_age_months * 2.0),
        }
        credit_score = (
            (factor_scores["dti"] * 0.35)
            + (factor_scores["loan_to_income"] * 0.25)
            + (factor_scores["prior_defaults"] * 0.25)
            + (factor_scores["account_age"] * 0.15)
        )

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "analyze_credit_risk",
            ["dti_ratio", "loan_to_income_ratio", "prior_defaults", "account_age_months"],
            ["credit_score", "factor_scores"],
            ms,
        )
        return {
            **state,
            "factor_scores": factor_scores,
            "credit_score": float(max(0.0, min(100.0, credit_score))),
        }

    async def _node_policy(self, state: CreditState) -> CreditState:  # type: ignore[override]
        t = time.time()
        dti_ratio = float(state.get("dti_ratio") or 0.0)
        loan_to_income_ratio = float(state.get("loan_to_income_ratio") or 0.0)
        prior_defaults = int(state.get("prior_defaults") or 0)
        credit_score = float(state.get("credit_score") or 0.0)

        hard_gate: str | None = None
        if dti_ratio > 0.55:
            policy_outcome = "REJECT"
            hard_gate = "dti_hard_limit"
        elif loan_to_income_ratio > 10:
            policy_outcome = "REJECT"
            hard_gate = "concentration_risk"
        elif prior_defaults >= 2:
            policy_outcome = "REJECT"
            hard_gate = "credit_policy"
        elif credit_score >= 70:
            policy_outcome = "APPROVE"
        elif credit_score >= 45:
            policy_outcome = "MANUAL_REVIEW"
        else:
            policy_outcome = "REJECT"

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "apply_policy_constraints",
            ["dti_ratio", "loan_to_income_ratio", "prior_defaults", "credit_score"],
            ["policy_outcome", "hard_gate"],
            ms,
        )
        return {**state, "policy_outcome": policy_outcome, "hard_gate": hard_gate}

    async def _node_explain_decision(self, state: CreditState) -> CreditState:
        t = time.time()
        credit_score = float(state.get("credit_score") or 0.0)
        policy_outcome = str(state.get("policy_outcome") or "REJECT").upper()
        factor_scores = dict(state.get("factor_scores") or {})
        hard_gate = state.get("hard_gate")
        dti_ratio = float(state.get("dti_ratio") or 0.0)
        loan_to_income_ratio = float(state.get("loan_to_income_ratio") or 0.0)
        prior_defaults = int(state.get("prior_defaults") or 0)

        explanation = "Automated scorecard decision — manual explanation unavailable"
        key_factors: list[str] = []
        confidence = "low"

        if _ollama is not None:
            try:
                result = _ollama.ask(
                    system_prompt=(
                        "You are a credit analyst writing a regulatory-grade decision explanation. "
                        "Given this scorecard output, write a 3-4 sentence explanation of why this loan "
                        "was approved, rejected, or flagged for review. Be specific about which factors "
                        "drove the outcome. Respond ONLY in valid JSON with no extra text: "
                        "{\"explanation\": \"string\", \"key_factors\": [\"string\"], "
                        "\"confidence\": \"low\"|\"medium\"|\"high\"}"
                    ),
                    user_message=json.dumps(
                        {
                            "credit_score": credit_score,
                            "policy_outcome": policy_outcome,
                            "factor_scores": factor_scores,
                            "hard_gate_triggered": hard_gate or None,
                            "dti_ratio": dti_ratio,
                            "loan_to_income_ratio": loan_to_income_ratio,
                            "prior_defaults": prior_defaults,
                        }
                    ),
                )
                if result.get("fallback") is True or result.get("parse_error") is True:
                    explanation = "Automated scorecard decision — manual explanation unavailable"
                    key_factors = []
                    confidence = "low"
                else:
                    explanation = str(result["explanation"])
                    key_factors = [str(item) for item in list(result.get("key_factors") or [])]
                    confidence = str(result["confidence"])
            except Exception:
                explanation = "Automated scorecard decision — manual explanation unavailable"
                key_factors = []
                confidence = "low"

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "explain_decision",
            ["credit_score", "policy_outcome", "factor_scores"],
            ["explanation", "key_factors", "confidence"],
            ms,
        )
        return {**state, "explanation": explanation, "key_factors": key_factors, "confidence": confidence}

    async def _node_write_output(self, state: CreditState) -> CreditState:  # type: ignore[override]
        t = time.time()
        app_id = state["application_id"]
        applicant_id = str(state.get("applicant_id") or "").strip()
        credit_score = float(state.get("credit_score") or 0.0)
        policy_outcome = str(state.get("policy_outcome") or "REJECT").upper()
        hard_gate = state.get("hard_gate")
        factor_scores = dict(state.get("factor_scores") or {})
        explanation = str(state.get("explanation") or "Automated scorecard decision — manual explanation unavailable")
        key_factors = list(state.get("key_factors") or [])
        confidence = str(state.get("confidence") or "low")

        credit_event = {
            "event_type": "CREDIT_ANALYSIS_COMPLETED",
            "payload": {
                "applicant_id": applicant_id,
                "credit_score": float(credit_score),
                "policy_outcome": policy_outcome,
                "hard_gate": hard_gate,
                "factor_scores": {
                    "dti": float(factor_scores.get("dti", 0.0)),
                    "loan_to_income": float(factor_scores.get("loan_to_income", 0.0)),
                    "prior_defaults": float(factor_scores.get("prior_defaults", 0.0)),
                    "account_age": float(factor_scores.get("account_age", 0.0)),
                },
                "explanation": explanation,
                "key_factors": key_factors,
                "confidence": confidence,
            },
        }

        decision_event_type = {
            "APPROVE": "LOAN_APPROVED",
            "REJECT": "LOAN_REJECTED",
            "MANUAL_REVIEW": "MANUAL_REVIEW_REQUIRED",
        }[policy_outcome]
        decision_event = {
            "event_type": decision_event_type,
            "payload": {
                "applicant_id": applicant_id,
                "policy_outcome": policy_outcome,
                "hard_gate": hard_gate,
                "credit_score": float(credit_score),
            },
        }

        credit_positions = await self._append_with_retry(f"credit-{app_id}", [credit_event])
        loan_positions = await self._append_with_retry(f"loan-{app_id}", [decision_event])

        events_written = [
            {
                "stream_id": f"credit-{app_id}",
                "event_type": "CREDIT_ANALYSIS_COMPLETED",
                "stream_position": credit_positions[0] if credit_positions else -1,
            },
            {
                "stream_id": f"loan-{app_id}",
                "event_type": decision_event_type,
                "stream_position": loan_positions[0] if loan_positions else -1,
            },
        ]

        await self._record_output_written(
            events_written,
            f"Credit score {credit_score:.1f} -> {policy_outcome}.",
        )

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "write_output", ["credit_score", "policy_outcome"], ["events_written"], ms
        )
        return {**state, "output_events": events_written, "next_agent": None}

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def _resolve_annual_income(self, application_id: str, applicant_id: str) -> float | None:
        doc_stream_id = f"docpkg-{application_id}"
        try:
            doc_events = await self.store.load_stream(doc_stream_id)
        except Exception:
            doc_events = []

        for event in doc_events:
            if event.get("event_type") != "ExtractionCompleted":
                continue
            facts = dict((event.get("payload") or {}).get("facts") or {})
            annual_income = self._coerce_float(facts.get("annual_income"))
            if annual_income is None:
                annual_income = self._coerce_float(facts.get("total_revenue"))
            if annual_income is not None and annual_income > 0:
                return annual_income

        if self.registry is None:
            return None

        try:
            financial_rows = await self.registry.get_financial_history(applicant_id, years=[2022, 2023, 2024])
        except Exception:
            return None

        if not financial_rows:
            return None
        latest = financial_rows[-1]
        annual_income = self._coerce_float(getattr(latest, "total_revenue", None))
        if annual_income is not None and annual_income > 0:
            return annual_income
        return None

    @staticmethod
    def _extract_event_timestamp(event: dict[str, Any]) -> datetime | None:
        payload = dict(event.get("payload") or {})
        for key in (
            "submitted_at",
            "opened_at",
            "approved_at",
            "originated_at",
            "completed_at",
            "created_at",
            "recorded_at",
            "event_time",
        ):
            value = payload.get(key) or event.get(key)
            if isinstance(value, datetime):
                if value.tzinfo is not None:
                    return value.astimezone(timezone.utc).replace(tzinfo=None)
                return value
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    return parsed
                except ValueError:
                    continue
        return None

    @staticmethod
    def _calculate_account_age_months(oldest_timestamp: datetime | None) -> int:
        if oldest_timestamp is None:
            return 0
        age_days = max(0, (datetime.now(timezone.utc).replace(tzinfo=None) - oldest_timestamp).days)
        return int(age_days // 30)


