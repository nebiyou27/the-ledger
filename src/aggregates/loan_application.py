"""Deliverable-path shim to LoanApplicationAggregate."""

from ledger.domain.aggregates.loan_application import (
    ApplicationState,
    LoanApplicationAggregate,
)

__all__ = ["ApplicationState", "LoanApplicationAggregate"]

