from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledger.registry.client import ApplicantRegistryClient


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return FakeAcquire(self._conn)


class FakeConn:
    def __init__(self):
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.next_fetchrow = None
        self.next_fetch = []

    async def fetchrow(self, query, *params):
        self.fetchrow_calls.append((query, params))
        return self.next_fetchrow

    async def fetch(self, query, *params):
        self.fetch_calls.append((query, params))
        return self.next_fetch


@pytest.mark.asyncio
async def test_get_company_returns_profile():
    conn = FakeConn()
    conn.next_fetchrow = {
        "company_id": "COMP-001",
        "name": "Acme Inc",
        "industry": "manufacturing",
        "naics": "332710",
        "jurisdiction": "TX",
        "legal_type": "LLC",
        "founded_year": 2014,
        "employee_count": 72,
        "risk_segment": "MEDIUM",
        "trajectory": "GROWTH",
        "submission_channel": "web",
        "ip_region": "US-Central",
    }
    client = ApplicantRegistryClient(FakePool(conn))

    company = await client.get_company("COMP-001")

    assert company is not None
    assert company.company_id == "COMP-001"
    assert company.name == "Acme Inc"
    assert company.employee_count == 72


@pytest.mark.asyncio
async def test_get_company_missing_returns_none():
    conn = FakeConn()
    conn.next_fetchrow = None
    client = ApplicantRegistryClient(FakePool(conn))

    company = await client.get_company("MISSING")

    assert company is None


@pytest.mark.asyncio
async def test_get_financial_history_maps_values_and_orders():
    conn = FakeConn()
    conn.next_fetch = [
        {
            "fiscal_year": 2023,
            "total_revenue": Decimal("1000.10"),
            "gross_profit": Decimal("500.50"),
            "operating_income": Decimal("100.00"),
            "ebitda": Decimal("120.00"),
            "net_income": Decimal("80.00"),
            "total_assets": Decimal("1500.00"),
            "total_liabilities": Decimal("600.00"),
            "total_equity": Decimal("900.00"),
            "long_term_debt": Decimal("300.00"),
            "cash_and_equivalents": Decimal("120.00"),
            "current_assets": Decimal("500.00"),
            "current_liabilities": Decimal("250.00"),
            "accounts_receivable": Decimal("140.00"),
            "inventory": Decimal("80.00"),
            "debt_to_equity": Decimal("0.6667"),
            "current_ratio": Decimal("2.0000"),
            "debt_to_ebitda": Decimal("5.0000"),
            "interest_coverage_ratio": Decimal("3.2500"),
            "gross_margin": Decimal("0.5005"),
            "ebitda_margin": Decimal("0.1200"),
            "net_margin": Decimal("0.0800"),
        }
    ]
    client = ApplicantRegistryClient(FakePool(conn))

    rows = await client.get_financial_history("COMP-001", years=[2023])

    assert len(rows) == 1
    fy = rows[0]
    assert fy.fiscal_year == 2023
    assert fy.total_revenue == 1000.10
    assert fy.interest_coverage_ratio == 3.25
    assert "fiscal_year = ANY" in conn.fetch_calls[0][0]
    assert conn.fetch_calls[0][1] == ("COMP-001", [2023])


@pytest.mark.asyncio
async def test_get_compliance_flags_active_only_filter_and_date_format():
    conn = FakeConn()
    conn.next_fetch = [
        {
            "flag_type": "AML_WATCH",
            "severity": "MEDIUM",
            "is_active": True,
            "added_date": date(2025, 1, 10),
            "note": None,
        }
    ]
    client = ApplicantRegistryClient(FakePool(conn))

    flags = await client.get_compliance_flags("COMP-001", active_only=True)

    assert len(flags) == 1
    assert flags[0].added_date == "2025-01-10"
    assert flags[0].note == ""
    assert "is_active = TRUE" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_get_loan_relationships_returns_plain_dicts():
    conn = FakeConn()
    conn.next_fetch = [
        {
            "id": 9,
            "loan_amount": Decimal("250000.00"),
            "loan_year": 2022,
            "was_repaid": True,
            "default_occurred": False,
            "note": "closed",
        }
    ]
    client = ApplicantRegistryClient(FakePool(conn))

    loans = await client.get_loan_relationships("COMP-001")

    assert loans == [
        {
            "id": 9,
            "loan_amount": 250000.0,
            "loan_year": 2022,
            "was_repaid": True,
            "default_occurred": False,
            "note": "closed",
        }
    ]
