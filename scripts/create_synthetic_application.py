from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from datagen.company_generator import INDUSTRIES
from datagen.excel_generator import generate_financial_excel
from datagen.pdf_generator import (
    generate_application_proposal_pdf,
    generate_balance_sheet_pdf,
    generate_income_statement_pdf,
)
from ledger.event_store import EventStore
from ledger.registry.client import ApplicantRegistryClient
from ledger.schema.events import (
    ApplicationSubmitted,
    DocumentAdded,
    DocumentFormat,
    DocumentType,
    DocumentUploadRequested,
    DocumentUploaded,
    LoanPurpose,
    PackageCreated,
)


def _default_db_url() -> str:
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DB_URL")
        or "postgresql://postgres:apex@localhost:5432/apex_ledger"
    )


def _pick_loan_purpose(industry: str) -> str:
    return INDUSTRIES.get(industry, INDUSTRIES["other"])["purposes"][0]


def _normalize_value(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def _normalize_row(row: dict) -> dict:
    return {key: _normalize_value(value) for key, value in row.items()}


def _next_application_id(existing: set[str]) -> str:
    idx = 30
    while True:
        app_id = f"APEX-{idx:04d}"
        if app_id not in existing:
            return app_id
        idx += 1


async def _load_company_context(registry_pool, registry: ApplicantRegistryClient, company_id: str) -> SimpleNamespace:
    company = await registry.get_company(company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found in applicant registry")

    async with registry_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT fiscal_year, total_revenue, gross_profit, operating_expenses,
                   operating_income, ebitda, depreciation_amortization,
                   interest_expense, income_before_tax, tax_expense, net_income,
                   total_assets, current_assets, cash_and_equivalents,
                   accounts_receivable, inventory, total_liabilities,
                   current_liabilities, long_term_debt, total_equity,
                   operating_cash_flow, investing_cash_flow, financing_cash_flow,
                   free_cash_flow, debt_to_equity, current_ratio, debt_to_ebitda,
                   interest_coverage_ratio, gross_margin, ebitda_margin, net_margin,
                   balance_sheet_check
            FROM applicant_registry.financial_history
            WHERE company_id = $1
            ORDER BY fiscal_year ASC
            """,
            company_id,
        )
    history = [_normalize_row(dict(row)) for row in rows]
    flags = await registry.get_compliance_flags(company_id)
    loans = await registry.get_loan_relationships(company_id)

    return SimpleNamespace(
        company_id=company.company_id,
        name=company.name,
        industry=company.industry,
        naics=company.naics,
        jurisdiction=company.jurisdiction,
        legal_type=company.legal_type,
        founded_year=company.founded_year,
        employee_count=company.employee_count,
        risk_segment=company.risk_segment,
        trajectory=company.trajectory,
        submission_channel=company.submission_channel,
        ip_region=company.ip_region,
        ein=f"synthetic-{company.company_id.lower()}",
        address_city="Unknown",
        address_state=company.jurisdiction,
        relationship_start="2023-01-01",
        account_manager="Synthetic Intake",
        financials=history,
        compliance_flags=[asdict(flag) for flag in flags],
        loan_relationships=loans,
        loan_purposes=INDUSTRIES.get(company.industry, INDUSTRIES["other"])["purposes"],
    )


async def create_application(args: argparse.Namespace) -> int:
    db_url = args.db_url or _default_db_url()
    store = EventStore(db_url)
    await store.connect()
    registry_pool = None
    try:
        await store.initialize_schema()
        import asyncpg

        registry_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
        registry = ApplicantRegistryClient(registry_pool)
        company = await _load_company_context(registry_pool, registry, args.company_id)

        existing_loan_streams = set()
        async for event in store.load_all(from_position=0, batch_size=500, event_types=["ApplicationSubmitted"]):
            stream_id = str(event.get("stream_id") or "")
            if stream_id.startswith("loan-"):
                existing_loan_streams.add(stream_id.replace("loan-", "", 1))

        application_id = args.application_id or _next_application_id(existing_loan_streams)
        loan_purpose = args.loan_purpose or _pick_loan_purpose(company.industry)
        requested_amount = float(args.requested_amount) if args.requested_amount else float(
            round(float(company.financials[-1]["total_revenue"]) * 0.22, -3)
        )

        docs_dir = Path("documents") / company.company_id
        docs_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = docs_dir / f"{application_id}_proposal.pdf"
        income_path = docs_dir / f"{application_id}_income_stmt_2024.pdf"
        balance_path = docs_dir / f"{application_id}_balance_sheet_2024.pdf"
        workbook_path = docs_dir / f"{application_id}_financials.xlsx"

        generate_application_proposal_pdf(company, application_id, requested_amount, loan_purpose, str(proposal_path))
        generate_income_statement_pdf(company, 2024, str(income_path), variant="clean")
        generate_balance_sheet_pdf(company, 2024, str(balance_path), variant="clean")
        generate_financial_excel(company, str(workbook_path))

        submitted_at = datetime.utcnow()
        requested_at = submitted_at + timedelta(minutes=2)
        package_at = submitted_at + timedelta(minutes=3)

        loan_stream = f"loan-{application_id}"
        docpkg_stream = f"docpkg-{application_id}"

        loan_events = [
            ApplicationSubmitted(
                application_id=application_id,
                applicant_id=company.company_id,
                requested_amount_usd=Decimal(str(requested_amount)),
                loan_purpose=LoanPurpose(loan_purpose),
                loan_term_months=36,
                submission_channel=company.submission_channel,
                contact_email=f"{company.company_id.lower()}@synthetic.example",
                contact_name=company.name.split()[0] if company.name else "Applicant",
                submitted_at=submitted_at,
                application_reference=application_id,
            ).to_store_dict(),
            DocumentUploadRequested(
                application_id=application_id,
                required_document_types=[
                    DocumentType.APPLICATION_PROPOSAL,
                    DocumentType.INCOME_STATEMENT,
                    DocumentType.BALANCE_SHEET,
                ],
                deadline=submitted_at + timedelta(days=7),
                requested_by="synthetic_generator",
            ).to_store_dict(),
            DocumentUploaded(
                application_id=application_id,
                document_id=f"doc-{uuid4().hex[:8]}",
                document_type=DocumentType.APPLICATION_PROPOSAL,
                document_format=DocumentFormat.PDF,
                filename=proposal_path.name,
                file_path=str(proposal_path),
                file_size_bytes=proposal_path.stat().st_size,
                file_hash=uuid4().hex[:16],
                fiscal_year=None,
                uploaded_at=requested_at,
                uploaded_by="synthetic_generator",
            ).to_store_dict(),
            DocumentUploaded(
                application_id=application_id,
                document_id=f"doc-{uuid4().hex[:8]}",
                document_type=DocumentType.INCOME_STATEMENT,
                document_format=DocumentFormat.PDF,
                filename=income_path.name,
                file_path=str(income_path),
                file_size_bytes=income_path.stat().st_size,
                file_hash=uuid4().hex[:16],
                fiscal_year=2024,
                uploaded_at=requested_at + timedelta(minutes=5),
                uploaded_by="synthetic_generator",
            ).to_store_dict(),
            DocumentUploaded(
                application_id=application_id,
                document_id=f"doc-{uuid4().hex[:8]}",
                document_type=DocumentType.BALANCE_SHEET,
                document_format=DocumentFormat.PDF,
                filename=balance_path.name,
                file_path=str(balance_path),
                file_size_bytes=balance_path.stat().st_size,
                file_hash=uuid4().hex[:16],
                fiscal_year=2024,
                uploaded_at=requested_at + timedelta(minutes=10),
                uploaded_by="synthetic_generator",
            ).to_store_dict(),
        ]

        docpkg_events = [
            PackageCreated(
                package_id=application_id,
                application_id=application_id,
                required_documents=[
                    DocumentType.APPLICATION_PROPOSAL,
                    DocumentType.INCOME_STATEMENT,
                    DocumentType.BALANCE_SHEET,
                ],
                created_at=package_at,
            ).to_store_dict(),
        ]

        for event in loan_events:
            await store.append(loan_stream, [event], expected_version=await store.stream_version(loan_stream))

        for event in docpkg_events:
            await store.append(docpkg_stream, [event], expected_version=await store.stream_version(docpkg_stream))

        for uploaded in loan_events[2:]:
            payload = uploaded["payload"]
            await store.append(
                docpkg_stream,
                [
                    DocumentAdded(
                        package_id=application_id,
                        document_id=payload["document_id"],
                        document_type=DocumentType(payload["document_type"]),
                        document_format=DocumentFormat(payload["document_format"]),
                        file_hash=payload["file_hash"],
                        added_at=datetime.utcnow(),
                    ).to_store_dict()
                ],
                expected_version=await store.stream_version(docpkg_stream),
            )

        print(f"Created synthetic application {application_id} for {company.company_id}")
        print(f"  Loan stream: {loan_stream}")
        print(f"  Document package: {docpkg_stream}")
        print(f"  Files:")
        print(f"    {proposal_path}")
        print(f"    {income_path}")
        print(f"    {balance_path}")
        print(f"    {workbook_path}")
        return 0
    finally:
        await store.close()
        if registry_pool is not None:
            await registry_pool.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create one synthetic loan application for a company.")
    parser.add_argument("--company-id", default="COMP-001", help="Applicant company id, default COMP-001.")
    parser.add_argument("--application-id", default=None, help="Application id to use, default next APEX number.")
    parser.add_argument("--requested-amount", type=float, default=None, help="Requested loan amount.")
    parser.add_argument("--loan-purpose", default=None, help="Loan purpose, default from industry.")
    parser.add_argument("--db-url", default=None, help="Database URL, defaults to DATABASE_URL/TEST_DB_URL.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(create_application(args)))


if __name__ == "__main__":
    main()
