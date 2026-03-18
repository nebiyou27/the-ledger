import pytest

from ledger.domain.aggregates.agent_session import AgentSessionAggregate, AgentSessionState
from ledger.domain.aggregates.audit_ledger import AuditLedgerAggregate
from ledger.domain.aggregates.compliance_record import ComplianceRecordAggregate


def _ev(event_type: str, payload: dict | None = None, pos: int | None = None):
    e = {"event_type": event_type, "payload": payload or {}}
    if pos is not None:
        e["stream_position"] = pos
    return e


def test_agent_session_requires_started_event_first():
    agg = AgentSessionAggregate(stream_id="agent-credit_analysis-sess-1")
    with pytest.raises(ValueError):
        agg.apply(_ev("AgentNodeExecuted", {"session_id": "sess-1"}))


def test_agent_session_transitions_and_counts():
    agg = AgentSessionAggregate(stream_id="agent-credit_analysis-sess-1")
    agg.apply(_ev("AgentSessionStarted", {
        "session_id": "sess-1",
        "agent_type": "credit_analysis",
        "agent_id": "agent-1",
        "application_id": "APEX-1",
        "model_version": "claude-sonnet-4-20250514",
        "context_source": "fresh",
    }))
    agg.apply(_ev("AgentNodeExecuted", {"session_id": "sess-1", "agent_type": "credit_analysis"}))
    agg.apply(_ev("AgentToolCalled", {"session_id": "sess-1", "agent_type": "credit_analysis"}))
    agg.apply(_ev("AgentOutputWritten", {
        "session_id": "sess-1",
        "agent_type": "credit_analysis",
        "events_written": [{"event_type": "CreditAnalysisCompleted"}],
    }))
    agg.apply(_ev("AgentSessionCompleted", {"session_id": "sess-1", "agent_type": "credit_analysis"}))

    assert agg.state == AgentSessionState.COMPLETED
    assert agg.node_count == 1
    assert agg.tool_calls == 1
    assert agg.output_events_written == 1


def test_compliance_completion_requires_all_rules_without_hard_block():
    agg = ComplianceRecordAggregate(application_id="APEX-1")
    agg.apply(_ev("ComplianceCheckInitiated", {
        "session_id": "sess-com-1",
        "regulation_set_version": "2026-Q1",
        "rules_to_evaluate": ["REG-001", "REG-002"],
    }))
    agg.apply(_ev("ComplianceRulePassed", {"rule_id": "REG-001"}))

    with pytest.raises(ValueError):
        agg.apply(_ev("ComplianceCheckCompleted", {
            "rules_passed": 1,
            "rules_failed": 0,
            "rules_noted": 0,
            "overall_verdict": "CLEAR",
        }))


def test_compliance_hard_block_allows_early_completion():
    agg = ComplianceRecordAggregate(application_id="APEX-1")
    agg.apply(_ev("ComplianceCheckInitiated", {
        "session_id": "sess-com-1",
        "regulation_set_version": "2026-Q1",
        "rules_to_evaluate": ["REG-001", "REG-002", "REG-003"],
    }))
    agg.apply(_ev("ComplianceRuleFailed", {"rule_id": "REG-002", "is_hard_block": True}))
    agg.apply(_ev("ComplianceCheckCompleted", {
        "rules_passed": 0,
        "rules_failed": 1,
        "rules_noted": 0,
        "overall_verdict": "BLOCKED",
    }))

    assert agg.completed is True
    assert agg.verdict == "BLOCKED"


def test_audit_chain_mismatch_detected():
    agg = AuditLedgerAggregate(entity_id="APEX-1")
    agg.apply(_ev("AuditIntegrityCheckRun", {
        "previous_hash": None,
        "integrity_hash": "hash-1",
        "chain_valid": True,
        "tamper_detected": False,
    }))

    with pytest.raises(ValueError):
        agg.apply(_ev("AuditIntegrityCheckRun", {
            "previous_hash": "not-hash-1",
            "integrity_hash": "hash-2",
            "chain_valid": True,
            "tamper_detected": False,
        }))

    assert agg.chain_valid is False
    assert agg.tamper_detected is True
