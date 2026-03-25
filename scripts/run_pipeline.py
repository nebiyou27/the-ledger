from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from ledger.agents.credit_analysis_agent import CreditAnalysisAgent
from ledger.agents.extraction_adapter import DatagenExtractionAdapter, ExtractionAdapter
from ledger.agents.llm_adapter import MockLLMClient, OllamaClient
from ledger.agents.stub_agents import (
    ComplianceAgent,
    DecisionOrchestratorAgent,
    DocumentProcessingAgent,
    FraudDetectionAgent,
)
from ledger.event_store import EventStore
from ledger.registry.client import ApplicantRegistryClient
from ledger.schema.events import FraudScreeningRequested


SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_events.jsonl"
DEFAULT_PHASE_ORDER = ["document", "credit", "fraud", "compliance", "decision"]
BOOTSTRAP_EVENT_TYPES = {
    "ApplicationSubmitted",
    "DocumentUploadRequested",
    "PackageCreated",
    "DocumentUploaded",
    "DocumentAdded",
}


class SeedBackedExtractionAdapter(ExtractionAdapter):
    """Prefer app-specific seed facts, then fall back to datagen synthetic extraction."""

    def __init__(self, seed_path: Path, fallback: ExtractionAdapter | None = None):
        self.seed_path = seed_path
        self.fallback = fallback or DatagenExtractionAdapter(seed_path.parent)
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    async def extract(self, file_path: str, document_type: str) -> dict[str, Any]:
        app_id = _extract_app_id(file_path)
        if app_id:
            cached = self._lookup_seed_facts(app_id, document_type)
            if cached is not None:
                return cached
        return await self.fallback.extract(file_path, document_type)

    def _lookup_seed_facts(self, app_id: str, document_type: str) -> dict[str, Any] | None:
        key = (app_id, document_type)
        if key in self._cache:
            return dict(self._cache[key])

        if not self.seed_path.exists():
            return None

        target_stream = f"docpkg-{app_id}"
        try:
            with self.seed_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    record = json.loads(line)
                    if record.get("stream_id") != target_stream:
                        continue
                    if record.get("event_type") != "ExtractionCompleted":
                        continue
                    payload = record.get("payload") or {}
                    if str(payload.get("document_type") or "") != document_type:
                        continue
                    facts = payload.get("facts") or {}
                    if isinstance(facts, dict) and facts:
                        self._cache[key] = dict(facts)
                        return dict(facts)
        except Exception:
            return None
        return None


class RegistryBackedExtractionAdapter(ExtractionAdapter):
    """Prefer registry-backed synthetic facts for locally generated documents."""

    def __init__(
        self,
        registry: ApplicantRegistryClient,
        seed_path: Path,
        fallback: ExtractionAdapter | None = None,
    ):
        self.registry = registry
        self.seed_path = seed_path
        self.fallback = fallback or SeedBackedExtractionAdapter(seed_path)

    async def extract(self, file_path: str, document_type: str) -> dict[str, Any]:
        company_id = _extract_company_id(file_path)
        if company_id:
            try:
                company = await self.registry.get_company(company_id)
                if company is not None:
                    history = await self.registry.get_financial_history(company_id, years=[2022, 2023, 2024])
                    latest = history[-1] if history else None
                    if latest is not None and document_type in {"income_statement", "balance_sheet"}:
                        return _financial_year_to_facts(latest, company_id, document_type)
            except Exception:
                pass
        return await self.fallback.extract(file_path, document_type)


def _extract_app_id(file_path: str) -> str | None:
    match = re.search(r"APEX-\d{4}", file_path or "")
    return match.group(0) if match else None


def _extract_company_id(file_path: str) -> str | None:
    match = re.search(r"COMP-\d{3}", file_path or "")
    return match.group(0) if match else None


def _financial_year_to_facts(financial_year: Any, company_id: str, document_type: str) -> dict[str, Any]:
    data = {
        "company_id": company_id,
        "document_type": document_type,
        "fiscal_year": getattr(financial_year, "fiscal_year", None),
        "total_revenue": getattr(financial_year, "total_revenue", None),
        "gross_profit": getattr(financial_year, "gross_profit", None),
        "operating_income": getattr(financial_year, "operating_income", None),
        "ebitda": getattr(financial_year, "ebitda", None),
        "depreciation_amortization": 0.0,
        "interest_expense": 0.0,
        "income_before_tax": None,
        "tax_expense": None,
        "net_income": getattr(financial_year, "net_income", None),
        "total_assets": getattr(financial_year, "total_assets", None),
        "current_assets": getattr(financial_year, "current_assets", None),
        "cash_and_equivalents": getattr(financial_year, "cash_and_equivalents", None),
        "accounts_receivable": getattr(financial_year, "accounts_receivable", None),
        "inventory": getattr(financial_year, "inventory", None),
        "total_liabilities": getattr(financial_year, "total_liabilities", None),
        "current_liabilities": getattr(financial_year, "current_liabilities", None),
        "long_term_debt": getattr(financial_year, "long_term_debt", None),
        "total_equity": getattr(financial_year, "total_equity", None),
        "operating_cash_flow": 0.0,
        "investing_cash_flow": 0.0,
        "financing_cash_flow": 0.0,
        "free_cash_flow": 0.0,
        "debt_to_equity": getattr(financial_year, "debt_to_equity", None),
        "current_ratio": getattr(financial_year, "current_ratio", None),
        "debt_to_ebitda": getattr(financial_year, "debt_to_ebitda", None),
        "interest_coverage": getattr(financial_year, "interest_coverage_ratio", None),
        "gross_margin": getattr(financial_year, "gross_margin", None),
        "net_margin": getattr(financial_year, "net_margin", None),
        "fiscal_year_end": f"{getattr(financial_year, 'fiscal_year', 2024)}-12-31",
        "currency": "USD",
        "gaap_compliant": True,
        "field_confidence": {
            "total_revenue": 0.95,
            "net_income": 0.95,
            "total_assets": 0.95,
        },
        "page_references": {
            "total_revenue": "page 1, table 1, row 1",
            "net_income": "page 1, table 1, row 9",
        },
        "extraction_notes": [],
        "balance_sheet_balances": True,
        "balance_discrepancy_usd": 0.0,
    }
    return data


def _extract_applicant_id_from_stream(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("event_type") == "ApplicationSubmitted":
            payload = event.get("payload") or {}
            applicant_id = payload.get("applicant_id")
            if applicant_id:
                return str(applicant_id)
    return None


async def _load_seed_bootstrap_events(app_id: str, docs_root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    positions: dict[str, int] = defaultdict(lambda: -1)

    if not SEED_PATH.exists():
        raise FileNotFoundError(f"Seed file not found: {SEED_PATH}")

    with SEED_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            stream_id = str(record.get("stream_id") or "")
            if app_id not in stream_id:
                continue
            if record.get("event_type") not in BOOTSTRAP_EVENT_TYPES:
                continue

            event = dict(record)
            payload = dict(event.get("payload") or {})
            if event.get("event_type") == "DocumentUploaded":
                original_path = str(payload.get("file_path") or "")
                relative_path = Path(original_path)
                materialized_path = docs_root / relative_path
                materialized_path.parent.mkdir(parents=True, exist_ok=True)
                if not materialized_path.exists():
                    materialized_path.write_bytes(
                        f"Placeholder document for {app_id} / {payload.get('document_type')}\n".encode("utf-8")
                    )
                payload["file_path"] = str(materialized_path.resolve())
                event["payload"] = payload

            events.append(event)
            positions[stream_id] += 1

    return events, dict(positions)


async def ensure_document_files(events: list[dict[str, Any]]) -> list[Path]:
    created: list[Path] = []
    seen: set[Path] = set()
    for event in events:
        if event.get("event_type") != "DocumentUploaded":
            continue
        payload = event.get("payload") or {}
        file_path = Path(str(payload.get("file_path") or ""))
        if not str(file_path):
            continue
        if file_path in seen:
            continue
        seen.add(file_path)
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(
                f"Placeholder document for {payload.get('application_id')} / {payload.get('document_type')}\n".encode(
                    "utf-8"
                )
            )
            created.append(file_path)
    return created


async def bootstrap_application(
    store: EventStore,
    app_id: str,
    docs_root: Path,
) -> bool:
    loan_stream = f"loan-{app_id}"
    if await store.stream_version(loan_stream) >= 0:
        return False

    bootstrap_events, _ = await _load_seed_bootstrap_events(app_id, docs_root)
    if not bootstrap_events:
        raise ValueError(f"No bootstrap seed events found for {app_id}")

    per_stream_version: dict[str, int] = defaultdict(lambda: -1)
    for event in bootstrap_events:
        stream_id = str(event["stream_id"])
        payload_event = {
            "event_type": event["event_type"],
            "event_version": event.get("event_version", 1),
            "payload": event.get("payload", {}),
        }
        expected_version = per_stream_version[stream_id]
        await store.append(
            stream_id,
            [payload_event],
            expected_version=expected_version,
        )
        per_stream_version[stream_id] = expected_version + 1

    return True


async def _load_stream_summary(store: EventStore, stream_id: str) -> list[str]:
    events = await store.load_stream(stream_id)
    return [f"{event.get('stream_position')}:{event.get('event_type')}" for event in events]


async def _agent_done(store: EventStore, stream_id: str, event_type: str) -> bool:
    events = await store.load_stream(stream_id)
    return any(event.get("event_type") == event_type for event in events)


async def run_pipeline(args: argparse.Namespace) -> int:
    db_url = args.db_url or os.environ.get("DATABASE_URL") or os.environ.get("TEST_DB_URL")
    if not db_url:
        raise SystemExit("Set DATABASE_URL or TEST_DB_URL first, or pass --db-url.")

    store = EventStore(db_url)
    await store.connect()
    registry_pool = None
    try:
        await store.initialize_schema()

        import asyncpg

        registry_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
        registry = ApplicantRegistryClient(registry_pool)

        docs_root = Path(tempfile.mkdtemp(prefix=f"the-ledger-{args.app}-docs-"))
        bootstrapped = await bootstrap_application(store, args.app, docs_root)

        loan_stream = f"loan-{args.app}"
        loan_events = await store.load_stream(loan_stream)
        created_docs = await ensure_document_files(loan_events)
        applicant_id = _extract_applicant_id_from_stream(loan_events)
        if applicant_id:
            company = await registry.get_company(applicant_id)
            if company is None:
                print(f"Warning: applicant {applicant_id} not found in registry.")

        llm = _build_llm(args)
        extraction_adapter = RegistryBackedExtractionAdapter(registry, SEED_PATH)

        phases = DEFAULT_PHASE_ORDER if args.phase == "all" else [args.phase]
        phase_map = {
            "document": (
                DocumentProcessingAgent,
                "CreditAnalysisRequested",
                f"docpkg-{args.app}",
                "PackageReadyForAnalysis",
            ),
            "credit": (
                CreditAnalysisAgent,
                "FraudScreeningRequested",
                f"credit-{args.app}",
                "CreditAnalysisCompleted",
            ),
            "fraud": (
                FraudDetectionAgent,
                "ComplianceCheckRequested",
                f"fraud-{args.app}",
                "FraudScreeningCompleted",
            ),
            "compliance": (
                ComplianceAgent,
                "DecisionRequested",
                f"compliance-{args.app}",
                "ComplianceCheckCompleted",
            ),
            "decision": (
                DecisionOrchestratorAgent,
                None,
                f"loan-{args.app}",
                "DecisionGenerated",
            ),
        }

        if bootstrapped:
            print(f"Bootstrapped seed events for {args.app} from {SEED_PATH.name} into {docs_root}")
        if created_docs:
            print(f"Materialized {len(created_docs)} placeholder documents for existing upload events.")

        for phase in phases:
            agent_cls, trigger_event, stream_name, completion_event = phase_map[phase]
            if completion_event and await _agent_done(store, stream_name, completion_event):
                print(f"[{phase}] already completed, skipping")
                continue

            if phase == "decision":
                has_request = await _agent_done(store, loan_stream, "DecisionRequested")
                has_decline = await _agent_done(store, loan_stream, "ApplicationDeclined")
                if not has_request:
                    if has_decline:
                        print("[decision] skipped because compliance already declined the application")
                        continue
                    print("[decision] skipped because DecisionRequested is not present yet")
                    continue

            if phase == "document":
                agent = agent_cls(
                    agent_id="agent-document_processing",
                    agent_type="document_processing",
                    store=store,
                    registry=registry,
                    llm=llm,
                    extraction_adapter=extraction_adapter,
                )
            elif phase == "credit":
                agent = agent_cls(
                    agent_id="agent-credit_analysis",
                    agent_type="credit_analysis",
                    store=store,
                    registry=registry,
                    llm=llm,
                )
            elif phase == "fraud":
                agent = agent_cls(
                    agent_id="agent-fraud_detection",
                    agent_type="fraud_detection",
                    store=store,
                    registry=registry,
                    llm=llm,
                )
            elif phase == "compliance":
                agent = agent_cls(
                    agent_id="agent-compliance",
                    agent_type="compliance",
                    store=store,
                    registry=registry,
                    llm=llm,
                )
            else:
                agent = agent_cls(
                    agent_id="agent-decision_orchestrator",
                    agent_type="decision_orchestrator",
                    store=store,
                    registry=registry,
                    llm=llm,
                )

            print(f"[{phase}] running...")
            try:
                await agent.process_application(args.app)
            except Exception as exc:
                print(f"[{phase}] failed: {exc}")
                return 1

            if phase == "credit":
                loan_stream_id = f"loan-{args.app}"
                has_fraud_request = await _agent_done(store, loan_stream_id, "FraudScreeningRequested")
                has_credit_approval = await _agent_done(store, loan_stream_id, "LOAN_APPROVED")
                if has_credit_approval and not has_fraud_request:
                    expected_version = await store.stream_version(loan_stream_id)
                    await store.append(
                        loan_stream_id,
                        [
                            FraudScreeningRequested(
                                application_id=args.app,
                                requested_at=datetime.now(timezone.utc),
                                triggered_by_event_id=f"credit-{args.app}",
                            ).to_store_dict()
                        ],
                        expected_version=expected_version,
                    )
                    print("[credit] synthesized FraudScreeningRequested handoff")

            summary_streams = [
                f"loan-{args.app}",
                f"docpkg-{args.app}",
                f"credit-{args.app}",
                f"fraud-{args.app}",
                f"compliance-{args.app}",
            ]
            print(f"[{phase}] completed")
            for stream_id in summary_streams:
                tail = await _load_stream_summary(store, stream_id)
                if tail:
                    print(f"  {stream_id}: {tail[-4:]}")

        final_loan_events = await store.load_stream(f"loan-{args.app}")
        if final_loan_events:
            print("\nFinal loan stream events:")
            for event in final_loan_events[-8:]:
                print(f"  {event['stream_position']}: {event['event_type']}")

        return 0
    finally:
        await store.close()
        if registry_pool is not None:
            await registry_pool.close()


def _build_llm(args: argparse.Namespace):
    if args.llm == "mock":
        return MockLLMClient()
    return OllamaClient(model=args.model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a single loan application through the Apex agent pipeline.")
    parser.add_argument("--app", required=True, help="Application id, e.g. APEX-0007")
    parser.add_argument(
        "--phase",
        default="all",
        choices=["document", "credit", "fraud", "compliance", "decision", "all"],
        help="Run one phase or the full pipeline.",
    )
    parser.add_argument("--db-url", default=None, help="Override DATABASE_URL / TEST_DB_URL.")
    parser.add_argument(
        "--llm",
        default="mock",
        choices=["mock", "openrouter"],
        help="Choose the LLM backend used by the agents.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENROUTER_MODEL", os.environ.get("OLLAMA_MODEL", "google/gemini-2.5-flash")),
        help="Model name used by the OpenAI-compatible LLM backend.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_pipeline(args)))


if __name__ == "__main__":
    main()
