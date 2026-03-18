from __future__ import annotations

import pytest

from ledger.event_store import InMemoryEventStore
from src.integrity.gas_town import reconstruct_agent_context


@pytest.mark.asyncio
async def test_reconstruct_agent_context_after_simulated_crash():
    store = InMemoryEventStore()
    stream_id = "agent-credit_analysis-sess-gas-1"
    await store.append(
        stream_id,
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-gas-1",
                    "agent_type": "credit_analysis",
                    "agent_id": "agent-9",
                    "application_id": "APEX-9",
                    "model_version": "model-v1",
                    "context_source": "fresh",
                },
            },
            {
                "event_type": "AgentNodeExecuted",
                "payload": {"session_id": "sess-gas-1", "agent_type": "credit_analysis", "node_name": "validate"},
            },
            {
                "event_type": "AgentToolCalled",
                "payload": {"session_id": "sess-gas-1", "agent_type": "credit_analysis", "tool_name": "registry_lookup"},
            },
            {
                "event_type": "AgentOutputWritten",
                "payload": {"session_id": "sess-gas-1", "application_id": "APEX-9", "events_written": [{"event_type": "CreditAnalysisCompleted"}]},
            },
            {
                "event_type": "AgentSessionFailed",
                "payload": {"session_id": "sess-gas-1", "agent_type": "credit_analysis", "application_id": "APEX-9", "error_type": "TimeoutError"},
            },
        ],
        expected_version=-1,
    )

    context = await reconstruct_agent_context(
        store,
        agent_id="agent-9",
        session_id="sess-gas-1",
        token_budget=8000,
    )

    assert context.last_event_position == 4
    assert "VERBATIM" in context.context_text
    assert context.session_health_status == "NEEDS_RECONCILIATION"
    assert len(context.pending_work) >= 1
