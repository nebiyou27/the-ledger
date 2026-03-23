import pytest

from ledger.agents.base_agent import BaseApexAgent
from ledger.event_store import InMemoryEventStore
from ledger.integrity.gas_town import reconstruct_agent_context


class _NoopLLM:
    async def generate(self, **kwargs):
        return ("noop", 0, 0, 0.0)


class SnapshotAgent(BaseApexAgent):
    def build_graph(self):
        async def node_validate(state):
            await self._record_node_execution("validate_inputs", ["application_id"], ["validated"], ms=1)
            return state

        async def node_lookup(state):
            await self._record_node_execution("load_context", ["application_id"], ["context"], ms=1)
            return state

        async def node_analyze(state):
            await self._record_node_execution("analyze", ["context"], ["decision"], ms=1)
            return {"next_agent_triggered": None}

        from langgraph.graph import END, StateGraph

        graph = StateGraph(dict)
        graph.add_node("validate", node_validate)
        graph.add_node("lookup", node_lookup)
        graph.add_node("analyze", node_analyze)
        graph.set_entry_point("validate")
        graph.add_edge("validate", "lookup")
        graph.add_edge("lookup", "analyze")
        graph.add_edge("analyze", END)
        return graph.compile()


@pytest.mark.asyncio
async def test_agent_session_emits_snapshot_and_recovery_uses_it():
    store = InMemoryEventStore()
    agent = SnapshotAgent(
        agent_id="A1",
        agent_type="decision_orchestrator",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    await agent.process_application("SNAP-001")

    session_stream = f"agent-{agent.agent_type}-{agent.session_id}"
    session_events = await store.load_stream(session_stream)
    snapshots = [event for event in session_events if event["event_type"] == "AgentSessionSnapshot"]

    assert snapshots, "expected at least one AgentSessionSnapshot event"
    assert snapshots[0]["payload"]["snapshot_reason"] == "periodic"
    assert snapshots[0]["payload"]["last_completed_node"] == "analyze"

    context = await reconstruct_agent_context(store, agent_id="A1", session_id=agent.session_id)
    assert context.context_text.startswith("Snapshot checkpoint:")
