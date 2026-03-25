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
import os, time, json, re
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, END

from ledger.agents.base_agent import BaseApexAgent
from ledger.agents.extraction_adapter import DatagenExtractionAdapter
try:
    import sys as _sys, os as _os
    _refinery_path = _os.getenv("DOCUMENT_REFINERY_PATH", "")
    if _refinery_path and _refinery_path not in _sys.path:
        _sys.path.insert(0, _refinery_path)
    from src.agents.extractor import run_extraction, ExtractedDocument
    REFINERY_AVAILABLE = True
except ImportError:
    REFINERY_AVAILABLE = False
    run_extraction = None  # type: ignore[assignment]
    ExtractedDocument = Any  # type: ignore[assignment]
try:
    from ledger.agents.ollama_client import OllamaClient
except Exception:  # pragma: no cover - optional path guard
    OllamaClient = None  # type: ignore[assignment]
try:
    _ollama = OllamaClient() if OllamaClient is not None else None
except ImportError:  # pragma: no cover - optional path guard
    _ollama = None
from ledger.domain.aggregates.compliance_record import ComplianceRecordAggregate
from ledger.domain.compliance_rules import REGULATIONS, RULE_SET_VERSION
from ledger.schema.events import (
    ApplicationApproved,
    ApplicationDeclined,
    CreditAnalysisRequested,
    ComplianceCheckCompleted,
    ComplianceCheckInitiated,
    ComplianceCheckRequested,
    ComplianceRuleFailed,
    ComplianceRuleNoted,
    ComplianceRulePassed,
    DecisionGenerated,
    DecisionRequested,
    DocumentFormatRejected,
    DocumentFormatValidated,
    DocumentUploaded,
    ExtractionCompleted,
    ExtractionFailed,
    ExtractionStarted,
    FraudAnomalyDetected,
    FraudScreeningCompleted,
    FraudScreeningInitiated,
    HumanReviewRequested,
    HumanReviewCompleted,
    ComplianceVerdict,
    FinancialFacts,
    PackageReadyForAnalysis,
    QualityAssessmentCompleted,
    DocumentType,
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


# â”€â”€â”€ DOCUMENT PROCESSING AGENT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class DocProcState(TypedDict):
    application_id: str
    session_id: str
    package_id: str | None
    processing_mode: str | None
    documents: list[dict] | None
    document_ids: list[str] | None
    document_paths: list[str] | None
    structured_input: dict | None
    pdf_path: str | None
    refinery_result: Any | None
    processed_document: dict | None
    extraction_results: list[dict] | None  # one per document
    quality_assessment: dict | None
    quality_flags: list[str] | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


class DocumentProcessingAgent(BaseApexAgent):
    """
    Wraps the Week 3 Document Intelligence pipeline.
    Processes uploaded PDFs and appends extraction events.

    LangGraph nodes:
        validate_inputs â†’ validate_document_formats â†’ extract_income_statement â†’
        extract_balance_sheet â†’ assess_quality â†’ write_output

    Output events:
        docpkg-{id}:  DocumentFormatValidated (x per doc), ExtractionStarted (x per doc),
                      ExtractionCompleted (x per doc), QualityAssessmentCompleted,
                      PackageReadyForAnalysis
        loan-{id}:    CreditAnalysisRequested

    WEEK 3 INTEGRATION:
        In _node_extract_document(), call your Week 3 pipeline:
            from document_refinery.pipeline import extract_financial_facts
            facts = await extract_financial_facts(file_path, document_type)
        Wrap in try/except â€” append ExtractionFailed if pipeline raises.

    LLM in _node_assess_quality():
        System: "You are a financial document quality analyst.
                 Check internal consistency. Do NOT make credit decisions.
                 Return DocumentQualityAssessment JSON."
        The LLM checks: Assets = Liabilities + Equity, margins plausible, etc.

    WHEN THIS WORKS:
        pytest tests/phase2/test_document_agent.py  # all pass
        python scripts/run_pipeline.py --app APEX-0001 --phase document
          â†’ ExtractionCompleted event in docpkg stream with non-null total_revenue
          â†’ QualityAssessmentCompleted event present
          â†’ PackageReadyForAnalysis event present
          â†’ CreditAnalysisRequested on loan stream
    """

    def __init__(self, agent_id: str, agent_type: str, store, registry, llm=None, model=os.getenv("OLLAMA_MODEL", "llama3:8b"), client=None, extraction_adapter=None):
        super().__init__(agent_id, agent_type, store, registry, llm=llm, model=model, client=client)
        self.extraction_adapter = extraction_adapter or DatagenExtractionAdapter()

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

    def _initial_state(
        self,
        application_id: str,
        recovery_context_text=None,
        recovery_pending_work=None,
        recovered_from_session_id=None,
        recovery_point=None,
        resume_after_sequence: int = -1,
        resume_state_snapshot: dict | None = None,
    ) -> DocProcState:
        state: DocProcState = DocProcState(
            application_id=application_id,
            session_id=self.session_id,
            package_id=None,
            processing_mode=None,
            documents=None,
            document_ids=None,
            document_paths=None,
            structured_input=None,
            pdf_path=None,
            refinery_result=None,
            processed_document=None,
            extraction_results=None,
            quality_assessment=None,
            quality_flags=None,
            errors=[],
            output_events=[],
            next_agent=None,
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
            state.update(snapshot_state)  # type: ignore[arg-type]
        return state

    @staticmethod
    def _coerce_float(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _append_failure_detail(app_id: str, reason: str, details: str, failed_fields: list[str] | None = None) -> dict[str, Any]:
        return {
            "event_type": "DOCUMENT_PROCESSING_FAILED",
            "event_version": 1,
            "payload": {
                "application_id": app_id,
                "reason": reason,
                "details": details,
                "failed_fields": failed_fields or [],
            },
        }

    @staticmethod
    def _normalize_extracted_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return value

    @staticmethod
    def _row_provenance(row: dict[str, Any]) -> dict[str, Any] | None:
        provenance = row.get("provenance")
        if isinstance(provenance, dict):
            candidate = dict(provenance)
        else:
            candidate = {k: row.get(k) for k in ("doc_id", "page_number", "bbox", "content_hash", "strategy_used") if row.get(k) is not None}
        required = {"doc_id", "page_number", "bbox", "content_hash", "strategy_used"}
        if not required.issubset(candidate.keys()):
            return None
        return {
            "doc_id": str(candidate["doc_id"]),
            "page_number": int(candidate["page_number"]),
            "bbox": list(candidate["bbox"]),
            "content_hash": str(candidate["content_hash"]),
            "strategy_used": str(candidate["strategy_used"]),
        }

    @staticmethod
    def _row_matches_keywords(row: dict[str, Any], keywords: list[str]) -> bool:
        haystack = " ".join(
            str(row.get(key, ""))
            for key in ("field_name", "fact_name", "label", "key", "name", "description", "text")
        ).lower()
        return any(keyword in haystack for keyword in keywords)

    @staticmethod
    def _row_numeric_value(row: dict[str, Any]) -> float | None:
        for key in ("numeric_value", "value", "field_value", "fact_value", "amount", "extracted_value"):
            if key in row:
                value = row.get(key)
                if isinstance(value, (int, float)):
                    return float(value)
                try:
                    return float(str(value).replace(",", "").strip())
                except Exception:
                    continue
        if len(row) == 1:
            only_value = next(iter(row.values()))
            if isinstance(only_value, (int, float)):
                return float(only_value)
            try:
                return float(str(only_value).replace(",", "").strip())
            except Exception:
                return None
        return None

    @staticmethod
    def _normalize_record(value: Any) -> dict[str, Any]:
        record = _to_plain_dict(value)
        if not isinstance(record, dict):
            return {}
        return dict(record)

    @staticmethod
    def _page_confidence(page: Any) -> float:
        page_dict = _to_plain_dict(page)
        for key in ("confidence", "page_confidence", "mean_confidence"):
            if key in page_dict:
                try:
                    return float(page_dict[key])
                except Exception:
                    continue
        try:
            return float(getattr(page, "confidence"))
        except Exception:
            return 0.0

    @staticmethod
    def _page_strategy(page: Any) -> str | None:
        page_dict = _to_plain_dict(page)
        for key in ("strategy_used", "strategy", "extraction_strategy"):
            if page_dict.get(key):
                return str(page_dict[key])
        for attr in ("strategy_used", "strategy", "extraction_strategy"):
            if getattr(page, attr, None):
                return str(getattr(page, attr))
        return None

    @staticmethod
    def _document_class_from_result(result: Any) -> str:
        for key in ("document_class", "doc_class", "class_name"):
            value = getattr(result, key, None)
            if value:
                return str(value)
        if isinstance(result, dict):
            for key in ("document_class", "doc_class", "class_name"):
                if result.get(key):
                    return str(result[key])
        return "unknown"

    @staticmethod
    def _strategy_used_from_result(result: Any, pages: list[Any]) -> str:
        for key in ("strategy_used", "strategy", "routing_strategy"):
            value = getattr(result, key, None)
            if value:
                return str(value)
        if isinstance(result, dict):
            for key in ("strategy_used", "strategy", "routing_strategy"):
                if result.get(key):
                    return str(result[key])
        for page in pages:
            strategy = DocumentProcessingAgent._page_strategy(page)
            if strategy:
                return strategy
        return "unknown"

    @staticmethod
    def _doc_id_from_result(result: Any, pdf_path: str | None) -> str:
        for key in ("doc_id", "document_id"):
            value = getattr(result, key, None)
            if value:
                return str(value)
        if isinstance(result, dict):
            for key in ("doc_id", "document_id"):
                if result.get(key):
                    return str(result[key])
        if pdf_path:
            return Path(pdf_path).stem
        return "unknown"

    @staticmethod
    def _fact_table_rows(result: Any) -> list[dict[str, Any]]:
        fact_table = getattr(result, "fact_table", None)
        if fact_table is None and isinstance(result, dict):
            fact_table = result.get("fact_table")
        rows: list[dict[str, Any]] = []
        if isinstance(fact_table, dict):
            fact_table = list(fact_table.values())
        if isinstance(fact_table, list):
            for row in fact_table:
                normalized = _to_plain_dict(row)
                if isinstance(normalized, dict):
                    rows.append(dict(normalized))
        return rows

    @staticmethod
    def _pages_from_result(result: Any) -> list[Any]:
        pages = getattr(result, "pages", None)
        if pages is None and isinstance(result, dict):
            pages = result.get("pages")
        if not pages:
            return []
        return list(pages)

    async def _node_validate_inputs(self, state):
        t = time.time()
        app_id = state["application_id"]
        loan_events = await self.store.load_stream(f"loan-{app_id}")
        if not loan_events:
            raise ValueError(f"No events found for loan-{app_id}")

        documents: list[dict[str, Any]] = []
        structured_input: dict[str, Any] | None = None
        pdf_path = None
        for ev in loan_events:
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if "pdf_path" in payload and payload.get("pdf_path"):
                pdf_path = str(payload.get("pdf_path"))
                structured_input = dict(payload)
            if structured_input is None and any(key in payload for key in ("loan_amount", "annual_income", "loan_term_months", "requested_amount_usd")):
                structured_input = dict(payload)
            if ev.get("event_type") == "DocumentUploaded":
                documents.append({
                    "document_id": str(payload.get("document_id") or ""),
                    "document_type": str(payload.get("document_type") or ""),
                    "file_path": str(payload.get("file_path") or ""),
                    "filename": str(payload.get("filename") or ""),
                    "application_id": app_id,
                })

        if pdf_path:
            mode = "refinery"
        elif structured_input and not documents:
            mode = "structured"
        elif documents:
            mode = "legacy"
            if structured_input is None:
                structured_input = {
                    "applicant_id": next((str((ev.get("payload") or {}).get("applicant_id") or "") for ev in loan_events if ev.get("event_type") == "ApplicationSubmitted"), ""),
                    "loan_amount": next((self._coerce_float((ev.get("payload") or {}).get("requested_amount_usd")) for ev in loan_events if ev.get("event_type") == "ApplicationSubmitted"), None),
                    "loan_term_months": next((int((ev.get("payload") or {}).get("loan_term_months") or 0) or None for ev in loan_events if ev.get("event_type") == "ApplicationSubmitted"), None),
                }
        else:
            raise ValueError(f"No usable document or structured loan input found for loan-{app_id}")

        await self._record_node_execution(
            "validate_inputs",
            ["application_id"],
            ["document_ids", "document_paths", "documents", "structured_input", "pdf_path", "processing_mode"],
            int((time.time() - t) * 1000),
        )
        return {
            **state,
            "package_id": f"docpkg-{app_id}",
            "documents": documents,
            "document_ids": [doc["document_id"] for doc in documents],
            "document_paths": [doc["file_path"] for doc in documents],
            "structured_input": structured_input,
            "pdf_path": pdf_path,
            "processing_mode": mode,
        }

    async def _node_validate_formats(self, state):
        t = time.time()
        package_id = state.get("package_id") or f"docpkg-{state['application_id']}"
        mode = state.get("processing_mode") or "legacy"
        docs = list(state.get("documents") or [])
        written: list[dict[str, Any]] = []

        if mode == "structured":
            structured = dict(state.get("structured_input") or {})
            required = ["applicant_id", "loan_amount", "annual_income", "loan_term_months"]
            failed_fields = [field for field in required if structured.get(field) in (None, "", [])]
            if failed_fields:
                failure = self._append_failure_detail(
                    state["application_id"],
                    "required_field_not_extracted",
                    f"Missing required structured loan fields: {', '.join(failed_fields)}",
                    failed_fields=failed_fields,
                )
                await self._append_with_retry(package_id, [failure])
                raise ValueError(f"Missing required structured loan fields: {failed_fields}")

            await self._record_node_execution(
                "validate_document_formats",
                ["structured_input"],
                ["documents", "structured_input"],
                int((time.time() - t) * 1000),
            )
            return {
                **state,
                "structured_input": structured,
                "processing_mode": mode,
            }

        if mode == "refinery":
            pdf_path = str(state.get("pdf_path") or "")
            if not pdf_path or not (_os.getenv("DOCUMENT_REFINERY_PATH", "") and REFINERY_AVAILABLE and callable(run_extraction)):
                failure = self._append_failure_detail(
                    state["application_id"],
                    "refinery_unavailable",
                    "DOCUMENT_REFINERY_PATH is not set or document-refinery could not be imported.",
                )
                await self._append_with_retry(package_id, [failure])
                raise RuntimeError("document-refinery unavailable")

            extraction_config = {
                "lang": "amh+eng",
                "psm": 3,
                "table_density_threshold": 0.3,
                "high_image_thin_gate": True,
                "vlm_timeout": 45,
                "max_vlm_pages": 40,
                "total_runtime_cap": 900,
            }
            try:
                result: ExtractedDocument = run_extraction(pdf_path, extraction_config)  # type: ignore[misc]
            except Exception as exc:
                failure = self._append_failure_detail(
                    state["application_id"],
                    "extraction_failed",
                    str(exc),
                )
                await self._append_with_retry(package_id, [failure])
                raise

            pages = self._pages_from_result(result)
            if not pages:
                failure = self._append_failure_detail(
                    state["application_id"],
                    "extraction_failed",
                    "run_extraction returned no pages",
                )
                await self._append_with_retry(package_id, [failure])
                raise ValueError("run_extraction returned no pages")

            confidences = [self._page_confidence(page) for page in pages]
            mean_confidence = sum(confidences) / max(len(confidences), 1)
            if mean_confidence < 0.50:
                failure = self._append_failure_detail(
                    state["application_id"],
                    "confidence_too_low",
                    f"mean_confidence={mean_confidence:.2f}",
                )
                await self._append_with_retry(package_id, [failure])
                raise ValueError(f"mean confidence too low: {mean_confidence:.2f}")

            doc_id = self._doc_id_from_result(result, pdf_path)
            strategy_used = self._strategy_used_from_result(result, pages)
            document_class = self._document_class_from_result(result)
            escalation_count = int(getattr(result, "escalation_count", None) or (result.get("escalation_count") if isinstance(result, dict) else 0) or 0)
            ingested_event = {
                "event_type": "DOCUMENT_INGESTED",
                "event_version": 1,
                "payload": {
                    "doc_id": doc_id,
                    "page_count": len(pages),
                    "strategy_used": strategy_used,
                    "extraction_confidence": round(mean_confidence, 4),
                    "document_class": document_class,
                    "escalation_count": escalation_count,
                },
            }
            await self._append_with_retry(package_id, [ingested_event])
            await self._record_node_execution(
                "validate_document_formats",
                ["pdf_path"],
                ["refinery_result"],
                int((time.time() - t) * 1000),
            )
            return {
                **state,
                "refinery_result": result,
                "processing_mode": mode,
                "pdf_path": pdf_path,
                "document_ids": [doc_id],
            }

        if not docs:
            raise ValueError("No documents available for format validation")

        valid_docs: list[dict[str, Any]] = []
        for doc in docs:
            file_path = Path(doc.get("file_path") or "")
            doc_id = doc.get("document_id") or "unknown"
            doc_type = doc.get("document_type") or "unknown"
            suffix = file_path.suffix.lower()
            if not file_path.exists():
                rejected = DocumentFormatRejected(
                    package_id=package_id,
                    document_id=str(doc_id),
                    rejection_reason=f"Document file not found: {file_path}",
                    rejected_at=datetime.utcnow(),
                ).to_store_dict()
                await self._append_with_retry(package_id, [rejected])
                written.append({"stream_id": package_id, "event_type": "DocumentFormatRejected", "stream_position": -1})
                continue

            if suffix not in {".pdf", ".xlsx", ".xls", ".csv"}:
                rejected = DocumentFormatRejected(
                    package_id=package_id,
                    document_id=str(doc_id),
                    rejection_reason=f"Unsupported file format: {suffix or 'unknown'}",
                    rejected_at=datetime.utcnow(),
                ).to_store_dict()
                await self._append_with_retry(package_id, [rejected])
                written.append({"stream_id": package_id, "event_type": "DocumentFormatRejected", "stream_position": -1})
                continue

            detected_format = "pdf" if suffix == ".pdf" else "xlsx" if suffix in {".xlsx", ".xls"} else "csv"
            validated = DocumentFormatValidated(
                package_id=package_id,
                document_id=str(doc_id),
                document_type=doc_type,
                page_count=1,
                detected_format=detected_format,
                validated_at=datetime.utcnow(),
            ).to_store_dict()
            await self._append_with_retry(package_id, [validated])
            valid_docs.append(doc)
            written.append({"stream_id": package_id, "event_type": "DocumentFormatValidated", "stream_position": -1})

        await self._record_node_execution(
            "validate_document_formats",
            ["documents"],
            ["documents"],
            int((time.time() - t) * 1000),
        )
        return {
            **state,
            "documents": valid_docs,
            "document_ids": [doc["document_id"] for doc in valid_docs],
            "document_paths": [doc["file_path"] for doc in valid_docs],
            "output_events": state.get("output_events", []) + written,
        }

    async def _node_extract_is(self, state):
        if (state.get("processing_mode") or "legacy") != "legacy":
            await self._record_node_execution(
                "extract_income_statement",
                ["documents"],
                ["extraction_results"],
                0,
            )
            return state
        t = time.time()
        return await self._extract_document_type(state, DocumentType.INCOME_STATEMENT, "extract_income_statement")

    async def _node_extract_bs(self, state):
        if (state.get("processing_mode") or "legacy") != "legacy":
            await self._record_node_execution(
                "extract_balance_sheet",
                ["documents"],
                ["extraction_results"],
                0,
            )
            return state
        t = time.time()
        return await self._extract_document_type(state, DocumentType.BALANCE_SHEET, "extract_balance_sheet")

    async def _node_assess_quality(self, state):
        t = time.time()
        mode = state.get("processing_mode") or "legacy"
        package_id = state.get("package_id") or f"docpkg-{state['application_id']}"

        if mode == "structured":
            structured = dict(state.get("structured_input") or {})
            processed_document = {
                "applicant_id": str(structured.get("applicant_id") or state["application_id"]),
                "loan_amount": self._coerce_float(structured.get("loan_amount") or structured.get("requested_amount_usd")) or 0.0,
                "annual_income": self._coerce_float(structured.get("annual_income")) or 0.0,
                "loan_term_months": int(structured.get("loan_term_months") or 0),
            }
            quality_assessment = {
                "overall_confidence": 1.0,
                "is_coherent": True,
                "anomalies": [],
                "critical_missing_fields": [],
                "reextraction_recommended": False,
                "auditor_notes": "Structured loan fields validated successfully.",
            }
            await self._record_node_execution(
                "assess_quality",
                ["structured_input"],
                ["quality_assessment", "processed_document"],
                int((time.time() - t) * 1000),
            )
            return {
                **state,
                "processed_document": processed_document,
                "quality_assessment": quality_assessment,
                "quality_flags": [],
            }

        if mode == "refinery":
            result = state.get("refinery_result")
            pages = self._pages_from_result(result)
            if not pages:
                failure = self._append_failure_detail(
                    state["application_id"],
                    "extraction_failed",
                    "refinery_result missing pages",
                )
                await self._append_with_retry(package_id, [failure])
                raise ValueError("refinery_result missing pages")

            rows = self._fact_table_rows(result)
            field_specs = {
                "loan_amount": ["loan", "amount", "principal"],
                "annual_income": ["income", "salary", "earnings"],
                "loan_term_months": ["term", "months", "duration"],
            }
            mapped: dict[str, Any] = {}
            provenance: dict[str, dict[str, Any]] = {}
            for field_name, keywords in field_specs.items():
                for row in rows:
                    if not self._row_matches_keywords(row, keywords):
                        continue
                    value = self._row_numeric_value(row)
                    prov = self._row_provenance(row)
                    if value is None or prov is None:
                        continue
                    mapped[field_name] = value
                    provenance[field_name] = prov
                    break

            failed_fields = [field for field in field_specs if field not in mapped]
            if failed_fields:
                failure = self._append_failure_detail(
                    state["application_id"],
                    "required_field_not_extracted",
                    f"Missing mapped fields: {', '.join(failed_fields)}",
                    failed_fields=failed_fields,
                )
                await self._append_with_retry(package_id, [failure])
                raise ValueError(f"Missing mapped fields: {failed_fields}")

            doc_id = self._doc_id_from_result(result, state.get("pdf_path"))
            applicant_id = doc_id if doc_id else Path(str(state.get("pdf_path") or "")).stem
            mean_confidence = sum(self._page_confidence(page) for page in pages) / max(len(pages), 1)
            quality_assessment = {
                "overall_confidence": round(mean_confidence, 4),
                "is_coherent": True,
                "anomalies": [],
                "critical_missing_fields": [],
                "reextraction_recommended": False,
                "auditor_notes": "Document refinery output mapped successfully.",
            }
            processed_document = {
                "applicant_id": applicant_id,
                "loan_amount": mapped["loan_amount"],
                "annual_income": mapped["annual_income"],
                "loan_term_months": int(mapped["loan_term_months"]),
                "loan_amount_provenance": provenance["loan_amount"],
                "annual_income_provenance": provenance["annual_income"],
                "loan_term_months_provenance": provenance["loan_term_months"],
            }
            await self._record_node_execution(
                "assess_quality",
                ["refinery_result"],
                ["quality_assessment", "processed_document"],
                int((time.time() - t) * 1000),
            )
            return {
                **state,
                "processed_document": processed_document,
                "quality_assessment": quality_assessment,
                "quality_flags": [],
            }

        package_id = state.get("package_id") or f"docpkg-{state['application_id']}"
        extraction_results = list(state.get("extraction_results") or [])
        if not extraction_results:
            raise ValueError("No extraction results available for quality assessment")

        combined: dict[str, Any] = {}
        for result in extraction_results:
            facts = result.get("facts") or {}
            for key, value in facts.items():
                if value is not None and key not in combined:
                    combined[key] = value

        required_fields = ["total_revenue", "total_assets", "total_liabilities", "total_equity"]
        missing_fields = [field for field in required_fields if combined.get(field) in (None, "", [])]
        balance_sheet_balances = False
        balance_discrepancy = None
        assets = combined.get("total_assets")
        liabilities = combined.get("total_liabilities")
        equity = combined.get("total_equity")
        try:
            if assets is not None and liabilities is not None and equity is not None:
                assets_f = float(assets)
                liabilities_f = float(liabilities)
                equity_f = float(equity)
                expected = liabilities_f + equity_f
                balance_discrepancy = abs(assets_f - expected)
                balance_sheet_balances = balance_discrepancy <= max(1.0, abs(expected) * 0.05)
        except Exception:
            balance_sheet_balances = False

        system = (
            "You are a financial document quality analyst. Check internal consistency. "
            "Do NOT make credit decisions. Return JSON with keys: overall_confidence, "
            "is_coherent, anomalies, critical_missing_fields, reextraction_recommended, auditor_notes."
        )
        user = json.dumps(
            {
                "package_id": package_id,
                "application_id": state["application_id"],
                "combined_facts": combined,
                "missing_fields": missing_fields,
                "balance_sheet_balances": balance_sheet_balances,
                "balance_discrepancy_usd": balance_discrepancy,
            },
            default=str,
        )

        llm_data: dict[str, Any] = {}
        tok_in = tok_out = 0
        cost = 0.0
        try:
            content, tok_in, tok_out, cost = await self._call_llm(system, user, 512)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", content, re.DOTALL)
                parsed = json.loads(m.group(0)) if m else {}
            if isinstance(parsed, dict):
                llm_data = parsed
        except Exception:
            llm_data = {}
            tok_in = tok_out = 0
            cost = 0.0

        anomalies = list(llm_data.get("anomalies") or llm_data.get("warnings") or [])
        critical_missing_fields = list(llm_data.get("critical_missing_fields") or missing_fields)
        reextraction_recommended = bool(llm_data.get("reextraction_recommended", False) or critical_missing_fields or not balance_sheet_balances)
        overall_confidence = float(llm_data.get("overall_confidence") or llm_data.get("consistency_score") or (0.92 if balance_sheet_balances else 0.66))
        is_coherent = bool(llm_data.get("is_coherent", balance_sheet_balances and not critical_missing_fields))
        auditor_notes = str(llm_data.get("auditor_notes") or llm_data.get("summary") or "Document package reviewed for internal consistency.")

        assessment_event = QualityAssessmentCompleted(
            package_id=package_id,
            document_id=str((state.get("document_ids") or [package_id])[0]),
            overall_confidence=overall_confidence,
            is_coherent=is_coherent,
            anomalies=[str(a) for a in anomalies],
            critical_missing_fields=[str(f) for f in critical_missing_fields],
            reextraction_recommended=reextraction_recommended,
            auditor_notes=auditor_notes,
            assessed_at=datetime.utcnow(),
        ).to_store_dict()
        await self._append_with_retry(package_id, [assessment_event])

        await self._record_node_execution(
            "assess_quality",
            ["extraction_results"],
            ["quality_assessment"],
            int((time.time() - t) * 1000),
            tok_in,
            tok_out,
            cost,
        )
        return {
            **state,
            "quality_assessment": {
                "overall_confidence": overall_confidence,
                "is_coherent": is_coherent,
                "anomalies": [str(a) for a in anomalies],
                "critical_missing_fields": [str(f) for f in critical_missing_fields],
                "reextraction_recommended": reextraction_recommended,
                "auditor_notes": auditor_notes,
                "balance_sheet_balances": balance_sheet_balances,
                "balance_discrepancy_usd": balance_discrepancy,
            },
            "quality_flags": [str(f) for f in critical_missing_fields],
        }

    async def _node_write_output(self, state):
        t = time.time()
        app_id = state["application_id"]
        package_id = state.get("package_id") or f"docpkg-{app_id}"
        mode = state.get("processing_mode") or "legacy"
        quality = state.get("quality_assessment") or {}
        documents_processed = len(state.get("documents") or [])
        if mode in {"structured", "refinery"}:
            processed_document = dict(state.get("processed_document") or {})
            if not processed_document and state.get("structured_input"):
                structured = dict(state.get("structured_input") or {})
                processed_document = {
                    "applicant_id": str(structured.get("applicant_id") or app_id),
                    "loan_amount": self._coerce_float(structured.get("loan_amount") or structured.get("requested_amount_usd")) or 0.0,
                    "annual_income": self._coerce_float(structured.get("annual_income")) or 0.0,
                    "loan_term_months": int(structured.get("loan_term_months") or 0),
                }

            processed_event = {
                "event_type": "DOCUMENT_PROCESSED",
                "event_version": 1,
                "payload": processed_document,
            }
            events_to_write = [processed_event]
            positions = await self._append_with_retry(f"docpkg-{app_id}", events_to_write)
            events_written = [
                {"stream_id": f"docpkg-{app_id}", "event_type": "DOCUMENT_PROCESSED", "stream_position": positions[0] if positions else -1},
            ]
            await self._record_output_written(events_written, "Document processed successfully.")
            await self._record_node_execution(
                "write_output",
                ["quality_assessment", "processed_document"],
                ["events_written"],
                int((time.time() - t) * 1000),
            )
            return {**state, "output_events": events_written, "next_agent": "credit_analysis"}

        has_quality_flags = bool((quality.get("critical_missing_fields") or []) or (quality.get("anomalies") or []) or quality.get("reextraction_recommended"))
        quality_flag_count = len(quality.get("critical_missing_fields") or []) + len(quality.get("anomalies") or [])

        processed_document = state.get("processed_document")
        if not processed_document and state.get("structured_input"):
            structured = dict(state.get("structured_input") or {})
            processed_document = {
                "applicant_id": str(structured.get("applicant_id") or app_id),
                "loan_amount": self._coerce_float(structured.get("loan_amount") or structured.get("requested_amount_usd")) or 0.0,
                "annual_income": self._coerce_float(structured.get("annual_income")) or (self._coerce_float((state.get("extraction_results") or [{}])[0].get("facts", {}).get("total_revenue")) if state.get("extraction_results") else 0.0),
                "loan_term_months": int(structured.get("loan_term_months") or 0),
            }
        if processed_document:
            processed_event = {
                "event_type": "DOCUMENT_PROCESSED",
                "event_version": 1,
                "payload": processed_document,
            }
            await self._append_with_retry(f"docpkg-{app_id}", [processed_event])

        package_event = PackageReadyForAnalysis(
            package_id=package_id,
            application_id=app_id,
            documents_processed=documents_processed,
            has_quality_flags=has_quality_flags,
            quality_flag_count=quality_flag_count,
            ready_at=datetime.utcnow(),
        ).to_store_dict()
        credit_event = CreditAnalysisRequested(
            application_id=app_id,
            requested_at=datetime.utcnow(),
            requested_by="document_processing_agent",
            priority="NORMAL",
        ).to_store_dict()

        positions = await self._append_with_retry(f"docpkg-{app_id}", [package_event])
        credit_positions = await self._append_with_retry(f"loan-{app_id}", [credit_event])
        events_written = [
            {"stream_id": f"docpkg-{app_id}", "event_type": "PackageReadyForAnalysis", "stream_position": positions[0] if positions else -1},
            {"stream_id": f"loan-{app_id}", "event_type": "CreditAnalysisRequested", "stream_position": credit_positions[0] if credit_positions else -1},
        ]

        await self._record_output_written(
            events_written,
            f"Document package ready: {documents_processed} documents processed, quality_flags={quality_flag_count}.",
        )
        await self._record_node_execution(
            "write_output",
            ["quality_assessment", "processed_document"],
            ["events_written"],
            int((time.time() - t) * 1000),
        )
        return {**state, "output_events": events_written, "next_agent": "credit_analysis"}

    async def _extract_document_type(self, state, document_type: DocumentType, node_name: str):
        t = time.time()
        package_id = state.get("package_id") or f"docpkg-{state['application_id']}"
        documents = list(state.get("documents") or [])
        doc = next((item for item in documents if str(item.get("document_type")) == document_type.value), None)
        if not doc:
            raise ValueError(f"{document_type.value} document not found in package")

        start_event = ExtractionStarted(
            package_id=package_id,
            document_id=str(doc.get("document_id") or ""),
            document_type=document_type,
            pipeline_version="datagen-adapter",
            extraction_model=getattr(self.extraction_adapter, "__class__", type(self.extraction_adapter)).__name__,
            started_at=datetime.utcnow(),
        ).to_store_dict()
        await self._append_with_retry(package_id, [start_event])

        try:
            facts_raw = await self.extraction_adapter.extract(str(doc.get("file_path") or ""), document_type.value)
            facts_dict = _to_plain_dict(facts_raw)
            facts_model = FinancialFacts(**facts_dict)
            completed_event = ExtractionCompleted(
                package_id=package_id,
                document_id=str(doc.get("document_id") or ""),
                document_type=document_type,
                facts=facts_model,
                raw_text_length=len(json.dumps(facts_dict, default=str)),
                tables_extracted=1 if document_type == DocumentType.BALANCE_SHEET else 0,
                processing_ms=int((time.time() - t) * 1000),
                completed_at=datetime.utcnow(),
            ).to_store_dict()
            await self._append_with_retry(package_id, [completed_event])
            extracted_event = completed_event
            extraction_result = {
                "document_id": str(doc.get("document_id") or ""),
                "document_type": document_type.value,
                "facts": facts_model.model_dump(mode="json"),
            }
        except Exception as exc:
            failed_event = ExtractionFailed(
                package_id=package_id,
                document_id=str(doc.get("document_id") or ""),
                error_type=type(exc).__name__,
                error_message=str(exc),
                partial_facts=None,
                failed_at=datetime.utcnow(),
            ).to_store_dict()
            await self._append_with_retry(package_id, [failed_event])
            raise

        await self._record_tool_call(
            "datagen_extraction_adapter",
            f"{document_type.value}:{doc.get('file_path')}",
            extracted_event["event_type"],
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            node_name,
            ["documents"],
            ["extraction_results"],
            int((time.time() - t) * 1000),
        )
        return {**state, "extraction_results": (state.get("extraction_results") or []) + [extraction_result]}


# â”€â”€â”€ FRAUD DETECTION AGENT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class FraudState(TypedDict):
    application_id: str
    session_id: str
    applicant_id: str | None
    loan_amount_usd: float | None
    annual_income: float | None
    extracted_facts: dict | None
    registry_profile: dict | None
    historical_financials: list[dict] | None
    loan_history: list[dict] | None
    document_events: list[dict] | None
    fraud_signals: list[dict] | None
    rule_findings: dict | None
    ollama_assessment: dict | None
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
        validate_inputs â†’ load_document_facts â†’ cross_reference_registry â†’
        analyze_fraud_patterns â†’ write_output

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
        fraud_score > 0.60 â†’ recommendation = "DECLINE"
        fraud_score 0.30..0.60 â†’ "FLAG_FOR_REVIEW"
        fraud_score < 0.30 â†’ "PROCEED"

    LLM in _node_analyze():
        System: "You are a financial fraud analyst.
                 Given the cross-reference results, identify specific named anomalies.
                 For each anomaly: type, severity, evidence, affected_fields.
                 Compute a final fraud_score 0-1. Return FraudAssessment JSON."

    WHEN THIS WORKS:
        pytest tests/phase2/test_fraud_agent.py
          â†’ FraudScreeningCompleted event in fraud stream
          â†’ fraud_score between 0.0 and 1.0
          â†’ ComplianceCheckRequested on loan stream
          â†’ NARR-03 (crash recovery) test passes
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

    def _initial_state(
        self,
        application_id: str,
        recovery_context_text=None,
        recovery_pending_work=None,
        recovered_from_session_id=None,
        recovery_point=None,
        resume_after_sequence: int = -1,
        resume_state_snapshot: dict | None = None,
    ) -> FraudState:
        state: FraudState = FraudState(
            application_id=application_id, session_id=self.session_id, applicant_id=None,
            loan_amount_usd=None, annual_income=None,
            extracted_facts=None, registry_profile=None, historical_financials=None,
            loan_history=None, document_events=None, fraud_signals=None, fraud_score=None,
            rule_findings=None, ollama_assessment=None,
            risk_level=None, recommendation=None, anomalies=None,
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
            state.update(snapshot_state)  # type: ignore[arg-type]
        return state

    @staticmethod
    def _coerce_float(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _is_malformed_applicant_id(applicant_id: str | None) -> bool:
        if not applicant_id:
            return True
        return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", str(applicant_id)) is None

    def _build_rule_findings(self, state) -> dict[str, Any]:
        facts = state.get("extracted_facts") or {}
        profile = state.get("registry_profile") or {}
        history = state.get("historical_financials") or []
        loans = state.get("loan_history") or []
        applicant_id = state.get("applicant_id")

        loan_amount = self._coerce_float(
            state.get("loan_amount_usd")
            or facts.get("requested_amount_usd")
            or profile.get("requested_amount_usd")
        )
        annual_income = self._coerce_float(
            facts.get("annual_income")
            or facts.get("total_revenue")
            or (history[-1].get("total_revenue") if history else None)
        )
        dti_ratio = self._coerce_float(
            facts.get("dti_ratio")
            or facts.get("debt_to_income_ratio")
            or profile.get("dti_ratio")
        )
        prior_defaults = sum(1 for loan in loans if bool((loan or {}).get("default_occurred")))

        flags: list[str] = []
        if self._is_malformed_applicant_id(applicant_id):
            flags.append("missing_identity_fields")

        if loan_amount is None or loan_amount <= 0 or annual_income is None or annual_income <= 0:
            flags.append("implausible_values")
        elif loan_amount > 5 * annual_income:
            flags.append("income_loan_ratio_anomaly")

        if dti_ratio is not None and dti_ratio > 0.55:
            flags.append("dti_anomaly")

        if prior_defaults >= 2:
            flags.append("prior_default_history")

        return {
            "flags": flags,
            "risk_signals": {
                "loan_amount": loan_amount,
                "annual_income": annual_income,
                "dti_ratio": dti_ratio,
                "prior_defaults": prior_defaults,
                "applicant_id": applicant_id,
            },
            "flag_count": len(flags),
        }

    def _score_with_ollama(self, findings: dict[str, Any]) -> dict[str, Any]:
        fallback_reasoning = "Automated rule-based check only - Ollama unavailable"
        if OllamaClient is None:
            return {
                "score": 0,
                "confidence": "low",
                "reasoning": fallback_reasoning,
                "fallback": True,
            }

        try:
            client = OllamaClient()
            response = client.ask(
                (
                    "You are a fraud risk analyst for a commercial lending platform. "
                    "Given these rule-based findings from a loan application, assign a fraud risk "
                    "score from 0 to 100 and explain your reasoning. A score above 70 means high "
                    "fraud risk. Respond ONLY in valid JSON with no extra text: "
                    '{"score": int, "confidence": "low"|"medium"|"high", "reasoning": "string"}'
                ),
                json.dumps(findings, default=str),
            )
        except Exception as exc:
            return {
                "score": 0,
                "confidence": "low",
                "reasoning": fallback_reasoning,
                "fallback": True,
                "error": str(exc),
            }

        if not isinstance(response, dict):
            return {
                "score": 0,
                "confidence": "low",
                "reasoning": fallback_reasoning,
                "fallback": True,
            }

        if response.get("fallback"):
            return {
                "score": 0,
                "confidence": "low",
                "reasoning": fallback_reasoning,
                "fallback": True,
            }

        if response.get("parse_error"):
            return {
                "score": 0,
                "confidence": "low",
                "reasoning": str(response.get("raw") or fallback_reasoning),
                "fallback": False,
            }

        try:
            score = int(response.get("score", 0))
        except Exception:
            score = 0
        confidence = str(response.get("confidence") or "low").lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        reasoning = str(response.get("reasoning") or "")
        return {
            "score": score,
            "confidence": confidence,
            "reasoning": reasoning,
            "fallback": False,
        }

    @staticmethod
    def _derive_risk_level(score: int, flag_count: int) -> str:
        if score > 70 or flag_count >= 2:
            return "HIGH"
        if score >= 40 or flag_count == 1:
            return "MEDIUM"
        return "LOW"

    async def _node_validate_inputs(self, state):
        t = time.time()
        app_id = state["application_id"]
        loan_events = await self.store.load_stream(f"loan-{app_id}")
        if not loan_events:
            raise ValueError(f"No events found for loan-{app_id}")

        applicant_id = None
        loan_amount_usd = None
        fraud_requested = False
        for ev in loan_events:
            if ev["event_type"] == "ApplicationSubmitted":
                applicant_id = ev["payload"].get("applicant_id")
                loan_amount_usd = self._coerce_float(ev["payload"].get("requested_amount_usd"))
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
        return {**state, "applicant_id": applicant_id, "loan_amount_usd": loan_amount_usd}

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
        findings = self._build_rule_findings(state)
        ollama_result = self._score_with_ollama(findings)
        score = int(ollama_result.get("score") or 0)
        confidence = str(ollama_result.get("confidence") or "low").lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        reasoning = str(ollama_result.get("reasoning") or "")

        flags = list(findings.get("flags") or [])
        risk_signals = dict(findings.get("risk_signals") or {})
        flag_count = int(findings.get("flag_count") or 0)

        anomalies: list[dict[str, Any]] = []
        anomaly_descriptions = {
            "missing_identity_fields": (
                "Applicant identifier is missing or malformed.",
                ["applicant_id"],
            ),
            "implausible_values": (
                "Loan amount or annual income is non-positive.",
                ["loan_amount_usd", "annual_income"],
            ),
            "income_loan_ratio_anomaly": (
                "Requested loan exceeds five times annual income.",
                ["loan_amount_usd", "annual_income"],
            ),
            "dti_anomaly": (
                "Debt-to-income ratio exceeds the allowed threshold.",
                ["dti_ratio"],
            ),
            "prior_default_history": (
                "Two or more prior defaults were found in registry history.",
                ["loan_history"],
            ),
        }
        for flag in flags:
            description, fields = anomaly_descriptions.get(
                flag,
                (f"Rule flag raised: {flag}", []),
            )
            anomalies.append(
                {
                    "anomaly_type": flag,
                    "description": description,
                    "severity": "HIGH" if flag in {"implausible_values", "income_loan_ratio_anomaly", "prior_default_history"} else "MEDIUM",
                    "evidence": json.dumps(risk_signals, default=str),
                    "affected_fields": fields,
                }
            )

        fraud_flag_raised = score > 70 or flag_count >= 2
        event_name = "FRAUD_FLAG_RAISED" if fraud_flag_raised else "FRAUD_CHECK_PASSED"
        risk_level = self._derive_risk_level(score, flag_count)
        recommendation = "DECLINE" if fraud_flag_raised else ("FLAG_FOR_REVIEW" if risk_level == "MEDIUM" else "PROCEED")

        await self._record_node_execution(
            "analyze_fraud_patterns",
            ["extracted_facts", "registry_profile", "historical_financials", "loan_history"],
            ["fraud_score", "anomalies", "rule_findings", "ollama_assessment"],
            int((time.time() - t) * 1000),
        )
        return {
            **state,
            "fraud_score": round(score / 100.0, 4),
            "risk_level": risk_level,
            "recommendation": recommendation,
            "anomalies": anomalies,
            "fraud_signals": flags,
            "rule_findings": findings,
            "ollama_assessment": ollama_result,
            "output_events": list(state.get("output_events") or []) + [{"event_type": event_name}],
        }

    async def _node_write_output(self, state):
        t = time.time()
        app_id = state["application_id"]
        fraud_stream = f"fraud-{app_id}"
        loan_stream = f"loan-{app_id}"
        rule_findings = state.get("rule_findings") or {}
        ollama_assessment = state.get("ollama_assessment") or {}
        flags = list(rule_findings.get("flags") or [])
        risk_signals = dict(rule_findings.get("risk_signals") or {})
        flag_count = int(rule_findings.get("flag_count") or len(flags))
        ollama_score = int(ollama_assessment.get("score") or 0)
        confidence = str(ollama_assessment.get("confidence") or "low").lower()
        reasoning = str(ollama_assessment.get("reasoning") or "")
        fraud_flag_raised = ollama_score > 70 or flag_count >= 2
        event_name = "FRAUD_FLAG_RAISED" if fraud_flag_raised else "FRAUD_CHECK_PASSED"
        risk_level = state.get("risk_level") or self._derive_risk_level(ollama_score, flag_count)
        recommendation = state.get("recommendation") or ("DECLINE" if fraud_flag_raised else ("FLAG_FOR_REVIEW" if risk_level == "MEDIUM" else "PROCEED"))

        result_event = {
            "event_type": event_name,
            "event_version": 1,
            "payload": {
                "application_id": app_id,
                "session_id": state["session_id"],
                "flags": flags,
                "flag_count": flag_count,
                "risk_signals": risk_signals,
                "ollama_score": ollama_score,
                "confidence": confidence,
                "reasoning": reasoning,
            },
        }
        completion_event = {
            "event_type": "FRAUD_CHECK_COMPLETED",
            "event_version": 1,
            "payload": {
                "application_id": app_id,
                "session_id": state["session_id"],
                "flags": flags,
                "flag_count": flag_count,
                "risk_signals": risk_signals,
                "ollama_score": ollama_score,
                "confidence": confidence,
                "reasoning": reasoning,
                "risk_level": risk_level,
                "recommendation": recommendation,
            },
        }
        completed = FraudScreeningCompleted(
            application_id=app_id,
            session_id=state["session_id"],
            fraud_score=round(max(0, min(ollama_score, 100)) / 100.0, 4),
            risk_level=risk_level,
            anomalies_found=flag_count,
            recommendation=recommendation,
            screening_model_version=self.model,
            input_data_hash=self._sha(
                {
                    "flags": flags,
                    "risk_signals": risk_signals,
                    "ollama_score": ollama_score,
                    "confidence": confidence,
                    "reasoning": reasoning,
                }
            ),
            completed_at=datetime.utcnow(),
        ).to_store_dict()

        compliance_request = ComplianceCheckRequested(
            application_id=app_id,
            requested_at=datetime.utcnow(),
            triggered_by_event_id=state["session_id"],
            regulation_set_version=RULE_SET_VERSION,
            rules_to_evaluate=list(REGULATIONS.keys()),
        ).to_store_dict()

        fraud_positions = await self._append_with_retry(fraud_stream, [result_event, completion_event, completed])
        loan_positions = await self._append_with_retry(loan_stream, [compliance_request])

        events_written = [
            {"stream_id": fraud_stream, "event_type": event_name, "stream_position": fraud_positions[0] if fraud_positions else -1},
            {"stream_id": fraud_stream, "event_type": "FRAUD_CHECK_COMPLETED", "stream_position": fraud_positions[1] if len(fraud_positions) > 1 else -1},
            {"stream_id": fraud_stream, "event_type": "FraudScreeningCompleted", "stream_position": fraud_positions[2] if len(fraud_positions) > 2 else -1},
            {"stream_id": loan_stream, "event_type": "ComplianceCheckRequested", "stream_position": loan_positions[0] if loan_positions else -1},
        ]
        await self._record_output_written(
            events_written,
            f"Fraud: {risk_level} risk, score {ollama_score / 100.0:.2f}, recommendation {recommendation}. Compliance check triggered.",
        )
        await self._record_node_execution(
            "write_output",
            ["fraud_score", "anomalies", "rule_findings", "ollama_assessment"],
            ["events_written"],
            int((time.time() - t) * 1000),
        )
        return {**state, "output_events": events_written, "next_agent": "compliance", "next_agent_triggered": "compliance"}


# â”€â”€â”€ COMPLIANCE AGENT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    LLM not used in rule evaluation â€” only for human-readable evidence summaries.

    LangGraph nodes:
        validate_inputs â†’ load_company_profile â†’ evaluate_reg001 â†’ evaluate_reg002 â†’
        evaluate_reg003 â†’ evaluate_reg004 â†’ evaluate_reg005 â†’ evaluate_reg006 â†’ write_output

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
          â†’ ComplianceCheckCompleted with correct verdict
          â†’ NARR-04 (Montana REG-003 hard block): no DecisionRequested event,
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

    def _initial_state(
        self,
        application_id: str,
        recovery_context_text=None,
        recovery_pending_work=None,
        recovered_from_session_id=None,
        recovery_point=None,
        resume_after_sequence: int = -1,
        resume_state_snapshot: dict | None = None,
    ) -> ComplianceState:
        state = ComplianceState(
            application_id=application_id, session_id=self.session_id, applicant_id=None,
            company_profile=None, rule_results=[], has_hard_block=False,
            block_rule_id=None, errors=[], output_events=[], next_agent=None,
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
            state.update(snapshot_state)  # type: ignore[arg-type]
        return state

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


# â”€â”€â”€ DECISION ORCHESTRATOR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class OrchestratorState(TypedDict):
    application_id: str
    session_id: str
    upstream_events: dict[str, list[dict]]
    upstream_summary: dict | None
    ollama_result: dict | None
    final_decision: str | None
    final_event_type: str | None
    confidence: str | None
    reasoning: str | None
    safety_rule_applied: str | None
    missing_upstream_events: list[str]
    orchestration_failed_reason: str | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None
    next_agent_triggered: str | None


class DecisionOrchestratorAgent(BaseApexAgent):
    """
    Collects the completed outputs from the upstream agents, sends a structured
    summary to Ollama, and emits the final loan decision event.

    Upstream streams:
        docpkg-{id}: DOCUMENT_PROCESSED
        credit-{id}: CREDIT_ANALYSIS_COMPLETED
        fraud-{id}: FRAUD_CHECK_COMPLETED
        compliance-{id}: COMPLIANCE_PASSED or COMPLIANCE_FLAG_RAISED
    """

    def build_graph(self):
        g = StateGraph(OrchestratorState)
        g.add_node("validate_inputs", self._node_validate_inputs)
        g.add_node("load_credit_result", self._node_load_credit)
        g.add_node("load_fraud_result", self._node_load_fraud)
        g.add_node("load_compliance_result", self._node_load_compliance)
        g.add_node("synthesize_decision", self._node_synthesize)
        g.add_node("apply_hard_constraints", self._node_constraints)
        g.add_node("write_output", self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_conditional_edges(
            "validate_inputs",
            lambda s: "write_output" if s.get("missing_upstream_events") else "load_credit_result",
        )
        g.add_edge("load_credit_result", "load_fraud_result")
        g.add_edge("load_fraud_result", "load_compliance_result")
        g.add_edge("load_compliance_result", "synthesize_decision")
        g.add_edge("synthesize_decision", "apply_hard_constraints")
        g.add_edge("apply_hard_constraints", "write_output")
        g.add_edge("write_output", END)
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
    ) -> OrchestratorState:
        state = OrchestratorState(
            application_id=application_id,
            session_id=self.session_id,
            upstream_events={},
            upstream_summary=None,
            ollama_result=None,
            final_decision=None,
            final_event_type=None,
            confidence=None,
            reasoning=None,
            safety_rule_applied=None,
            missing_upstream_events=[],
            orchestration_failed_reason=None,
            errors=[],
            output_events=[],
            next_agent=None,
            next_agent_triggered=None,
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
            state.update(snapshot_state)  # type: ignore[arg-type]
        return state

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(float(value))
        except Exception:
            return None

    @staticmethod
    def _normalize_list(value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        return [value]

    @staticmethod
    def _latest_matching_event(events: list[dict], event_types: list[str]) -> dict | None:
        wanted = {str(item) for item in event_types}
        for event in reversed(events or []):
            if str(event.get("event_type")) in wanted:
                return dict(event)
        return None

    @staticmethod
    def _payload(event: dict | None) -> dict[str, Any]:
        if not event:
            return {}
        return _to_plain_dict(event.get("payload"))

    @staticmethod
    def _hard_gate_triggered(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().upper()
        return text not in {"", "NONE", "FALSE", "NO", "0"}

    def _build_upstream_summary(self, state: OrchestratorState) -> dict[str, Any]:
        document_event = state["upstream_events"].get("document", [])
        credit_event = state["upstream_events"].get("credit", [])
        fraud_event = state["upstream_events"].get("fraud", [])
        compliance_event = state["upstream_events"].get("compliance", [])

        document_completed = self._latest_matching_event(
            document_event,
            ["DOCUMENT_PROCESSED", "DocumentProcessed", "ExtractionCompleted"],
        )
        credit_completed = self._latest_matching_event(
            credit_event,
            ["CREDIT_ANALYSIS_COMPLETED", "CreditAnalysisCompleted"],
        )
        fraud_completed = self._latest_matching_event(
            fraud_event,
            ["FRAUD_CHECK_COMPLETED", "FRAUD_FLAG_RAISED", "FraudScreeningCompleted"],
        )
        compliance_completed = self._latest_matching_event(
            compliance_event,
            ["COMPLIANCE_PASSED", "COMPLIANCE_FLAG_RAISED", "ComplianceCheckCompleted"],
        )

        document_payload = self._payload(document_completed)
        credit_payload = self._payload(credit_completed)
        fraud_payload = self._payload(fraud_completed)
        compliance_payload = self._payload(compliance_completed)

        credit_decision = _to_plain_dict(credit_payload.get("decision"))
        credit_score = self._coerce_float(
            credit_payload.get("credit_score")
            or credit_payload.get("score")
            or credit_decision.get("credit_score")
        )
        if credit_score is None:
            credit_score = 0.0

        hard_gate = credit_payload.get("hard_gate") or credit_decision.get("hard_gate")
        factor_scores = _to_plain_dict(
            credit_payload.get("factor_scores")
            or credit_decision.get("factor_scores")
            or {}
        )

        fraud_flags = self._normalize_list(
            fraud_payload.get("flags")
            or fraud_payload.get("fraud_flags")
            or fraud_payload.get("anomalies")
        )
        fraud_score = self._coerce_int(
            fraud_payload.get("ollama_score")
            or fraud_payload.get("fraud_risk_score")
            or fraud_payload.get("score")
        )
        if fraud_score is None:
            fraud_score_float = self._coerce_float(fraud_payload.get("fraud_score"))
            if fraud_score_float is not None:
                fraud_score = int(round(fraud_score_float * 100))
        if fraud_score is None:
            fraud_score = 0
        fraud_confidence = str(
            fraud_payload.get("confidence")
            or fraud_payload.get("fraud_confidence")
            or "low"
        ).lower()

        compliance_outcome = str(
            compliance_payload.get("compliance_outcome")
            or compliance_payload.get("overall_verdict")
            or (compliance_completed or {}).get("event_type")
            or "UNKNOWN"
        ).upper()
        if compliance_outcome in {"CLEAR", "PASSED", "COMPLIANT"}:
            compliance_outcome = "COMPLIANCE_PASSED"
        elif compliance_outcome in {"BLOCKED", "FLAGGED", "CONDITIONAL"}:
            compliance_outcome = "COMPLIANCE_FLAG_RAISED"

        loan_amount = self._coerce_float(
            document_payload.get("loan_amount")
            or document_payload.get("requested_amount_usd")
            or document_payload.get("loan_amount_usd")
        )
        annual_income = self._coerce_float(
            document_payload.get("annual_income")
            or document_payload.get("total_revenue")
        )
        loan_term_months = self._coerce_int(
            document_payload.get("loan_term_months")
            or document_payload.get("term_months")
        )

        applicant_id = str(
            document_payload.get("applicant_id")
            or credit_payload.get("applicant_id")
            or ""
        )

        return {
            "application_id": state["application_id"],
            "applicant_id": applicant_id,
            "loan_amount": loan_amount,
            "annual_income": annual_income,
            "loan_term_months": loan_term_months,
            "credit_score": credit_score,
            "credit_policy_outcome": str(
                credit_payload.get("policy_outcome")
                or credit_decision.get("policy_outcome")
                or credit_payload.get("recommendation")
                or "UNKNOWN"
            ).upper(),
            "hard_gate": hard_gate,
            "fraud_flags": list(fraud_flags),
            "fraud_risk_score": int(max(0, min(fraud_score, 100))),
            "fraud_confidence": fraud_confidence,
            "compliance_outcome": compliance_outcome,
            "factor_scores": factor_scores,
        }

    async def _node_validate_inputs(self, state):
        t = time.time()
        app_id = state["application_id"]

        upstream_events = {
            "document": await self.store.load_stream(f"docpkg-{app_id}"),
            "credit": await self.store.load_stream(f"credit-{app_id}"),
            "fraud": await self.store.load_stream(f"fraud-{app_id}"),
            "compliance": await self.store.load_stream(f"compliance-{app_id}"),
        }
        missing: list[str] = []
        if self._latest_matching_event(upstream_events["document"], ["DOCUMENT_PROCESSED", "DocumentProcessed", "ExtractionCompleted"]) is None:
            missing.append("DOCUMENT_PROCESSED")
        if self._latest_matching_event(upstream_events["credit"], ["CREDIT_ANALYSIS_COMPLETED", "CreditAnalysisCompleted"]) is None:
            missing.append("CREDIT_ANALYSIS_COMPLETED")
        if self._latest_matching_event(upstream_events["fraud"], ["FRAUD_CHECK_COMPLETED", "FRAUD_FLAG_RAISED", "FraudScreeningCompleted"]) is None:
            missing.append("FRAUD_CHECK_COMPLETED")
        if self._latest_matching_event(upstream_events["compliance"], ["COMPLIANCE_PASSED", "COMPLIANCE_FLAG_RAISED", "ComplianceCheckCompleted"]) is None:
            missing.append("COMPLIANCE_OUTCOME")

        await self._record_tool_call(
            "load_streams",
            {
                "document": f"docpkg-{app_id}",
                "credit": f"credit-{app_id}",
                "fraud": f"fraud-{app_id}",
                "compliance": f"compliance-{app_id}",
            },
            {"missing": missing, "upstream_streams": list(upstream_events.keys())},
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            "validate_inputs",
            ["application_id"],
            ["upstream_events", "missing_upstream_events"],
            int((time.time() - t) * 1000),
        )
        return {
            **state,
            "upstream_events": upstream_events,
            "missing_upstream_events": missing,
            "orchestration_failed_reason": "missing_upstream_output" if missing else None,
        }

    async def _node_load_credit(self, state):
        t = time.time()
        summary = self._build_upstream_summary(state)
        await self._record_tool_call(
            "load_stream",
            f"credit-{state['application_id']}",
            {"credit_score": summary["credit_score"], "policy_outcome": summary["credit_policy_outcome"], "hard_gate": summary["hard_gate"]},
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            "load_credit_result",
            ["upstream_events"],
            ["upstream_summary"],
            int((time.time() - t) * 1000),
        )
        return {**state, "upstream_summary": summary}

    async def _node_load_fraud(self, state):
        t = time.time()
        summary = dict(state.get("upstream_summary") or self._build_upstream_summary(state))
        await self._record_tool_call(
            "load_stream",
            f"fraud-{state['application_id']}",
            {"fraud_risk_score": summary["fraud_risk_score"], "fraud_confidence": summary["fraud_confidence"], "fraud_flags": summary["fraud_flags"]},
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            "load_fraud_result",
            ["upstream_summary"],
            ["upstream_summary"],
            int((time.time() - t) * 1000),
        )
        return {**state, "upstream_summary": summary}

    async def _node_load_compliance(self, state):
        t = time.time()
        summary = dict(state.get("upstream_summary") or self._build_upstream_summary(state))
        await self._record_tool_call(
            "load_stream",
            f"compliance-{state['application_id']}",
            {"compliance_outcome": summary["compliance_outcome"]},
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            "load_compliance_result",
            ["upstream_summary"],
            ["upstream_summary"],
            int((time.time() - t) * 1000),
        )
        return {**state, "upstream_summary": summary}

    async def _node_synthesize(self, state):
        t = time.time()
        summary = dict(state.get("upstream_summary") or self._build_upstream_summary(state))
        system_prompt = (
            "You are a senior loan underwriting supervisor at Apex Financial Services. "
            "Given these structured assessments from four specialist agents, recommend a final decision "
            "for this commercial loan application. Consider all factors together. Respond ONLY in valid "
            "JSON with no extra text: {\"decision\": \"APPROVE\"|\"REJECT\"|\"MANUAL_REVIEW\", "
            "\"confidence\": \"low\"|\"medium\"|\"high\", \"reasoning\": \"string (3-4 sentences)\"}"
        )
        user_message = json.dumps(summary, default=str)

        if _ollama is None:
            ollama_result = {
                "decision": "MANUAL_REVIEW",
                "confidence": "low",
                "reasoning": "Orchestration fallback — Ollama unavailable",
                "fallback": True,
            }
        else:
            try:
                response = _ollama.ask(system_prompt=system_prompt, user_message=user_message)
                if not isinstance(response, dict) or response.get("fallback") or response.get("parse_error"):
                    ollama_result = {
                        "decision": "MANUAL_REVIEW",
                        "confidence": "low",
                        "reasoning": "Orchestration fallback — Ollama unavailable",
                        "fallback": True,
                    }
                else:
                    decision = str(response.get("decision") or "MANUAL_REVIEW").upper()
                    if decision not in {"APPROVE", "REJECT", "MANUAL_REVIEW"}:
                        decision = "MANUAL_REVIEW"
                    confidence = str(response.get("confidence") or "low").lower()
                    if confidence not in {"low", "medium", "high"}:
                        confidence = "low"
                    ollama_result = {
                        "decision": decision,
                        "confidence": confidence,
                        "reasoning": str(response.get("reasoning") or ""),
                        "fallback": False,
                    }
            except Exception:
                ollama_result = {
                    "decision": "MANUAL_REVIEW",
                    "confidence": "low",
                    "reasoning": "Orchestration fallback — Ollama unavailable",
                    "fallback": True,
                }

        await self._record_tool_call(
            "ollama.ask",
            system_prompt,
            ollama_result,
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            "synthesize_decision",
            ["upstream_summary"],
            ["ollama_result"],
            int((time.time() - t) * 1000),
        )
        return {**state, "upstream_summary": summary, "ollama_result": ollama_result}

    async def _node_constraints(self, state):
        t = time.time()
        summary = dict(state.get("upstream_summary") or self._build_upstream_summary(state))
        ollama_result = dict(state.get("ollama_result") or {})
        fallback = bool(ollama_result.get("fallback"))
        ollama_confidence = str(ollama_result.get("confidence") or "low").lower()
        ollama_decision = str(ollama_result.get("decision") or "MANUAL_REVIEW").upper()
        reasoning = str(ollama_result.get("reasoning") or "")

        final_event_type = None
        final_decision = None
        safety_rule_applied = None

        if fallback:
            final_event_type = "MANUAL_REVIEW_REQUIRED"
            final_decision = final_event_type
            safety_rule_applied = "ollama_unavailable"
            reasoning = "Orchestration fallback — Ollama unavailable"
            ollama_confidence = "low"
        elif self._hard_gate_triggered(summary.get("hard_gate")):
            final_event_type = "LOAN_REJECTED"
            final_decision = final_event_type
            safety_rule_applied = "hard_gate"
        elif int(summary.get("fraud_risk_score") or 0) > 70:
            final_event_type = "MANUAL_REVIEW_REQUIRED"
            final_decision = final_event_type
            safety_rule_applied = "fraud_risk_score_gt_70"
        elif ollama_confidence == "low":
            final_event_type = "MANUAL_REVIEW_REQUIRED"
            final_decision = final_event_type
            safety_rule_applied = "low_confidence"
        else:
            decision_map = {
                "APPROVE": "LOAN_APPROVED",
                "REJECT": "LOAN_REJECTED",
                "MANUAL_REVIEW": "MANUAL_REVIEW_REQUIRED",
            }
            final_event_type = decision_map.get(ollama_decision, "MANUAL_REVIEW_REQUIRED")
            final_decision = final_event_type

        await self._record_node_execution(
            "apply_hard_constraints",
            ["upstream_summary", "ollama_result"],
            ["final_decision", "final_event_type", "safety_rule_applied"],
            int((time.time() - t) * 1000),
        )
        return {
            **state,
            "upstream_summary": summary,
            "ollama_result": ollama_result,
            "final_decision": final_decision,
            "final_event_type": final_event_type,
            "confidence": ollama_confidence,
            "reasoning": reasoning,
            "safety_rule_applied": safety_rule_applied,
        }

    async def _node_write_output(self, state):
        t = time.time()
        app_id = state["application_id"]
        missing = list(state.get("missing_upstream_events") or [])

        if missing:
            failure_event = {
                "event_type": "ORCHESTRATION_FAILED",
                "event_version": 1,
                "payload": {
                    "application_id": app_id,
                    "reason": "missing_upstream_output",
                    "missing_events": missing,
                },
            }
            positions = await self._append_with_retry(f"loan-{app_id}", [failure_event])
            events_written = [
                {
                    "stream_id": f"loan-{app_id}",
                    "event_type": "ORCHESTRATION_FAILED",
                    "stream_position": positions[0] if positions else -1,
                }
            ]
            await self._record_output_written(events_written, "Orchestration failed: missing upstream output.")
            await self._record_node_execution(
                "write_output",
                ["missing_upstream_events"],
                ["events_written"],
                int((time.time() - t) * 1000),
            )
            return {
                **state,
                "output_events": events_written,
                "next_agent": None,
                "next_agent_triggered": None,
            }

        summary = dict(state.get("upstream_summary") or self._build_upstream_summary(state))
        final_event_type = str(state.get("final_event_type") or "MANUAL_REVIEW_REQUIRED")
        final_decision = str(state.get("final_decision") or final_event_type)
        confidence = str(state.get("confidence") or "low").lower()
        reasoning = str(state.get("reasoning") or "")
        safety_rule_applied = state.get("safety_rule_applied")

        orchestration_completed = {
            "event_type": "ORCHESTRATION_COMPLETED",
            "event_version": 1,
            "payload": {
                "application_id": app_id,
                "final_decision": final_decision,
                "confidence": confidence,
                "reasoning": reasoning,
                "safety_rule_applied": safety_rule_applied,
                "upstream_summary": summary,
            },
        }
        final_event = {
            "event_type": final_event_type,
            "event_version": 1,
            "payload": {
                "application_id": app_id,
                "confidence": confidence,
                "reasoning": reasoning,
                "safety_rule_applied": safety_rule_applied,
            },
        }

        positions = await self._append_with_retry(f"loan-{app_id}", [orchestration_completed, final_event])
        events_written = [
            {
                "stream_id": f"loan-{app_id}",
                "event_type": "ORCHESTRATION_COMPLETED",
                "stream_position": positions[0] if positions else -1,
            },
            {
                "stream_id": f"loan-{app_id}",
                "event_type": final_event_type,
                "stream_position": positions[1] if len(positions) > 1 else -1,
            },
        ]
        await self._record_output_written(
            events_written,
            f"Orchestration completed with {final_decision} using {safety_rule_applied or 'ollama_recommendation'}.",
        )
        await self._record_node_execution(
            "write_output",
            ["upstream_summary", "final_decision"],
            ["events_written"],
            int((time.time() - t) * 1000),
        )
        return {
            **state,
            "output_events": events_written,
            "next_agent": None,
            "next_agent_triggered": None,
        }
