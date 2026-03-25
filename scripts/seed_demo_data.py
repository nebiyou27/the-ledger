from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from datetime import date
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from ledger.event_store import EventStore


REGISTRY_SQL = """
CREATE SCHEMA IF NOT EXISTS applicant_registry;
CREATE TABLE IF NOT EXISTS applicant_registry.companies (
    company_id TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT NOT NULL,
    naics TEXT NOT NULL, jurisdiction TEXT NOT NULL, legal_type TEXT NOT NULL,
    founded_year INT NOT NULL, employee_count INT NOT NULL, ein TEXT NOT NULL UNIQUE,
    address_city TEXT NOT NULL, address_state TEXT NOT NULL,
    relationship_start DATE NOT NULL, account_manager TEXT NOT NULL,
    risk_segment TEXT NOT NULL CHECK (risk_segment IN ('LOW','MEDIUM','HIGH')),
    trajectory TEXT NOT NULL, submission_channel TEXT NOT NULL, ip_region TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS applicant_registry.financial_history (
    id SERIAL PRIMARY KEY, company_id TEXT NOT NULL REFERENCES applicant_registry.companies(company_id),
    fiscal_year INT NOT NULL, total_revenue NUMERIC(15,2) NOT NULL, gross_profit NUMERIC(15,2) NOT NULL,
    operating_expenses NUMERIC(15,2) NOT NULL, operating_income NUMERIC(15,2) NOT NULL,
    ebitda NUMERIC(15,2) NOT NULL, depreciation_amortization NUMERIC(15,2) NOT NULL,
    interest_expense NUMERIC(15,2) NOT NULL, income_before_tax NUMERIC(15,2) NOT NULL,
    tax_expense NUMERIC(15,2) NOT NULL, net_income NUMERIC(15,2) NOT NULL,
    total_assets NUMERIC(15,2) NOT NULL, current_assets NUMERIC(15,2) NOT NULL,
    cash_and_equivalents NUMERIC(15,2) NOT NULL, accounts_receivable NUMERIC(15,2) NOT NULL,
    inventory NUMERIC(15,2) NOT NULL, total_liabilities NUMERIC(15,2) NOT NULL,
    current_liabilities NUMERIC(15,2) NOT NULL, long_term_debt NUMERIC(15,2) NOT NULL,
    total_equity NUMERIC(15,2) NOT NULL, operating_cash_flow NUMERIC(15,2) NOT NULL,
    investing_cash_flow NUMERIC(15,2) NOT NULL, financing_cash_flow NUMERIC(15,2) NOT NULL,
    free_cash_flow NUMERIC(15,2) NOT NULL, debt_to_equity NUMERIC(8,4),
    current_ratio NUMERIC(8,4), debt_to_ebitda NUMERIC(8,4),
    interest_coverage_ratio NUMERIC(8,4), gross_margin NUMERIC(8,4),
    ebitda_margin NUMERIC(8,4), net_margin NUMERIC(8,4),
    balance_sheet_check BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (company_id, fiscal_year)
);
CREATE TABLE IF NOT EXISTS applicant_registry.compliance_flags (
    id SERIAL PRIMARY KEY, company_id TEXT NOT NULL REFERENCES applicant_registry.companies(company_id),
    flag_type TEXT NOT NULL CHECK (flag_type IN ('AML_WATCH','SANCTIONS_REVIEW','PEP_LINK')),
    severity TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH')),
    is_active BOOLEAN NOT NULL, added_date DATE NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS applicant_registry.loan_relationships (
    id SERIAL PRIMARY KEY, company_id TEXT NOT NULL REFERENCES applicant_registry.companies(company_id),
    loan_amount NUMERIC(15,2) NOT NULL, loan_year INT NOT NULL,
    was_repaid BOOLEAN NOT NULL, default_occurred BOOLEAN NOT NULL, note TEXT
);
"""

INDUSTRY_NAICS = {
    "logistics": "484110",
    "manufacturing": "332710",
    "technology": "541511",
    "healthcare": "621111",
    "retail": "441110",
    "professional_services": "541200",
    "construction": "236220",
    "other": "519130",
}

YEAR_FACTORS = {2022: 0.82, 2023: 0.92, 2024: 1.0}
MONEY_FIELDS = {
    "total_revenue",
    "gross_profit",
    "operating_expenses",
    "operating_income",
    "ebitda",
    "depreciation_amortization",
    "interest_expense",
    "income_before_tax",
    "tax_expense",
    "net_income",
    "total_assets",
    "current_assets",
    "cash_and_equivalents",
    "accounts_receivable",
    "inventory",
    "total_liabilities",
    "current_liabilities",
    "long_term_debt",
    "total_equity",
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "free_cash_flow",
}
RATIO_FIELDS = {
    "debt_to_equity",
    "current_ratio",
    "debt_to_ebitda",
    "interest_coverage_ratio",
    "gross_margin",
    "ebitda_margin",
    "net_margin",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


async def _connect_with_retry(store: EventStore, attempts: int = 10, delay_seconds: int = 2) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await store.connect()
            return
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            print(f"PostgreSQL not ready yet ({attempt}/{attempts}): {exc}")
            await asyncio.sleep(delay_seconds)

    raise SystemExit(f"Unable to connect to PostgreSQL after {attempts} attempts: {last_error}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_int(seed: str, lower: int, upper: int) -> int:
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    span = upper - lower + 1
    return lower + int.from_bytes(digest[:4], "big") % span


def _company_record(profile: dict[str, Any]) -> dict[str, Any]:
    company_id = str(profile["company_id"])
    digest_seed = f"{company_id}:{profile.get('name', '')}"
    cities = [
        "Phoenix",
        "Austin",
        "Denver",
        "Raleigh",
        "Columbus",
        "Milwaukee",
        "Portland",
        "Nashville",
        "Atlanta",
        "Salt Lake City",
    ]
    submission_channels = ["web", "mobile", "branch"]
    ip_regions = ["US-East", "US-West", "US-Central", "US-South"]
    founded_year = _stable_int(digest_seed + ":founded", 2000, 2020)
    employee_count = _stable_int(digest_seed + ":employees", 8, 420)
    address_city = cities[_stable_int(digest_seed + ":city", 0, len(cities) - 1)]
    relationship_start = date(_stable_int(digest_seed + ":rel-year", 2019, 2024), 1, 15)
    return {
        "company_id": company_id,
        "name": str(profile.get("name", company_id)),
        "industry": str(profile.get("industry", "other")),
        "naics": INDUSTRY_NAICS.get(str(profile.get("industry", "other")), INDUSTRY_NAICS["other"]),
        "jurisdiction": str(profile.get("jurisdiction", "DE")),
        "legal_type": str(profile.get("legal_type", "LLC")),
        "founded_year": founded_year,
        "employee_count": employee_count,
        "ein": f"{_stable_int(digest_seed + ':ein1', 10, 99):02d}-{_stable_int(digest_seed + ':ein2', 1000000, 9999999):07d}",
        "address_city": address_city,
        "address_state": str(profile.get("jurisdiction", "DE")),
        "relationship_start": relationship_start,
        "account_manager": "Synthetic Intake",
        "risk_segment": str(profile.get("risk_segment", "MEDIUM")),
        "trajectory": str(profile.get("trajectory", "STABLE")),
        "submission_channel": submission_channels[_stable_int(digest_seed + ":channel", 0, len(submission_channels) - 1)],
        "ip_region": ip_regions[_stable_int(digest_seed + ":region", 0, len(ip_regions) - 1)],
    }


def _read_financial_summary(documents_dir: Path, company_id: str) -> dict[str, float]:
    csv_path = documents_dir / company_id / "financial_summary.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing financial summary CSV for {company_id}: {csv_path}")

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        values: dict[str, float] = {}
        for row in reader:
            field = str(row["field"])
            if field == "fiscal_year":
                continue
            value = row.get("value")
            if value in (None, ""):
                continue
            try:
                values[field] = float(value)
            except (TypeError, ValueError):
                # Some summary rows are booleans or other non-numeric checks.
                continue
    return values


def _financial_rows(base_row: dict[str, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, factor in YEAR_FACTORS.items():
        row: dict[str, Any] = {"fiscal_year": year}
        for field, value in base_row.items():
            if field in MONEY_FIELDS:
                row[field] = round(value * factor, 2)
            elif field in RATIO_FIELDS:
                row[field] = round(value, 4)
        row["balance_sheet_check"] = True
        rows.append(row)
    return rows


async def _seed_registry(store: EventStore, root: Path) -> None:
    pool = store._require_pool()
    async with pool.acquire() as conn:
        await conn.execute(REGISTRY_SQL)
        await conn.execute(
            """
            TRUNCATE TABLE
                applicant_registry.loan_relationships,
                applicant_registry.compliance_flags,
                applicant_registry.financial_history,
                applicant_registry.companies
            RESTART IDENTITY CASCADE
            """
        )

        profiles = _load_json(root / "data" / "applicant_profiles.json")
        documents_dir = root / "documents"

        async with conn.transaction():
            for profile in profiles:
                company = _company_record(profile)
                await conn.execute(
                    """
                    INSERT INTO applicant_registry.companies
                        (company_id, name, industry, naics, jurisdiction, legal_type,
                         founded_year, employee_count, ein, address_city, address_state,
                         relationship_start, account_manager, risk_segment, trajectory,
                         submission_channel, ip_region)
                    VALUES
                        ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                    ON CONFLICT (company_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        industry = EXCLUDED.industry,
                        naics = EXCLUDED.naics,
                        jurisdiction = EXCLUDED.jurisdiction,
                        legal_type = EXCLUDED.legal_type,
                        founded_year = EXCLUDED.founded_year,
                        employee_count = EXCLUDED.employee_count,
                        ein = EXCLUDED.ein,
                        address_city = EXCLUDED.address_city,
                        address_state = EXCLUDED.address_state,
                        relationship_start = EXCLUDED.relationship_start,
                        account_manager = EXCLUDED.account_manager,
                        risk_segment = EXCLUDED.risk_segment,
                        trajectory = EXCLUDED.trajectory,
                        submission_channel = EXCLUDED.submission_channel,
                        ip_region = EXCLUDED.ip_region
                    """,
                    company["company_id"],
                    company["name"],
                    company["industry"],
                    company["naics"],
                    company["jurisdiction"],
                    company["legal_type"],
                    company["founded_year"],
                    company["employee_count"],
                    company["ein"],
                    company["address_city"],
                    company["address_state"],
                    company["relationship_start"],
                    company["account_manager"],
                    company["risk_segment"],
                    company["trajectory"],
                    company["submission_channel"],
                    company["ip_region"],
                )

                base_financials = _read_financial_summary(documents_dir, company["company_id"])
                for financial_row in _financial_rows(base_financials):
                    await conn.execute(
                        """
                        INSERT INTO applicant_registry.financial_history
                            (company_id, fiscal_year, total_revenue, gross_profit, operating_expenses,
                             operating_income, ebitda, depreciation_amortization, interest_expense,
                             income_before_tax, tax_expense, net_income, total_assets, current_assets,
                             cash_and_equivalents, accounts_receivable, inventory, total_liabilities,
                             current_liabilities, long_term_debt, total_equity, operating_cash_flow,
                             investing_cash_flow, financing_cash_flow, free_cash_flow, debt_to_equity,
                             current_ratio, debt_to_ebitda, interest_coverage_ratio, gross_margin,
                             ebitda_margin, net_margin, balance_sheet_check)
                        VALUES
                            ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33)
                        ON CONFLICT (company_id, fiscal_year) DO UPDATE SET
                            total_revenue = EXCLUDED.total_revenue,
                            gross_profit = EXCLUDED.gross_profit,
                            operating_expenses = EXCLUDED.operating_expenses,
                            operating_income = EXCLUDED.operating_income,
                            ebitda = EXCLUDED.ebitda,
                            depreciation_amortization = EXCLUDED.depreciation_amortization,
                            interest_expense = EXCLUDED.interest_expense,
                            income_before_tax = EXCLUDED.income_before_tax,
                            tax_expense = EXCLUDED.tax_expense,
                            net_income = EXCLUDED.net_income,
                            total_assets = EXCLUDED.total_assets,
                            current_assets = EXCLUDED.current_assets,
                            cash_and_equivalents = EXCLUDED.cash_and_equivalents,
                            accounts_receivable = EXCLUDED.accounts_receivable,
                            inventory = EXCLUDED.inventory,
                            total_liabilities = EXCLUDED.total_liabilities,
                            current_liabilities = EXCLUDED.current_liabilities,
                            long_term_debt = EXCLUDED.long_term_debt,
                            total_equity = EXCLUDED.total_equity,
                            operating_cash_flow = EXCLUDED.operating_cash_flow,
                            investing_cash_flow = EXCLUDED.investing_cash_flow,
                            financing_cash_flow = EXCLUDED.financing_cash_flow,
                            free_cash_flow = EXCLUDED.free_cash_flow,
                            debt_to_equity = EXCLUDED.debt_to_equity,
                            current_ratio = EXCLUDED.current_ratio,
                            debt_to_ebitda = EXCLUDED.debt_to_ebitda,
                            interest_coverage_ratio = EXCLUDED.interest_coverage_ratio,
                            gross_margin = EXCLUDED.gross_margin,
                            ebitda_margin = EXCLUDED.ebitda_margin,
                            net_margin = EXCLUDED.net_margin,
                            balance_sheet_check = EXCLUDED.balance_sheet_check
                        """,
                        company["company_id"],
                        financial_row["fiscal_year"],
                        financial_row["total_revenue"],
                        financial_row["gross_profit"],
                        financial_row["operating_expenses"],
                        financial_row["operating_income"],
                        financial_row["ebitda"],
                        financial_row["depreciation_amortization"],
                        financial_row["interest_expense"],
                        financial_row["income_before_tax"],
                        financial_row["tax_expense"],
                        financial_row["net_income"],
                        financial_row["total_assets"],
                        financial_row["current_assets"],
                        financial_row["cash_and_equivalents"],
                        financial_row["accounts_receivable"],
                        financial_row["inventory"],
                        financial_row["total_liabilities"],
                        financial_row["current_liabilities"],
                        financial_row["long_term_debt"],
                        financial_row["total_equity"],
                        financial_row["operating_cash_flow"],
                        financial_row["investing_cash_flow"],
                        financial_row["financing_cash_flow"],
                        financial_row["free_cash_flow"],
                        financial_row["debt_to_equity"],
                        financial_row["current_ratio"],
                        financial_row["debt_to_ebitda"],
                        financial_row["interest_coverage_ratio"],
                        financial_row["gross_margin"],
                        financial_row["ebitda_margin"],
                        financial_row["net_margin"],
                        financial_row["balance_sheet_check"],
                    )

                for flag in profile.get("compliance_flags", []) or []:
                    await conn.execute(
                        """
                        INSERT INTO applicant_registry.compliance_flags
                            (company_id, flag_type, severity, is_active, added_date, note)
                        VALUES ($1,$2,$3,$4,$5,$6)
                        """,
                        company["company_id"],
                        flag.get("flag_type"),
                        flag.get("severity"),
                        bool(flag.get("is_active", False)),
                        date.fromisoformat(str(flag.get("added_date"))),
                        flag.get("note"),
                    )


async def _seed_events(store: EventStore, root: Path) -> int:
    pool = store._require_pool()
    event_count = await pool.fetchval("SELECT COUNT(*) FROM events")
    if int(event_count or 0) > 0:
        return 0

    seed_path = root / "data" / "seed_events.jsonl"
    if not seed_path.exists():
        raise FileNotFoundError(f"Missing seed event file: {seed_path}")

    stream_versions: dict[str, int] = {}
    imported = 0
    with seed_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            stream_id = str(record["stream_id"])
            expected_version = stream_versions.get(stream_id, -1)
            await store.append(
                stream_id,
                [
                    {
                        "event_type": record["event_type"],
                        "event_version": record.get("event_version", 1),
                        "payload": record.get("payload", {}),
                        "recorded_at": record.get("recorded_at"),
                    }
                ],
                expected_version=expected_version,
                metadata={"seed": True, "source": "data/seed_events.jsonl"},
            )
            stream_versions[stream_id] = expected_version + 1
            imported += 1

    return imported


async def _seed_projection_checkpoints(store: EventStore) -> None:
    pool = store._require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for name in ["application_summary", "agent_performance", "compliance_audit", "manual_reviews"]:
                await conn.execute(
                    """
                    INSERT INTO projection_checkpoints (projection_name, last_position, updated_at)
                    VALUES ($1, 0, NOW())
                    ON CONFLICT (projection_name) DO NOTHING
                    """,
                    name,
                )


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DB_URL")
    if not db_url:
        raise SystemExit("Set DATABASE_URL or TEST_DB_URL first.")

    repo_root = _repo_root()
    store = EventStore(db_url)
    await _connect_with_retry(store)
    try:
        await store.initialize_schema()
        await _seed_registry(store, repo_root)
        imported_events = await _seed_events(store, repo_root)
        await _seed_projection_checkpoints(store)
        print(f"Seeded registry data and {imported_events} events into {db_url}")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
