"""
ledger/agents/stub_agents.py
============================
STUB IMPLEMENTATIONS for DocumentProcessingAgent, FraudDetectionAgent,
ComplianceAgent, and DecisionOrchestratorAgent.

Each stub contains:
  - The State TypedDict
  - build_graph() with the correct node sequence
  - All node method stubs with TODO instructions
  - The exact events each node must write
  - WHEN IT WORKS criteria for each agent

Pattern: follow CreditAnalysisAgent exactly. Same build_graph() structure,
same _record_node_execution() calls, same _append_with_retry() for domain writes.
"""
from __future__ import annotations
import time, json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, END

from ledger.agents.base_agent import BaseApexAgent
from ledger.domain.aggregates.compliance_record import ComplianceRecordAggregate
from ledger.domain.compliance_rules import REGULATIONS, RULE_SET_VERSION
from ledger.schema.events import (
    ApplicationApproved,
    ApplicationDeclined,
    ComplianceCheckCompleted,
    ComplianceCheckInitiated,
    ComplianceCheckRequested,
    ComplianceRuleFailed,
    ComplianceRuleNoted,
    ComplianceRulePassed,
    DecisionGenerated,
    DecisionRequested,
    FraudAnomalyDetected,
    FraudScreeningCompleted,
    FraudScreeningInitiated,
    HumanReviewRequested,
    HumanReviewCompleted,
    ComplianceVerdict,
)


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        try:
            return asdict(value)
        except TypeError:
            return dict(value.__dict__)
    return dict(value)


# ─── DOCUMENT PROCESSING AGENT ───────────────────────────────────────────────

class DocProcState(TypedDict):
    application_id: str
    session_id: str
    document_ids: list[str] | None
    document_paths: list[str] | None
    extraction_results: list[dict] | None  # one per document
    quality_assessment: dict | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


class DocumentProcessingAgent(BaseApexAgent):
    """
    Wraps the Week 3 Document Intelligence pipeline.
    Processes uploaded PDFs and appends extraction events.

    LangGraph nodes:
        validate_inputs → validate_document_formats → extract_income_statement →
        extract_balance_sheet → assess_quality → write_output

    Output events:
        docpkg-{id}:  DocumentFormatValidated (x per doc), ExtractionStarted (x per doc),
                      ExtractionCompleted (x per doc), QualityAssessmentCompleted,
                      PackageReadyForAnalysis
        loan-{id}:    CreditAnalysisRequested

    WEEK 3 INTEGRATION:
        In _node_extract_document(), call your Week 3 pipeline:
            from document_refinery.pipeline import extract_financial_facts
            facts = await extract_financial_facts(file_path, document_type)
        Wrap in try/except — append ExtractionFailed if pipeline raises.

    LLM in _node_assess_quality():
        System: "You are a financial document quality analyst.
                 Check internal consistency. Do NOT make credit decisions.
                 Return DocumentQualityAssessment JSON."
        The LLM checks: Assets = Liabilities + Equity, margins plausible, etc.

    WHEN THIS WORKS:
        pytest tests/phase2/test_document_agent.py  # all pass
        python scripts/run_pipeline.py --app APEX-0001 --phase document
          → ExtractionCompleted event in docpkg stream with non-null total_revenue
          → QualityAssessmentCompleted event present
          → PackageReadyForAnalysis event present
          → CreditAnalysisRequested on loan stream
    """

    def build_graph(self):
        g = StateGraph(DocProcState)
        g.add_node("validate_inputs",            self._node_validate_inputs)
        g.add_node("validate_document_formats",  self._node_validate_formats)
        g.add_node("extract_income_statement",   self._node_extract_is)
        g.add_node("extract_balance_sheet",      self._node_extract_bs)
        g.add_node("assess_quality",             self._node_assess_quality)
        g.add_node("write_output",               self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs",           "validate_document_formats")
        g.add_edge("validate_document_formats", "extract_income_statement")
        g.add_edge("extract_income_statement",  "extract_balance_sheet")
        g.add_edge("extract_balance_sheet",     "assess_quality")
        g.add_edge("assess_quality",            "write_output")
        g.add_edge("write_output",              END)
        return g.compile()

    def _initial_state(self, application_id: str) -> DocProcState:
        return DocProcState(
            application_id=application_id, session_id=self.session_id,
            document_ids=None, document_paths=None,
            extraction_results=None, quality_assessment=None,
            errors=[], output_events=[], next_agent=None,
        )

    async def _node_validate_inputs(self, state):
        t = time.time()
        # TODO:
        # 1. Load DocumentUploaded events from "loan-{app_id}" stream
        # 2. Extract document_ids and file_paths for each uploaded document
        # 3. Verify at least APPLICATION_PROPOSAL + INCOME_STATEMENT + BALANCE_SHEET uploaded
        # 4. If any required doc missing: await self._record_input_failed([...], [...]) then raise
        # 5. await self._record_input_validated(["application_id","document_ids","file_paths"], ms)
        raise NotImplementedError("Implement _node_validate_inputs")

    async def _node_validate_formats(self, state):
        t = time.time()
        # TODO:
        # For each document:
        #   1. Check file exists on disk, is not corrupt
        #   2. Detect actual format (PyPDF2, python-magic, etc.)
        #   3. Append DocumentFormatValidated(package_id, doc_id, page_count, detected_format)
        #      to "docpkg-{app_id}" stream
        #   4. If corrupt: append DocumentFormatRejected and remove from processing list
        # 5. await self._record_node_execution("validate_document_formats", ...)
        raise NotImplementedError("Implement _node_validate_formats")

    async def _node_extract_is(self, state):
        t = time.time()
        # TODO:
        # 1. Find income statement document from state["document_paths"]
        # 2. Append ExtractionStarted(package_id, doc_id, pipeline_version, "mineru-1.0")
        #    to "docpkg-{app_id}" stream
        # 3. Call Week 3 pipeline:
        #    from document_refinery.pipeline import extract_financial_facts
        #    facts = await extract_financial_facts(file_path, "income_statement")
        # 4. On success: append ExtractionCompleted(facts=FinancialFacts(**facts), ...)
        # 5. On failure: append ExtractionFailed(error_type, error_message, partial_facts)
        # 6. await self._record_tool_call("week3_extraction_pipeline", ..., ms)
        # 7. await self._record_node_execution("extract_income_statement", ...)
        raise NotImplementedError("Implement _node_extract_is")

    async def _node_extract_bs(self, state):
        t = time.time()
        # TODO: Same pattern as _node_extract_is but for balance sheet
        # Key difference: ExtractionCompleted for balance sheet should populate
        # total_assets, total_liabilities, total_equity, current_assets, etc.
        # The QualityAssessmentCompleted LLM will check Assets = Liabilities + Equity
        raise NotImplementedError("Implement _node_extract_bs")

    async def _node_assess_quality(self, state):
        t = time.time()
        # TODO:
        # 1. Merge extraction results from IS + BS into a combined FinancialFacts
        # 2. Build LLM prompt asking for quality assessment (consistency check)
        # 3. content, ti, to, cost = await self._call_llm(SYSTEM, USER, 512)
        # 4. Parse DocumentQualityAssessment from JSON response
        # 5. Append QualityAssessmentCompleted to "docpkg-{app_id}" stream
        # 6. If critical_missing_fields: add to state["quality_flags"]
        # 7. await self._record_node_execution("assess_quality", ..., ms, ti, to, cost)
        raise NotImplementedError("Implement _node_assess_quality")

    async def _node_write_output(self, state):
        t = time.time()
        # TODO:
        # 1. Append PackageReadyForAnalysis to "docpkg-{app_id}" stream
        # 2. Append CreditAnalysisRequested to "loan-{app_id}" stream
        # 3. await self._record_output_written([...], summary)
        # 4. await self._record_node_execution("write_output", ...)
        # 5. return {**state, "next_agent": "credit_analysis"}
        raise NotImplementedError("Implement _node_write_output")


# ─── FRAUD DETECTION AGENT ───────────────────────────────────────────────────

class FraudState(TypedDict):
    application_id: str
    session_id: str
    applicant_id: str | None
    extracted_facts: dict | None
    registry_profile: dict | None
    historical_financials: list[dict] | None
    loan_history: list[dict] | None
    document_events: list[dict] | None
    fraud_signals: list[dict] | None
    fraud_score: float | None
    risk_level: str | None
    recommendation: str | None
    anomalies: list[dict] | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


class FraudDetectionAgent(BaseApexAgent):
    """
    Cross-references extracted document facts against historical registry data.
    Detects anomalous discrepancies that suggest fraud or document manipulation.

    LangGraph nodes:
        validate_inputs → load_document_facts → cross_reference_registry →
        analyze_fraud_patterns → write_output

    Output events:
        fraud-{id}: FraudScreeningInitiated, FraudAnomalyDetected (0..N),
                    FraudScreeningCompleted
        loan-{id}:  ComplianceCheckRequested

    KEY SCORING LOGIC:
        fraud_score = base(0.05)
            + revenue_discrepancy_factor   (doc revenue vs prior year registry)
            + submission_pattern_factor    (channel, timing, IP region)
            + balance_sheet_consistency    (assets = liabilities + equity within tolerance)

        revenue_discrepancy_factor:
            gap = abs(doc_revenue - registry_prior_revenue) / registry_prior_revenue
            if gap > 0.40 and trajectory not in (GROWTH, RECOVERING): += 0.25

        FraudAnomalyDetected is appended for each anomaly where severity >= MEDIUM.
        fraud_score > 0.60 → recommendation = "DECLINE"
        fraud_score 0.30..0.60 → "FLAG_FOR_REVIEW"
        fraud_score < 0.30 → "PROCEED"

    LLM in _node_analyze():
        System: "You are a financial fraud analyst.
                 Given the cross-reference results, identify specific named anomalies.
                 For each anomaly: type, severity, evidence, affected_fields.
                 Compute a final fraud_score 0-1. Return FraudAssessment JSON."

    WHEN THIS WORKS:
        pytest tests/phase2/test_fraud_agent.py
          → FraudScreeningCompleted event in fraud stream
          → fraud_score between 0.0 and 1.0
          → ComplianceCheckRequested on loan stream
          → NARR-03 (crash recovery) test passes
    """

    def build_graph(self):
        g = StateGraph(FraudState)
        g.add_node("validate_inputs",         self._node_validate_inputs)
        g.add_node("load_document_facts",     self._node_load_facts)
        g.add_node("cross_reference_registry",self._node_cross_reference)
        g.add_node("analyze_fraud_patterns",  self._node_analyze)
        g.add_node("write_output",            self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs",          "load_document_facts")
        g.add_edge("load_document_facts",      "cross_reference_registry")
        g.add_edge("cross_reference_registry", "analyze_fraud_patterns")
        g.add_edge("analyze_fraud_patterns",   "write_output")
        g.add_edge("write_output",             END)
        return g.compile()

    def _initial_state(self, application_id: str) -> FraudState:
        return FraudState(
            application_id=application_id, session_id=self.session_id, applicant_id=None,
            extracted_facts=None, registry_profile=None, historical_financials=None,
            loan_history=None, document_events=None, fraud_signals=None, fraud_score=None,
            risk_level=None, recommendation=None, anomalies=None,
            errors=[], output_events=[], next_agent=None,
        )

    async def _node_validate_inputs(self, state):
        t = time.time()
        app_id = state["application_id"]
        loan_events = await self.store.load_stream(f"loan-{app_id}")
        if not loan_events:
            raise ValueError(f"No events found for loan-{app_id}")

        applicant_id = None
        fraud_requested = False
        for ev in loan_events:
            if ev["event_type"] == "ApplicationSubmitted":
                applicant_id = ev["payload"].get("applicant_id")
            elif ev["event_type"] == "FraudScreeningRequested":
                fraud_requested = True

        if not fraud_requested:
            raise ValueError("FraudScreeningRequested event not found in loan stream.")
        if not applicant_id:
            raise ValueError("Could not determine applicant_id from loan stream.")

        fraud_stream = f"fraud-{app_id}"
        initiated = FraudScreeningInitiated(
            application_id=app_id,
            session_id=state["session_id"],
            screening_model_version=self.model,
            initiated_at=datetime.utcnow(),
        ).to_store_dict()
        await self._append_with_retry(fraud_stream, [initiated])

        await self._record_node_execution(
            "validate_inputs",
            ["application_id"],
            ["applicant_id", "fraud_screening_initiated"],
            int((time.time() - t) * 1000),
        )
        return {**state, "applicant_id": applicant_id}

    async def _node_load_facts(self, state):
        t = time.time()
        app_id = state["application_id"]
        pkg_events = await self.store.load_stream(f"docpkg-{app_id}")
        extraction_events = [e for e in pkg_events if e["event_type"] == "ExtractionCompleted"]
        if not extraction_events:
            raise ValueError(f"No ExtractionCompleted events found for docpkg-{app_id}")

        def _to_dict(value):
            if value is None:
                return {}
            if isinstance(value, dict):
                return dict(value)
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if hasattr(value, "__dict__"):
                return dict(value.__dict__)
            return dict(value)

        merged_facts: dict = {}
        doc_ids: list[str] = []
        quality_flags: list[str] = []
        for ev in extraction_events:
            payload = ev.get("payload") or {}
            doc_ids.append(str(payload.get("document_id") or "unknown"))
            facts = _to_dict(payload.get("facts") or payload.get("financial_facts") or {})
            for key, value in facts.items():
                if value is not None and key not in merged_facts:
                    merged_facts[key] = value

        for ev in pkg_events:
            if ev["event_type"] == "QualityAssessmentCompleted":
                payload = ev.get("payload") or {}
                quality_flags.extend([str(v) for v in payload.get("anomalies", []) if v])
                quality_flags.extend([f"CRITICAL_MISSING:{f}" for f in payload.get("critical_missing_fields", []) if f])

        await self._record_tool_call(
            "load_event_store_stream",
            f"stream_id=docpkg-{app_id} filter=ExtractionCompleted",
            f"Loaded {len(extraction_events)} extraction results",
            int((time.time() - t) * 1000),
        )

        return {
            **state,
            "extracted_facts": merged_facts,
            "document_events": extraction_events,
            "fraud_signals": quality_flags,
        }

    async def _node_cross_reference(self, state):
        t = time.time()
        applicant_id = state.get("applicant_id")
        if not applicant_id:
            raise ValueError("applicant_id not in state")
        if self.registry is None:
            raise RuntimeError("Applicant registry client is not configured")

        company = await self.registry.get_company(applicant_id)
        if company is None:
            raise ValueError(f"Applicant '{applicant_id}' not found in registry")

        history = await self.registry.get_financial_history(applicant_id, years=[2022, 2023, 2024])
        loan_history = []
        if hasattr(self.registry, "get_loan_relationships"):
            try:
                loan_history = await self.registry.get_loan_relationships(applicant_id)
            except Exception:
                loan_history = []

        def _to_dict(value):
            if isinstance(value, dict):
                return dict(value)
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if hasattr(value, "__dict__"):
                return dict(value.__dict__)
            return dict(value)

        company_profile = _to_dict(company)
        historical_financials = [_to_dict(row) for row in history]
        if loan_history:
            loan_history = [_to_dict(row) for row in loan_history]

        await self._record_tool_call(
            "query_applicant_registry",
            f"company_id={applicant_id} tables=[companies,financial_history,loan_relationships]",
            f"Loaded profile, {len(historical_financials)} fiscal years, {len(loan_history)} loans",
            int((time.time() - t) * 1000),
        )

        return {
            **state,
            "registry_profile": company_profile,
            "historical_financials": historical_financials,
            "loan_history": loan_history,
        }

    async def _node_analyze(self, state):
        t = time.time()
        facts = state.get("extracted_facts") or {}
        profile = state.get("registry_profile") or {}
        history = state.get("historical_financials") or []
        loans = state.get("loan_history") or []
        signals = list(state.get("fraud_signals") or [])
        anomalies: list[dict] = []
        score = 0.05

        def _as_float(value):
            if value is None:
                return None
            if isinstance(value, (int, float)):
                return float(value)
            try:
                return float(value)
            except Exception:
                return None

        def _add_anomaly(anomaly_type, description, severity, evidence, affected_fields):
            anomalies.append({
                "anomaly_type": anomaly_type,
                "description": description,
                "severity": severity,
                "evidence": evidence,
                "affected_fields": affected_fields,
            })

        current_revenue = _as_float(facts.get("total_revenue"))
        if history and current_revenue is not None:
            prior = _as_float(history[-1].get("total_revenue"))
            if prior and prior > 0:
                gap = abs(current_revenue - prior) / prior
                trajectory = str(profile.get("trajectory") or "").upper()
                if gap > 0.40 and trajectory not in {"GROWTH", "RECOVERING"}:
                    severity = "HIGH" if gap > 0.75 else "MEDIUM"
                    score += 0.25 if severity == "MEDIUM" else 0.35
                    _add_anomaly(
                        "revenue_discrepancy",
                        "Documented revenue diverges materially from registry history.",
                        severity,
                        f"Current revenue {current_revenue:,.0f} vs prior year {prior:,.0f} ({gap:.0%} gap).",
                        ["total_revenue", "trajectory"],
                    )

        assets = _as_float(facts.get("total_assets"))
        liabilities = _as_float(facts.get("total_liabilities"))
        equity = _as_float(facts.get("total_equity"))
        if assets is not None and liabilities is not None and equity is not None:
            expected = liabilities + equity
            denom = max(abs(expected), 1.0)
            discrepancy = abs(assets - expected) / denom
            if discrepancy > 0.05:
                severity = "HIGH" if discrepancy > 0.15 else "MEDIUM"
                score += 0.2 if severity == "MEDIUM" else 0.3
                _add_anomaly(
                    "balance_sheet_inconsistency",
                    "Balance sheet totals do not reconcile within tolerance.",
                    severity,
                    f"Assets {assets:,.0f} vs liabilities+equity {expected:,.0f} ({discrepancy:.0%} gap).",
                    ["total_assets", "total_liabilities", "total_equity"],
                )
        elif assets is None or liabilities is None or equity is None:
            missing = [name for name, value in (
                ("total_assets", assets),
                ("total_liabilities", liabilities),
                ("total_equity", equity),
            ) if value is None]
            if missing:
                score += 0.1
                signals.append(f"missing_balance_fields:{','.join(missing)}")

        submission_channel = str(profile.get("submission_channel") or "").lower()
        ip_region = str(profile.get("ip_region") or "")
        if submission_channel in {"agent", "branch"}:
            score += 0.1
            _add_anomaly(
                "unusual_submission_pattern",
                "Submission channel is less typical for this risk profile.",
                "MEDIUM",
                f"Submission channel '{submission_channel}' is not typical for automated self-serve screening.",
                ["submission_channel"],
            )
        if ip_region and not ip_region.startswith("US"):
            score += 0.15
            _add_anomaly(
                "unusual_submission_pattern",
                "Submission originated from an unusual IP region.",
                "MEDIUM",
                f"IP region '{ip_region}' is outside the US region set.",
                ["ip_region"],
            )

        if any(l.get("default_occurred") for l in loans):
            score += 0.15
            _add_anomaly(
                "identity_mismatch",
                "Prior loan default increases the chance of manipulated disclosures.",
                "MEDIUM",
                "Registry shows at least one prior default.",
                ["loan_relationships"],
            )

        if signals:
            score += 0.05

        score = max(0.0, min(score, 1.0))
        recommendation = "PROCEED" if score < 0.30 else "FLAG_FOR_REVIEW" if score < 0.60 else "DECLINE"
        risk_level = "LOW" if score < 0.20 else "MEDIUM" if score < 0.50 else "HIGH"
        if score > 0.30 and not anomalies:
            score = min(score + 0.15, 1.0)
            recommendation = "FLAG_FOR_REVIEW" if score < 0.60 else "DECLINE"
            risk_level = "MEDIUM" if score < 0.50 else "HIGH"
            _add_anomaly(
                "document_alteration_suspected",
                "Overall fraud score indicates manual review is warranted.",
                "MEDIUM",
                "Composite score crossed the review threshold without a single dominant driver.",
                ["composite_score"],
            )

        anomaly_events = []
        for anomaly in anomalies:
            if anomaly["severity"] in {"MEDIUM", "HIGH"}:
                event = FraudAnomalyDetected(
                    application_id=state["application_id"],
                    session_id=state["session_id"],
                    anomaly=anomaly,
                    detected_at=datetime.utcnow(),
                ).to_store_dict()
                anomaly_events.append(event)

        if anomaly_events:
            await self._append_with_retry(f"fraud-{state['application_id']}", anomaly_events)

        await self._record_node_execution(
            "analyze_fraud_patterns",
            ["extracted_facts", "registry_profile", "historical_financials", "loan_history"],
            ["fraud_score", "anomalies"],
            int((time.time() - t) * 1000),
        )
        return {
            **state,
            "fraud_score": round(score, 4),
            "risk_level": risk_level,
            "recommendation": recommendation,
            "anomalies": anomalies,
            "fraud_signals": signals,
        }

    async def _node_write_output(self, state):
        t = time.time()
        app_id = state["application_id"]
        fraud_stream = f"fraud-{app_id}"
        loan_stream = f"loan-{app_id}"
        fraud_score = float(state.get("fraud_score") or 0.0)
        anomalies = state.get("anomalies") or []
        risk_level = state.get("risk_level") or ("LOW" if fraud_score < 0.20 else "MEDIUM" if fraud_score < 0.50 else "HIGH")
        recommendation = state.get("recommendation") or ("PROCEED" if fraud_score < 0.30 else "FLAG_FOR_REVIEW" if fraud_score < 0.60 else "DECLINE")

        completed = FraudScreeningCompleted(
            application_id=app_id,
            session_id=state["session_id"],
            fraud_score=fraud_score,
            risk_level=risk_level,
            anomalies_found=len(anomalies),
            recommendation=recommendation,
            screening_model_version=self.model,
            input_data_hash=self._sha({"facts": state.get("extracted_facts"), "profile": state.get("registry_profile"), "score": fraud_score}),
            completed_at=datetime.utcnow(),
        ).to_store_dict()

        compliance_request = ComplianceCheckRequested(
            application_id=app_id,
            requested_at=datetime.utcnow(),
            triggered_by_event_id=state["session_id"],
            regulation_set_version=RULE_SET_VERSION,
            rules_to_evaluate=list(REGULATIONS.keys()),
        ).to_store_dict()

        fraud_positions = await self._append_with_retry(fraud_stream, [completed])
        loan_positions = await self._append_with_retry(loan_stream, [compliance_request])

        events_written = [
            {"stream_id": fraud_stream, "event_type": "FraudScreeningCompleted", "stream_position": fraud_positions[0] if fraud_positions else -1},
            {"stream_id": loan_stream, "event_type": "ComplianceCheckRequested", "stream_position": loan_positions[0] if loan_positions else -1},
        ]
        await self._record_output_written(
            events_written,
            f"Fraud: {risk_level} risk, score {fraud_score:.2f}, recommendation {recommendation}. Compliance check triggered.",
        )
        await self._record_node_execution(
            "write_output",
            ["fraud_score", "anomalies"],
            ["events_written"],
            int((time.time() - t) * 1000),
        )
        return {**state, "output_events": events_written, "next_agent": "compliance", "next_agent_triggered": "compliance"}


# ─── COMPLIANCE AGENT ─────────────────────────────────────────────────────────

class ComplianceState(TypedDict):
    application_id: str
    session_id: str
    applicant_id: str | None
    company_profile: dict | None
    rule_results: list[dict] | None
    has_hard_block: bool
    block_rule_id: str | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


# Regulation definitions have been moved to ledger.domain.compliance_rules

class ComplianceAgent(BaseApexAgent):
    """
    Evaluates 6 deterministic regulatory rules in sequence.
    Stops at first hard block (is_hard_block=True).
    LLM not used in rule evaluation — only for human-readable evidence summaries.

    LangGraph nodes:
        validate_inputs → load_company_profile → evaluate_reg001 → evaluate_reg002 →
        evaluate_reg003 → evaluate_reg004 → evaluate_reg005 → evaluate_reg006 → write_output

    Note: Use conditional edges after each rule so hard blocks skip remaining rules.
    See add_conditional_edges() in LangGraph docs.

    Output events:
        compliance-{id}: ComplianceCheckInitiated,
                         ComplianceRulePassed/Failed/Noted (one per rule evaluated),
                         ComplianceCheckCompleted
        loan-{id}:       DecisionRequested (if no hard block)
                         ApplicationDeclined (if hard block)

    RULE EVALUATION PATTERN (each _node_evaluate_regXXX):
        1. co = state["company_profile"]
        2. passes = REGULATIONS[rule_id]["check"](co)
        3. eh = self._sha(f"{rule_id}-{co['company_id']}")
        4. If passes: append ComplianceRulePassed or ComplianceRuleNoted
        5. If fails: append ComplianceRuleFailed; if is_hard_block: set state["has_hard_block"]=True
        6. await self._record_node_execution(...)

    ROUTING:
        After each rule node, use conditional edge:
            g.add_conditional_edges(
                "evaluate_reg001",
                lambda s: "write_output" if s["has_hard_block"] else "evaluate_reg002",
            )

    WHEN THIS WORKS:
        pytest tests/phase2/test_compliance_agent.py
          → ComplianceCheckCompleted with correct verdict
          → NARR-04 (Montana REG-003 hard block): no DecisionRequested event,
            ApplicationDeclined present, adverse_action_notice_required=True
    """

    def build_graph(self):
        g = StateGraph(ComplianceState)
        g.add_node("validate_inputs",     self._node_validate_inputs)
        g.add_node("load_company_profile",self._node_load_profile)
        g.add_node("write_output",        self._node_write_output)

        async def evaluate_reg001(state):
            return await self._evaluate_rule(state, "REG-001")

        async def evaluate_reg002(state):
            return await self._evaluate_rule(state, "REG-002")

        async def evaluate_reg003(state):
            return await self._evaluate_rule(state, "REG-003")

        async def evaluate_reg004(state):
            return await self._evaluate_rule(state, "REG-004")

        async def evaluate_reg005(state):
            return await self._evaluate_rule(state, "REG-005")

        async def evaluate_reg006(state):
            return await self._evaluate_rule(state, "REG-006")

        g.add_node("evaluate_reg001", evaluate_reg001)
        g.add_node("evaluate_reg002", evaluate_reg002)
        g.add_node("evaluate_reg003", evaluate_reg003)
        g.add_node("evaluate_reg004", evaluate_reg004)
        g.add_node("evaluate_reg005", evaluate_reg005)
        g.add_node("evaluate_reg006", evaluate_reg006)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs",      "load_company_profile")
        g.add_edge("load_company_profile", "evaluate_reg001")

        # Conditional edges: stop at hard block, proceed otherwise
        for src, nxt in [
            ("evaluate_reg001", "evaluate_reg002"),
            ("evaluate_reg002", "evaluate_reg003"),
            ("evaluate_reg003", "evaluate_reg004"),
            ("evaluate_reg004", "evaluate_reg005"),
            ("evaluate_reg005", "evaluate_reg006"),
            ("evaluate_reg006", "write_output"),
        ]:
            g.add_conditional_edges(
                src,
                lambda s, _nxt=nxt: "write_output" if s["has_hard_block"] else _nxt,
            )
        g.add_edge("write_output", END)
        return g.compile()

    def _initial_state(self, application_id: str) -> ComplianceState:
        return ComplianceState(
            application_id=application_id, session_id=self.session_id, applicant_id=None,
            company_profile=None, rule_results=[], has_hard_block=False,
            block_rule_id=None, errors=[], output_events=[], next_agent=None,
        )

    async def _node_validate_inputs(self, state):
        t = time.time()
        loan_stream = f"loan-{state['application_id']}"
        events = await self.store.load_stream(loan_stream)
        if not events:
            raise ValueError(f"No events found for {loan_stream}")
        
        has_req = False
        applicant_id = None
        for ev in events:
            if ev["event_type"] == "ApplicationSubmitted":
                applicant_id = ev["payload"].get("applicant_id")
            elif ev["event_type"] == "ComplianceCheckRequested":
                has_req = True
                
        if not has_req:
            raise ValueError("ComplianceCheckRequested event not found in stream.")
        if not applicant_id:
            raise ValueError("Could not determine applicant_id from stream.")

        initiated = ComplianceCheckInitiated(
            application_id=state["application_id"],
            session_id=state["session_id"],
            regulation_set_version=RULE_SET_VERSION,
            rules_to_evaluate=list(REGULATIONS.keys()),
            initiated_at=datetime.utcnow(),
        ).to_store_dict()
        await self._append_with_retry(f"compliance-{state['application_id']}", [initiated])

        await self._record_node_execution(
            "validate_inputs",
            ["application_id"],
            ["applicant_id", "compliance_check_initiated"],
            int((time.time() - t) * 1000),
        )
        return {**state, "applicant_id": applicant_id}

    async def _node_load_profile(self, state):
        t = time.time()
        applicant_id = state.get("applicant_id")
        if not applicant_id:
            raise ValueError("applicant_id not in state")

        co = await self.registry.get_company(applicant_id)
        if not co:
            raise ValueError(f"Company profile not found for {applicant_id}")
        co = _to_plain_dict(co)

        flags = await self.registry.get_compliance_flags(applicant_id)
        co["compliance_flags"] = [_to_plain_dict(flag) for flag in flags]

        # Pull requested amount from loan stream to evaluate REG-004
        loan_events = await self.store.load_stream(f"loan-{state['application_id']}")
        for ev in loan_events:
            if ev["event_type"] == "ApplicationSubmitted":
                co["requested_amount_usd"] = ev["payload"].get("requested_amount_usd")
                break

        await self._record_tool_call(
            "registry.get_company_and_flags",
            {"applicant_id": applicant_id},
            {"company_id": co["company_id"], "flags_count": len(flags)},
            time.time() - t,
        )
        return {**state, "company_profile": co}

    async def _evaluate_rule(self, state: ComplianceState, rule_id: str) -> ComplianceState:
        t = time.time()
        rule = REGULATIONS[rule_id]
        co = state["company_profile"] or {}
        passes = rule.check(co)

        evidence_hash = self._sha(f"{rule_id}-{co.get('company_id', state['application_id'])}-{passes}")

        if rule_id == "REG-006":
            ev = ComplianceRuleNoted(
                application_id=state["application_id"],
                session_id=state["session_id"],
                rule_id=rule.rule_id,
                rule_name=rule.name,
                note_type=rule.note_type or "INFO",
                note_text=rule.note_text or "",
                evaluated_at=datetime.utcnow(),
            ).to_store_dict()
        elif passes:
            ev = ComplianceRulePassed(
                application_id=state["application_id"],
                session_id=state["session_id"],
                rule_id=rule.rule_id,
                rule_name=rule.name,
                rule_version=rule.version,
                evidence_hash=evidence_hash,
                evaluation_notes="Automated check passed",
                evaluated_at=datetime.utcnow(),
            ).to_store_dict()
        else:
            ev = ComplianceRuleFailed(
                application_id=state["application_id"],
                session_id=state["session_id"],
                rule_id=rule.rule_id,
                rule_name=rule.name,
                rule_version=rule.version,
                failure_reason=rule.failure_reason,
                is_hard_block=rule.is_hard_block,
                remediation_available=bool(rule.remediation),
                remediation_description=rule.remediation,
                evidence_hash=evidence_hash,
                evaluated_at=datetime.utcnow(),
            ).to_store_dict()

        await self._append_with_retry(f"compliance-{state['application_id']}", [ev])
        new_state = {
            **state,
            "rule_results": state.get("rule_results", []) + [{"rule_id": rule_id, "passes": passes}],
            "output_events": state.get("output_events", []) + [ev],
        }
        if not passes and rule.is_hard_block:
            new_state["has_hard_block"] = True
            new_state["block_rule_id"] = rule_id

        await self._record_node_execution(f"evaluate_{rule_id.lower().replace('-','_')}", None, None, time.time() - t)
        return new_state

    async def _node_write_output(self, state):
        t = time.time()
        passes = 0
        fails = 0
        notes = 0

        for ev in state.get("output_events", []):
            if ev["event_type"] == "ComplianceRulePassed":
                passes += 1
            elif ev["event_type"] == "ComplianceRuleFailed":
                fails += 1
            elif ev["event_type"] == "ComplianceRuleNoted":
                notes += 1

        verdict = "BLOCKED" if state.get("has_hard_block") else ("CONDITIONAL" if fails > 0 else "CLEAR")

        completion_ev = ComplianceCheckCompleted(
            application_id=state["application_id"],
            session_id=state["session_id"],
            rules_evaluated=passes + fails + notes,
            rules_passed=passes,
            rules_failed=fails,
            rules_noted=notes,
            has_hard_block=state.get("has_hard_block", False),
            overall_verdict=ComplianceVerdict(verdict),
            completed_at=datetime.utcnow(),
        ).to_store_dict()

        await self._append_with_retry(f"compliance-{state['application_id']}", [completion_ev])

        if state.get("has_hard_block"):
            loan_event = ApplicationDeclined(
                application_id=state["application_id"],
                decline_reasons=[f"Compliance hard block: {state.get('block_rule_id', 'REG-UNKNOWN')}"],
                declined_by="compliance_agent",
                adverse_action_notice_required=True,
                adverse_action_codes=["COMPLIANCE_BLOCK"],
                declined_at=datetime.utcnow(),
            ).to_store_dict()
            await self._append_with_retry(f"loan-{state['application_id']}", [loan_event])
            events_written = [
                {"stream_id": f"compliance-{state['application_id']}", "event_type": "ComplianceCheckCompleted", "stream_position": -1},
                {"stream_id": f"loan-{state['application_id']}", "event_type": "ApplicationDeclined", "stream_position": -1},
            ]
            next_agent = None
        else:
            loan_event = DecisionRequested(
                application_id=state["application_id"],
                requested_at=datetime.utcnow(),
                all_analyses_complete=True,
                triggered_by_event_id=state["session_id"],
            ).to_store_dict()
            await self._append_with_retry(f"loan-{state['application_id']}", [loan_event])
            events_written = [
                {"stream_id": f"compliance-{state['application_id']}", "event_type": "ComplianceCheckCompleted", "stream_position": -1},
                {"stream_id": f"loan-{state['application_id']}", "event_type": "DecisionRequested", "stream_position": -1},
            ]
            next_agent = "decision_orchestrator"

        await self._record_output_written(events_written, f"Compliance check completed: {verdict}")
        await self._record_node_execution("write_output", None, None, time.time() - t)
        return {**state, "output_events": state.get("output_events", []) + [completion_ev], "next_agent": next_agent}


# ─── DECISION ORCHESTRATOR ────────────────────────────────────────────────────

class OrchestratorState(TypedDict):
    application_id: str
    session_id: str
    credit_result: dict | None
    fraud_result: dict | None
    compliance_result: dict | None
    contributing_sessions: list[str] | None
    orchestrator_decision: dict | None
    recommendation: str | None
    confidence: float | None
    approved_amount: float | None
    executive_summary: str | None
    conditions: list[str] | None
    hard_constraints_applied: list[str] | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


class DecisionOrchestratorAgent(BaseApexAgent):
    """
    Synthesises all prior agent outputs into a final recommendation.
    The only agent that reads from multiple aggregate streams before deciding.

    LangGraph nodes:
        validate_inputs → load_credit_result → load_fraud_result →
        load_compliance_result → synthesize_decision → apply_hard_constraints →
        write_output

    Input streams read (load_* nodes):
        credit-{id}:     CreditAnalysisCompleted (last event of this type)
        fraud-{id}:      FraudScreeningCompleted
        compliance-{id}: ComplianceCheckCompleted

    Output events:
        loan-{id}:  DecisionGenerated
                    ApplicationApproved (if APPROVE)
                    ApplicationDeclined (if DECLINE)
                    HumanReviewRequested (if REFER)

    HARD CONSTRAINTS (Python, not LLM — applied in apply_hard_constraints node):
        1. compliance BLOCKED → recommendation = DECLINE (cannot override)
        2. confidence < 0.60 → recommendation = REFER (triggers HumanReviewRequested)
        3. fraud_score > 0.60 → recommendation = REFER (triggers HumanReviewRequested)
        4. risk_tier == HIGH and confidence < 0.70 → recommendation = REFER (triggers HumanReviewRequested)

    LLM in synthesize_decision:
        System: "You are a senior loan officer synthesising multi-agent analysis.
                 Produce a recommendation (APPROVE/DECLINE/REFER),
                 approved_amount_usd, executive_summary (3-5 sentences),
                 and key_risks list. Return OrchestratorDecision JSON."
        NOTE: The LLM recommendation may be overridden by apply_hard_constraints.
              Log this override in DecisionGenerated.policy_overrides_applied.

    WHEN THIS WORKS:
        pytest tests/phase2/test_orchestrator_agent.py
          → DecisionGenerated event on loan stream
          → NARR-05 (human override): DecisionGenerated.recommendation="DECLINE",
            followed by HumanReviewCompleted.override=True,
            followed by ApplicationApproved with correct override fields
    """

    def build_graph(self):
        g = StateGraph(OrchestratorState)
        g.add_node("validate_inputs",         self._node_validate_inputs)
        g.add_node("load_credit_result",      self._node_load_credit)
        g.add_node("load_fraud_result",       self._node_load_fraud)
        g.add_node("load_compliance_result",  self._node_load_compliance)
        g.add_node("synthesize_decision",     self._node_synthesize)
        g.add_node("apply_hard_constraints",  self._node_constraints)
        g.add_node("write_output",            self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs",        "load_credit_result")
        g.add_edge("load_credit_result",     "load_fraud_result")
        g.add_edge("load_fraud_result",      "load_compliance_result")
        g.add_edge("load_compliance_result", "synthesize_decision")
        g.add_edge("synthesize_decision",    "apply_hard_constraints")
        g.add_edge("apply_hard_constraints", "write_output")
        g.add_edge("write_output",           END)
        return g.compile()

    def _initial_state(self, application_id: str) -> OrchestratorState:
        return OrchestratorState(
            application_id=application_id, session_id=self.session_id,
            credit_result=None, fraud_result=None, compliance_result=None,
            contributing_sessions=None, orchestrator_decision=None, recommendation=None, confidence=None, approved_amount=None,
            executive_summary=None, conditions=None, hard_constraints_applied=[],
            errors=[], output_events=[], next_agent=None,
        )

    async def _node_validate_inputs(self, state):
        t = time.time()
        app_id = state["application_id"]
        events = await self.store.load_stream(f"loan-{app_id}")
        if not any(ev["event_type"] == "DecisionRequested" for ev in events):
            raise ValueError("DecisionRequested event not found in loan stream.")
        if not any(ev["event_type"] == "CreditAnalysisCompleted" for ev in await self.store.load_stream(f"credit-{app_id}")):
            raise ValueError("CreditAnalysisCompleted event not found in credit stream.")
        if not any(ev["event_type"] == "FraudScreeningCompleted" for ev in await self.store.load_stream(f"fraud-{app_id}")):
            raise ValueError("FraudScreeningCompleted event not found in fraud stream.")
        if not any(ev["event_type"] == "ComplianceCheckCompleted" for ev in await self.store.load_stream(f"compliance-{app_id}")):
            raise ValueError("ComplianceCheckCompleted event not found in compliance stream.")
        await self._record_node_execution("validate_inputs", ["application_id"], ["analysis_ready"], int((time.time() - t) * 1000))
        return state

    async def _node_load_credit(self, state):
        t = time.time()
        events = await self.store.load_stream(f"credit-{state['application_id']}")
        completed = next((ev for ev in reversed(events) if ev["event_type"] == "CreditAnalysisCompleted"), None)
        if not completed:
            raise ValueError("CreditAnalysisCompleted not found.")
        await self._record_tool_call("load_stream", f"credit-{state['application_id']}", "CreditAnalysisCompleted", int((time.time() - t) * 1000))
        return {**state, "credit_result": completed["payload"]}

    async def _node_load_fraud(self, state):
        t = time.time()
        events = await self.store.load_stream(f"fraud-{state['application_id']}")
        completed = next((ev for ev in reversed(events) if ev["event_type"] == "FraudScreeningCompleted"), None)
        if not completed:
            raise ValueError("FraudScreeningCompleted not found.")
        await self._record_tool_call("load_stream", f"fraud-{state['application_id']}", "FraudScreeningCompleted", int((time.time() - t) * 1000))
        return {**state, "fraud_result": completed["payload"]}

    async def _node_load_compliance(self, state):
        t = time.time()
        events = await self.store.load_stream(f"compliance-{state['application_id']}")
        completed = next((ev for ev in reversed(events) if ev["event_type"] == "ComplianceCheckCompleted"), None)
        if not completed:
            raise ValueError("ComplianceCheckCompleted not found.")
        await self._record_tool_call("load_stream", f"compliance-{state['application_id']}", "ComplianceCheckCompleted", int((time.time() - t) * 1000))
        contributing_sessions = [
            f"credit-{state['application_id']}",
            f"fraud-{state['application_id']}",
            f"compliance-{state['application_id']}",
        ]
        return {**state, "compliance_result": completed["payload"], "contributing_sessions": contributing_sessions}

    async def _node_synthesize(self, state):
        t = time.time()
        credit = state.get("credit_result") or {}
        fraud = state.get("fraud_result") or {}
        compliance = state.get("compliance_result") or {}
        credit_decision = credit.get("decision") or {}
        risk_tier = str(credit_decision.get("risk_tier") or "MEDIUM").upper()
        confidence = float(credit_decision.get("confidence") or 0.0)
        fraud_score = float(fraud.get("fraud_score") or 0.0)
        compliance_verdict = str(compliance.get("overall_verdict") or "CLEAR").upper()

        if compliance_verdict == "BLOCKED":
            recommendation = "DECLINE"
        elif fraud_score > 0.60 or confidence < 0.60 or (risk_tier == "HIGH" and confidence < 0.70):
            recommendation = "REFER"
        elif risk_tier == "HIGH":
            recommendation = "DECLINE"
        else:
            recommendation = "APPROVE"

        approved_amount = credit_decision.get("recommended_limit_usd")
        requested_amount = None
        loan_events = await self.store.load_stream(f"loan-{state['application_id']}")
        for ev in loan_events:
            if ev["event_type"] == "ApplicationSubmitted":
                requested_amount = ev["payload"].get("requested_amount_usd")
                break
        if approved_amount is not None and requested_amount is not None:
            approved_amount = min(float(approved_amount), float(requested_amount))

        key_risks = []
        if fraud.get("anomalies_found", 0):
            key_risks.append(f"{fraud.get('anomalies_found')} fraud anomalies detected")
        if compliance_verdict == "CONDITIONAL":
            key_risks.append("Compliance completed with conditional findings")
        if credit_decision.get("key_concerns"):
            key_risks.extend([str(item) for item in credit_decision.get("key_concerns", [])][:2])
        if not key_risks:
            key_risks = ["No major red flags detected"]

        executive_summary = (
            f"Credit risk is {risk_tier.lower()} with {confidence:.0%} confidence. "
            f"Fraud score is {fraud_score:.2f} and compliance verdict is {compliance_verdict.lower()}. "
            f"Preliminary recommendation is {recommendation.lower()}."
        )

        decision = {
            "recommendation": recommendation,
            "confidence": confidence,
            "approved_amount_usd": approved_amount,
            "executive_summary": executive_summary,
            "key_risks": key_risks,
            "conditions": list(credit_decision.get("conditions", [])),
            "policy_overrides_applied": list(credit_decision.get("policy_overrides_applied", [])),
            "model_versions": {
                "credit": str(credit.get("model_version") or ""),
                "fraud": str(fraud.get("screening_model_version") or ""),
                "compliance": str(compliance.get("regulation_set_version") or ""),
            },
        }

        await self._record_node_execution(
            "synthesize_decision",
            ["credit_result", "fraud_result", "compliance_result"],
            ["orchestrator_decision"],
            int((time.time() - t) * 1000),
        )
        return {**state, "recommendation": decision["recommendation"], "confidence": decision["confidence"], "approved_amount": decision["approved_amount_usd"], "executive_summary": decision["executive_summary"], "conditions": decision["conditions"], "orchestrator_decision": decision}

    async def _node_constraints(self, state):
        t = time.time()
        decision = dict(state.get("orchestrator_decision") or {})
        applied = list(state.get("hard_constraints_applied") or [])
        credit = state.get("credit_result") or {}
        fraud = state.get("fraud_result") or {}
        compliance = state.get("compliance_result") or {}
        compliance_verdict = str(compliance.get("overall_verdict") or "CLEAR").upper()
        confidence = float(decision.get("confidence") or 0.0)
        fraud_score = float(fraud.get("fraud_score") or 0.0)
        risk_tier = str((credit.get("decision") or {}).get("risk_tier") or "MEDIUM").upper()

        if compliance_verdict == "BLOCKED":
            if decision.get("recommendation") != "DECLINE":
                applied.append("COMPLIANCE_BLOCK")
            decision["recommendation"] = "DECLINE"
        elif confidence < 0.60:
            if decision.get("recommendation") != "REFER":
                applied.append("CONFIDENCE_FLOOR")
            decision["recommendation"] = "REFER"
        elif fraud_score > 0.60:
            if decision.get("recommendation") != "REFER":
                applied.append("FRAUD_FLOOR")
            decision["recommendation"] = "REFER"
        elif risk_tier == "HIGH" and confidence < 0.70:
            if decision.get("recommendation") != "REFER":
                applied.append("HIGH_RISK_CONFIDENCE")
            decision["recommendation"] = "REFER"

        decision["policy_overrides_applied"] = list(dict.fromkeys((decision.get("policy_overrides_applied") or []) + applied))
        if decision["recommendation"] == "REFER":
            decision["approved_amount_usd"] = None

        await self._record_node_execution(
            "apply_hard_constraints",
            ["orchestrator_decision"],
            ["orchestrator_decision"],
            int((time.time() - t) * 1000),
        )
        return {**state, "orchestrator_decision": decision, "recommendation": decision["recommendation"], "approved_amount": decision.get("approved_amount_usd"), "hard_constraints_applied": decision["policy_overrides_applied"]}

    async def _node_write_output(self, state):
        t = time.time()
        app_id = state["application_id"]
        decision = state.get("orchestrator_decision") or {}
        recommendation = str(decision.get("recommendation") or "REFER").upper()
        confidence = float(decision.get("confidence") or 0.0)
        approved_amount = decision.get("approved_amount_usd")
        if approved_amount is not None:
            approved_amount = float(approved_amount)
        model_versions = decision.get("model_versions") or {}
        contributing_sessions = list(state.get("contributing_sessions") or [])
        if not contributing_sessions:
            contributing_sessions = [f"credit-{app_id}", f"fraud-{app_id}", f"compliance-{app_id}"]

        decision_event = DecisionGenerated(
            application_id=app_id,
            orchestrator_session_id=state["session_id"],
            recommendation=recommendation,
            confidence=confidence,
            approved_amount_usd=approved_amount,
            conditions=list(decision.get("conditions") or []),
            executive_summary=str(decision.get("executive_summary") or ""),
            key_risks=list(decision.get("key_risks") or []),
            contributing_sessions=contributing_sessions,
            model_versions={k: str(v) for k, v in model_versions.items()},
            generated_at=datetime.utcnow(),
        ).to_store_dict()

        events = [decision_event]
        next_agent = None
        if recommendation == "APPROVE":
            compliance_record = await ComplianceRecordAggregate.load(self.store, app_id)
            if not compliance_record.completed or compliance_record.verdict == "BLOCKED":
                raise ValueError("ApplicationApproved requires completed non-blocked compliance")
            events.append(
                ApplicationApproved(
                    application_id=app_id,
                    approved_amount_usd=Decimal(str(approved_amount or 0)),
                    interest_rate_pct=float((decision.get("interest_rate_pct") or 12.5)),
                    term_months=int((decision.get("term_months") or 36)),
                    conditions=list(decision.get("conditions") or []),
                    approved_by=str(decision.get("approved_by") or "auto"),
                    effective_date=str(decision.get("effective_date") or datetime.utcnow().date().isoformat()),
                    approved_at=datetime.utcnow(),
                ).to_store_dict()
            )
        elif recommendation == "DECLINE":
            events.append(
                ApplicationDeclined(
                    application_id=app_id,
                    decline_reasons=list(decision.get("decline_reasons") or ["Risk policy decline"]),
                    declined_by=str(decision.get("declined_by") or "decision_orchestrator"),
                    adverse_action_notice_required=bool(decision.get("adverse_action_notice_required", True)),
                    adverse_action_codes=list(decision.get("adverse_action_codes") or []),
                    declined_at=datetime.utcnow(),
                ).to_store_dict()
            )
        else:
            events.append(
                HumanReviewRequested(
                    application_id=app_id,
                    reason=str(decision.get("review_reason") or "Recommendation REFER or low confidence"),
                    decision_event_id=self._sha({"application_id": app_id, "session_id": state["session_id"], "recommendation": recommendation}),
                    assigned_to=decision.get("assigned_to"),
                    requested_at=datetime.utcnow(),
                ).to_store_dict()
            )
            next_agent = "human_review"

        positions = await self._append_with_retry(f"loan-{app_id}", events)
        events_written = [
            {"stream_id": f"loan-{app_id}", "event_type": events[0]["event_type"], "stream_position": positions[0] if positions else -1}
        ]
        if len(events) > 1:
            events_written.append({"stream_id": f"loan-{app_id}", "event_type": events[1]["event_type"], "stream_position": positions[1] if len(positions) > 1 else -1})

        await self._record_output_written(
            events_written,
            f"Decision {recommendation}: confidence {confidence:.0%}, approved_amount {approved_amount}.",
        )
        await self._record_node_execution("write_output", ["orchestrator_decision"], ["events_written"], int((time.time() - t) * 1000))
        return {**state, "output_events": events_written, "next_agent": next_agent, "next_agent_triggered": next_agent}
