import pytest

from ledger.agents.base_agent import BaseApexAgent
from ledger.event_store import InMemoryEventStore


class _NoopLLM:
    async def generate(self, **kwargs):
        return ("noop", 0, 0, 0.0)


class ResumeAgent(BaseApexAgent):
    crashed_once = False

    def build_graph(self):
        async def bootstrap(state):
            if self._should_skip_node(state, 1):
                return state
            await self._record_node_execution("bootstrap", ["application_id"], ["bootstrap_done"], ms=1)
            next_state = {**state, "bootstrap_done": True}
            await self._save_session_checkpoint(next_state, "bootstrap")
            return next_state

        async def prepare(state):
            if self._should_skip_node(state, 2):
                return state
            await self._record_node_execution("prepare", ["bootstrap_done"], ["prepare_done"], ms=1)
            next_state = {**state, "prepare_done": True}
            await self._save_session_checkpoint(next_state, "prepare")
            return next_state

        async def finalize(state):
            if self._should_skip_node(state, 3):
                return state
            await self._record_node_execution("finalize", ["prepare_done"], ["decision_ready"], ms=1)
            if not type(self).crashed_once:
                type(self).crashed_once = True
                raise RuntimeError("simulated crash before final output")
            event = {
                "event_type": "DecisionGenerated",
                "event_version": 1,
                "payload": {
                    "application_id": self.application_id,
                    "agent": self.agent_type,
                    "resume": True,
                },
            }
            await self._append_with_retry(f"loan-{self.application_id}", [event])
            next_state = {**state, "decision_ready": True}
            await self._save_session_checkpoint(next_state, "finalize")
            return next_state

        from langgraph.graph import END, StateGraph

        graph = StateGraph(dict)
        graph.add_node("bootstrap", bootstrap)
        graph.add_node("prepare", prepare)
        graph.add_node("finalize", finalize)
        graph.set_entry_point("bootstrap")
        graph.add_edge("bootstrap", "prepare")
        graph.add_edge("prepare", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()


@pytest.mark.asyncio
async def test_checkpoint_resume_skips_completed_nodes():
    ResumeAgent.crashed_once = False
    store = InMemoryEventStore()
    agent = ResumeAgent(
        agent_id="A1",
        agent_type="decision_orchestrator",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    with pytest.raises(RuntimeError):
        await agent.process_application("CHK-001")

    crashed_session_id = agent.session_id
    checkpoint = await store.load_agent_checkpoint(crashed_session_id)
    assert checkpoint is not None
    assert checkpoint["last_completed_node"] == "prepare"
    assert checkpoint["node_sequence"] == 2

    await agent.process_application("CHK-001", resume_from_session_id=crashed_session_id)
    resumed_session_id = agent.session_id

    resumed_session_events = await store.load_stream(f"agent-{agent.agent_type}-{resumed_session_id}")
    resumed_node_names = [
        event["payload"]["node_name"]
        for event in resumed_session_events
        if event["event_type"] == "AgentNodeExecuted"
    ]
    assert resumed_node_names == ["finalize"]

    loan_events = await store.load_stream("loan-CHK-001")
    assert [event["event_type"] for event in loan_events].count("DecisionGenerated") == 1
