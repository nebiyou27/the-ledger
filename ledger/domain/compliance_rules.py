"""
ledger/domain/compliance_rules.py
=================================
Dedicated, versioned compliance rules module for the Regulation-Evolution layer.
Isolates compliance logic from orchestration agents.
"""
from typing import Any, Callable, Dict

RULE_SET_VERSION = "2026-Q1-v1"

class ComplianceRule:
    def __init__(self, rule_id: str, name: str, is_hard_block: bool, check: Callable[[Dict[str, Any]], bool], failure_reason: str | None = None, remediation: str | None = None, note_type: str | None = None, note_text: str | None = None):
        self.rule_id = rule_id
        self.name = name
        self.version = RULE_SET_VERSION
        self.is_hard_block = is_hard_block
        self.check = check
        self.failure_reason = failure_reason
        self.remediation = remediation
        self.note_type = note_type
        self.note_text = note_text

REGULATIONS: Dict[str, ComplianceRule] = {
    "REG-001": ComplianceRule(
        rule_id="REG-001",
        name="Bank Secrecy Act (BSA) Check",
        is_hard_block=False,
        check=lambda co: not any(
            f.get("flag_type") == "AML_WATCH" and f.get("is_active")
            for f in co.get("compliance_flags", [])
        ),
        failure_reason="Active AML Watch flag present. Remediation required.",
        remediation="Provide enhanced due diligence documentation within 10 business days.",
    ),
    "REG-002": ComplianceRule(
        rule_id="REG-002",
        name="OFAC Sanctions Screening",
        is_hard_block=True,
        check=lambda co: not any(
            f.get("flag_type") == "SANCTIONS_REVIEW" and f.get("is_active")
            for f in co.get("compliance_flags", [])
        ),
        failure_reason="Active OFAC Sanctions Review. Application blocked.",
    ),
    "REG-003": ComplianceRule(
        rule_id="REG-003",
        name="Jurisdiction Lending Eligibility",
        is_hard_block=True,
        check=lambda co: co.get("jurisdiction") != "MT",
        failure_reason="Jurisdiction MT not approved for commercial lending at this time.",
    ),
    "REG-004": ComplianceRule(
        rule_id="REG-004",
        name="Legal Entity Type Eligibility",
        is_hard_block=False,
        check=lambda co: not (
            co.get("legal_type") == "Sole Proprietor"
            and (co.get("requested_amount_usd", 0) or 0) > 250_000
        ),
        failure_reason="Sole Proprietor loans >$250K require additional documentation.",
        remediation="Submit SBA Form 912 and personal financial statement.",
    ),
    "REG-005": ComplianceRule(
        rule_id="REG-005",
        name="Minimum Operating History",
        is_hard_block=True,
        check=lambda co: (2024 - (co.get("founded_year") or 2024)) >= 2,
        failure_reason="Business must have at least 2 years of operating history.",
    ),
    "REG-006": ComplianceRule(
        rule_id="REG-006",
        name="CRA Community Reinvestment",
        is_hard_block=False,
        check=lambda co: True,
        note_type="CRA_CONSIDERATION",
        note_text="Jurisdiction qualifies for Community Reinvestment Act consideration.",
    ),
}
