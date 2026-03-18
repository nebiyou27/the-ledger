"""
ledger/registry/client.py -- Applicant Registry read-only client
===============================================================

This client reads from the applicant_registry schema in PostgreSQL.
It is READ-ONLY. No agent or event store component ever writes here.
The Applicant Registry is the external CRM seeded by datagen/generate_all.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

try:
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - optional for non-db unit tests
    asyncpg = None


@dataclass
class CompanyProfile:
    company_id: str
    name: str
    industry: str
    naics: str
    jurisdiction: str
    legal_type: str
    founded_year: int
    employee_count: int
    risk_segment: str
    trajectory: str
    submission_channel: str
    ip_region: str


@dataclass
class FinancialYear:
    fiscal_year: int
    total_revenue: float
    gross_profit: float
    operating_income: float
    ebitda: float
    net_income: float
    total_assets: float
    total_liabilities: float
    total_equity: float
    long_term_debt: float
    cash_and_equivalents: float
    current_assets: float
    current_liabilities: float
    accounts_receivable: float
    inventory: float
    debt_to_equity: float
    current_ratio: float
    debt_to_ebitda: float
    interest_coverage_ratio: float
    gross_margin: float
    ebitda_margin: float
    net_margin: float


@dataclass
class ComplianceFlag:
    flag_type: str
    severity: str
    is_active: bool
    added_date: str
    note: str


class ApplicantRegistryClient:
    """
    READ-ONLY access to the Applicant Registry.
    Agents call these methods to get company profiles and historical data.
    Never write to this database from the event store system.
    """

    def __init__(self, pool: Any):
        self._pool = pool

    async def get_company(self, company_id: str) -> CompanyProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT company_id, name, industry, naics, jurisdiction, legal_type,
                       founded_year, employee_count, risk_segment, trajectory,
                       submission_channel, ip_region
                FROM applicant_registry.companies
                WHERE company_id = $1
                """,
                company_id,
            )

        if row is None:
            return None

        return CompanyProfile(
            company_id=row["company_id"],
            name=row["name"],
            industry=row["industry"],
            naics=row["naics"],
            jurisdiction=row["jurisdiction"],
            legal_type=row["legal_type"],
            founded_year=int(row["founded_year"]),
            employee_count=int(row["employee_count"]),
            risk_segment=row["risk_segment"],
            trajectory=row["trajectory"],
            submission_channel=row["submission_channel"],
            ip_region=row["ip_region"],
        )

    async def get_financial_history(
        self,
        company_id: str,
        years: list[int] | None = None,
    ) -> list[FinancialYear]:
        async with self._pool.acquire() as conn:
            if years is None:
                rows = await conn.fetch(
                    """
                    SELECT fiscal_year, total_revenue, gross_profit, operating_income,
                           ebitda, net_income, total_assets, total_liabilities,
                           total_equity, long_term_debt, cash_and_equivalents,
                           current_assets, current_liabilities, accounts_receivable,
                           inventory, debt_to_equity, current_ratio, debt_to_ebitda,
                           interest_coverage_ratio, gross_margin, ebitda_margin,
                           net_margin
                    FROM applicant_registry.financial_history
                    WHERE company_id = $1
                    ORDER BY fiscal_year ASC
                    """,
                    company_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT fiscal_year, total_revenue, gross_profit, operating_income,
                           ebitda, net_income, total_assets, total_liabilities,
                           total_equity, long_term_debt, cash_and_equivalents,
                           current_assets, current_liabilities, accounts_receivable,
                           inventory, debt_to_equity, current_ratio, debt_to_ebitda,
                           interest_coverage_ratio, gross_margin, ebitda_margin,
                           net_margin
                    FROM applicant_registry.financial_history
                    WHERE company_id = $1
                      AND fiscal_year = ANY($2::int[])
                    ORDER BY fiscal_year ASC
                    """,
                    company_id,
                    years,
                )

        return [
            FinancialYear(
                fiscal_year=int(row["fiscal_year"]),
                total_revenue=_to_float(row["total_revenue"]),
                gross_profit=_to_float(row["gross_profit"]),
                operating_income=_to_float(row["operating_income"]),
                ebitda=_to_float(row["ebitda"]),
                net_income=_to_float(row["net_income"]),
                total_assets=_to_float(row["total_assets"]),
                total_liabilities=_to_float(row["total_liabilities"]),
                total_equity=_to_float(row["total_equity"]),
                long_term_debt=_to_float(row["long_term_debt"]),
                cash_and_equivalents=_to_float(row["cash_and_equivalents"]),
                current_assets=_to_float(row["current_assets"]),
                current_liabilities=_to_float(row["current_liabilities"]),
                accounts_receivable=_to_float(row["accounts_receivable"]),
                inventory=_to_float(row["inventory"]),
                debt_to_equity=_to_float(row["debt_to_equity"]),
                current_ratio=_to_float(row["current_ratio"]),
                debt_to_ebitda=_to_float(row["debt_to_ebitda"]),
                interest_coverage_ratio=_to_float(row["interest_coverage_ratio"]),
                gross_margin=_to_float(row["gross_margin"]),
                ebitda_margin=_to_float(row["ebitda_margin"]),
                net_margin=_to_float(row["net_margin"]),
            )
            for row in rows
        ]

    async def get_compliance_flags(
        self,
        company_id: str,
        active_only: bool = False,
    ) -> list[ComplianceFlag]:
        async with self._pool.acquire() as conn:
            if active_only:
                rows = await conn.fetch(
                    """
                    SELECT flag_type, severity, is_active, added_date, note
                    FROM applicant_registry.compliance_flags
                    WHERE company_id = $1
                      AND is_active = TRUE
                    ORDER BY added_date DESC, id DESC
                    """,
                    company_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT flag_type, severity, is_active, added_date, note
                    FROM applicant_registry.compliance_flags
                    WHERE company_id = $1
                    ORDER BY added_date DESC, id DESC
                    """,
                    company_id,
                )

        return [
            ComplianceFlag(
                flag_type=row["flag_type"],
                severity=row["severity"],
                is_active=bool(row["is_active"]),
                added_date=_to_date_iso(row["added_date"]),
                note=row["note"] or "",
            )
            for row in rows
        ]

    async def get_loan_relationships(self, company_id: str) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, loan_amount, loan_year, was_repaid, default_occurred, note
                FROM applicant_registry.loan_relationships
                WHERE company_id = $1
                ORDER BY loan_year DESC, id DESC
                """,
                company_id,
            )

        return [
            {
                "id": int(row["id"]),
                "loan_amount": _to_float(row["loan_amount"]),
                "loan_year": int(row["loan_year"]),
                "was_repaid": bool(row["was_repaid"]),
                "default_occurred": bool(row["default_occurred"]),
                "note": row["note"] or "",
            }
            for row in rows
        ]


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _to_date_iso(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
