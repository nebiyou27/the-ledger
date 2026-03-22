import asyncio
import pytest
from ledger.event_store import InMemoryEventStore, OptimisticConcurrencyError
from ledger.agents.base_agent import BaseApexAgent
from ledger.domain.aggregates.loan_application import LoanApplicationAggregate

class DummyAgent(BaseApexAgent):
    def build_graph(self):
        # Minimal state dict with required keys for LangGraph
        async def node_decide(state):
            # Simulate loading aggregate and making a decision based on current state
            agg = await LoanApplicationAggregate.load(self.store, self.application_id)
            # Decision logic: always append a DecisionGenerated event
            event = {"event_type": "DecisionGenerated", "event_version": 1, "payload": {"agent": self.agent_type}}
            await self._append_with_retry(f"loan-{self.application_id}", [event])
            # Return a valid state dict with required keys
            return {
                "application_id": self.application_id,
                "agent_id": self.agent_id,
                "agent_type": self.agent_type,
                "events_written": [event],
                "status": "done"
            }
        from langgraph.graph import StateGraph, END
        # Use a plain dict for state
        g = StateGraph(dict)
        g.add_node("decide", node_decide)
        g.set_entry_point("decide")
        g.add_edge("decide", END)
        return g.compile()

@pytest.mark.asyncio
async def test_agent_occ_retry():
    """
    Simulate two agents (Compliance and Credit) racing to append to the same loan stream.
    The first agent wins; the second detects OCC, reloads, re-runs, and succeeds on retry.
    Assert the stream is consistent and has no duplicate/contradictory events.
    """
    store = InMemoryEventStore()
    application_id = "APEX-0016"

    # Seed the stream with ApplicationSubmitted
    await store.append(f"loan-{application_id}", [
        {"event_type": "ApplicationSubmitted", "event_version": 1, "payload": {"applicant": "COMP-001"}}
    ], expected_version=-1)

    # Create two agents with different types
    agent1 = DummyAgent(agent_id="A1", agent_type="compliance", store=store, registry=None)
    agent2 = DummyAgent(agent_id="A2", agent_type="credit_analysis", store=store, registry=None)

    # Both agents try to process the same application concurrently
    task1 = asyncio.create_task(agent1.process_application(application_id))
    task2 = asyncio.create_task(agent2.process_application(application_id))
    results = await asyncio.gather(task1, task2, return_exceptions=True)

    # Assert no exceptions (OCC should be retried internally)
    for r in results:
        if isinstance(r, Exception):
            raise r

    # Load the final event stream
    events = await store.load_stream(f"loan-{application_id}")
    event_types = [e["event_type"] for e in events]
    # Should contain ApplicationSubmitted and two DecisionGenerated (one per agent)
    assert event_types.count("ApplicationSubmitted") == 1
    assert event_types.count("DecisionGenerated") == 2
    # No duplicate or contradictory events (each agent's event is unique by agent_type)
    agents = [e["payload"].get("agent") for e in events if e["event_type"] == "DecisionGenerated"]
    assert set(agents) == {"compliance", "credit_analysis"}
    # Stream positions are correct and unique
    positions = [e["stream_position"] for e in events]
    assert positions == list(range(len(events)))
