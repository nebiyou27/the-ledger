import pytest

from ledger.agents.base_agent import BaseApexAgent
from ledger.event_store import InMemoryEventStore


class _NoopLLM:
    async def generate(self, **kwargs):
        return {"text": "noop"}


class CrashyRecoveryAgent(BaseApexAgent):
    crashed_once = False

    def build_graph(self):
        async def node_decide(state):
            stream_id = f"loan-{self.application_id}"
            event = {
                "event_type": "DecisionGenerated",
                "event_version": 1,
                "payload": {
                    "application_id": self.application_id,
                    "agent": self.agent_type,
                    "status": "approved",
                },
            }
            positions = await self._append_with_retry(stream_id, [event])
            await self._record_output_written(
                [{"stream_id": stream_id, "event_type": "DecisionGenerated", "stream_position": positions[0]}],
                "Decision written",
            )
            await self._record_node_execution(
                "decide",
                ["application_id"],
                ["decision_written"],
                ms=1,
            )
            if not type(self).crashed_once:
                type(self).crashed_once = True
                raise RuntimeError("simulated crash after decision write")
            return {"next_agent_triggered": None}

        from langgraph.graph import END, StateGraph

        graph = StateGraph(dict)
        graph.add_node("decide", node_decide)
        graph.set_entry_point("decide")
        graph.add_edge("decide", END)
        return graph.compile()


@pytest.mark.asyncio
async def test_agent_resume_records_recovery_and_avoids_duplicate_domain_write():
    CrashyRecoveryAgent.crashed_once = False
    store = InMemoryEventStore()
    agent = CrashyRecoveryAgent(
        agent_id="agent-1",
        agent_type="decision_orchestrator",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )
    application_id = "RECOVER-001"

    with pytest.raises(RuntimeError):
        await agent.process_application(application_id)

    crashed_session_id = agent.session_id
    assert crashed_session_id is not None

    await agent.process_application(application_id, resume_from_session_id=crashed_session_id)
    recovered_session_id = agent.session_id

    recovered_stream = await store.load_stream(f"agent-{agent.agent_type}-{recovered_session_id}")
    assert any(event["event_type"] == "AgentSessionRecovered" for event in recovered_stream)
    recovery_event = next(event for event in recovered_stream if event["event_type"] == "AgentSessionRecovered")
    assert recovery_event["payload"]["recovered_from_session_id"] == crashed_session_id

    loan_events = await store.load_stream(f"loan-{application_id}")
    decision_events = [event for event in loan_events if event["event_type"] == "DecisionGenerated"]
    assert len(decision_events) == 1
    assert decision_events[0]["payload"]["agent"] == "decision_orchestrator"
