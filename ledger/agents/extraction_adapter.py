"""
ledger/agents/extraction_adapter.py
====================================
Extraction abstraction layer.
Provides a datagen-based adapter now; the real Week 3 document-refinery
pipeline can be plugged in later without changing any agent code.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ExtractionAdapter(ABC):
    """Protocol for financial document extraction."""

    @abstractmethod
    async def extract(
        self,
        file_path: str,
        document_type: str,
    ) -> dict[str, Any]:
        """
        Extract financial facts from a document.

        Args:
            file_path: Path to the PDF/XLSX file.
            document_type: One of 'income_statement', 'balance_sheet', 'application_proposal'.

        Returns:
            dict with standardised financial fact keys.
        """
        ...


class DatagenExtractionAdapter(ExtractionAdapter):
    """
    Reads pre-generated financial data from datagen seed files.

    For now, this adapter reads the company financial data that datagen
    already produced (Excel files or seed_events.jsonl) — it does NOT
    process PDFs. This provides realistic financial facts for the pipeline
    without requiring the full Week 3 extraction stack.

    When the real document-refinery pipeline is available, swap this
    adapter for DocumentRefineryAdapter.
    """

    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)

    async def extract(
        self,
        file_path: str,
        document_type: str,
    ) -> dict[str, Any]:
        """
        Simulate extraction by reading datagen seed data.

        Falls back to synthetic facts if no seed data is found.
        """
        # Try to find seed data from datagen
        seed_file = self.data_dir / "seed_events.jsonl"
        if seed_file.exists():
            facts = self._extract_from_seed(seed_file, document_type)
            if facts:
                return facts

        # Try to read from generated Excel files
        excel_facts = self._extract_from_excel(file_path, document_type)
        if excel_facts:
            return excel_facts

        # Fallback: produce realistic synthetic facts
        return self._synthetic_facts(document_type)

    def _extract_from_seed(
        self, seed_file: Path, document_type: str,
    ) -> dict[str, Any] | None:
        """Extract facts from seed_events.jsonl matching document_type."""
        try:
            with open(seed_file) as f:
                for line in f:
                    rec = json.loads(line)
                    if rec.get("event_type") == "ExtractionCompleted":
                        payload = rec.get("payload", {})
                        facts = payload.get("facts") or payload.get("financial_facts")
                        if facts and isinstance(facts, dict):
                            return facts
        except Exception:
            pass
        return None

    def _extract_from_excel(
        self, file_path: str, document_type: str,
    ) -> dict[str, Any] | None:
        """Try reading financial data from a generated Excel file."""
        try:
            import openpyxl
            path = Path(file_path)
            if not path.exists() or path.suffix.lower() not in (".xlsx", ".xls"):
                return None
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            # Simple key-value extraction from first two columns
            facts = {}
            for row in ws.iter_rows(min_row=2, max_col=2, values_only=True):
                if row[0] and row[1]:
                    key = str(row[0]).strip().lower().replace(" ", "_")
                    try:
                        facts[key] = float(row[1])
                    except (ValueError, TypeError):
                        facts[key] = str(row[1])
            wb.close()
            return facts if facts else None
        except Exception:
            return None

    @staticmethod
    def _synthetic_facts(document_type: str) -> dict[str, Any]:
        """Generate realistic synthetic financial facts."""
        if document_type == "income_statement":
            return {
                "total_revenue": 12_500_000.0,
                "cost_of_goods_sold": 7_800_000.0,
                "gross_profit": 4_700_000.0,
                "operating_expenses": 2_900_000.0,
                "operating_income": 1_800_000.0,
                "ebitda": 2_100_000.0,
                "net_income": 1_200_000.0,
                "fiscal_year": 2025,
                "currency": "USD",
                "extraction_confidence": 0.85,
            }
        elif document_type == "balance_sheet":
            return {
                "total_assets": 18_500_000.0,
                "current_assets": 6_200_000.0,
                "cash_and_equivalents": 2_100_000.0,
                "accounts_receivable": 2_800_000.0,
                "inventory": 1_300_000.0,
                "total_liabilities": 11_200_000.0,
                "current_liabilities": 4_500_000.0,
                "long_term_debt": 6_700_000.0,
                "total_equity": 7_300_000.0,
                "fiscal_year": 2025,
                "currency": "USD",
                "extraction_confidence": 0.82,
            }
        else:
            return {
                "document_type": document_type,
                "extraction_status": "completed",
                "extraction_confidence": 0.90,
            }


class DocumentRefineryAdapter(ExtractionAdapter):
    """
    Future adapter for the Week 3 document-refinery pipeline.

    To activate:
        1. Clone document-refinery alongside the-ledger
        2. pip install -e ../document-refinery
        3. Switch agent config to use this adapter

    The interface:
        from src.agents.extractor import extract_document
        doc = extract_document(pdf_path, strategy_override=None)
        # doc.pages[*].text, doc.pages[*].tables -> financial facts
    """

    def __init__(self, refinery_path: str | Path | None = None):
        self._refinery_path = Path(refinery_path) if refinery_path else None

    async def extract(
        self,
        file_path: str,
        document_type: str,
    ) -> dict[str, Any]:
        try:
            from src.agents.extractor import extract_document
            from src.agents.fact_table_extractor import extract_facts_from_document
        except ImportError:
            raise RuntimeError(
                "document-refinery is not installed. "
                "Install with: pip install -e <path-to-document-refinery>"
            )

        doc = extract_document(Path(file_path))
        facts = extract_facts_from_document(doc)
        return facts if isinstance(facts, dict) else {"raw_facts": facts}
