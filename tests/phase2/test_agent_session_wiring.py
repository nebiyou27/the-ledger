import pytest

from ledger.agents.base_agent import BaseApexAgent
from ledger.event_store import InMemoryEventStore


class _MiniAgent(BaseApexAgent):
    def build_graph(self):
        return None


@pytest.mark.asyncio
async def test_append_session_rejects_node_before_start():
    store = InMemoryEventStore()
    agent = _MiniAgent(
        agent_id="agent-1",
        agent_type="credit_analysis",
        store=store,
        registry=None,
        client=None,
    )
    agent.session_id = "sess-cre-test"
    agent._session_stream = "agent-credit_analysis-sess-cre-test"

    with pytest.raises(ValueError):
        await agent._append_session(
            {
                "event_type": "AgentNodeExecuted",
                "event_version": 1,
                "payload": {
                    "session_id": "sess-cre-test",
                    "agent_type": "credit_analysis",
                    "node_name": "validate_inputs",
                    "node_sequence": 1,
                    "input_keys": ["application_id"],
                    "output_keys": ["validated"],
                    "llm_called": False,
                    "duration_ms": 5,
                    "executed_at": "2026-03-18T10:00:00Z",
                },
            }
        )


@pytest.mark.asyncio
async def test_append_session_accepts_started_then_node():
    store = InMemoryEventStore()
    agent = _MiniAgent(
        agent_id="agent-1",
        agent_type="credit_analysis",
        store=store,
        registry=None,
        client=None,
    )
    agent.session_id = "sess-cre-test"
    agent._session_stream = "agent-credit_analysis-sess-cre-test"

    await agent._append_session(
        {
            "event_type": "AgentSessionStarted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-cre-test",
                "agent_type": "credit_analysis",
                "agent_id": "agent-1",
                "application_id": "APEX-1",
                "model_version": "claude-sonnet-4-20250514",
                "langgraph_graph_version": "1.0.0",
                "context_source": "fresh",
                "context_token_count": 1000,
                "started_at": "2026-03-18T10:00:00Z",
            },
        }
    )
    await agent._append_session(
        {
            "event_type": "AgentNodeExecuted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-cre-test",
                "agent_type": "credit_analysis",
                "node_name": "validate_inputs",
                "node_sequence": 1,
                "input_keys": ["application_id"],
                "output_keys": ["validated"],
                "llm_called": False,
                "duration_ms": 5,
                "executed_at": "2026-03-18T10:00:01Z",
            },
        }
    )

    events = await store.load_stream("agent-credit_analysis-sess-cre-test")
    assert [e["event_type"] for e in events] == [
        "AgentSessionStarted",
        "AgentNodeExecuted",
    ]
