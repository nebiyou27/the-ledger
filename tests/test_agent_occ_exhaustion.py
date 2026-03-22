import pytest
import asyncio
from ledger.agents.base_agent import BaseApexAgent, MAX_OCC_RETRIES
from ledger.exceptions import OptimisticConcurrencyError

# Global tracker for _fail_session calls
fail_session_calls = []

class AlwaysOCCStore:
    def __init__(self):
        self.append_calls = 0
    async def stream_version(self, stream_id):
        return 0
    async def append(self, *args, **kwargs):
        self.append_calls += 1
        raise OptimisticConcurrencyError(stream_id="loan-FAIL-001", expected=0, actual=1)
    async def load_stream(self, stream_id):
        return []

class DummyAgent(BaseApexAgent):
    def build_graph(self):
        async def node_decide(state):
            event = {"event_type": "DecisionGenerated", "event_version": 1, "payload": {"agent": self.agent_type}}
            # Only one attempt, let process_application handle retries
            await self._append_with_retry(f"loan-{self.application_id}", [event], max_retries=0)
            return {"status": "done"}
        from langgraph.graph import StateGraph, END
        g = StateGraph(dict)
        g.add_node("decide", node_decide)
        g.set_entry_point("decide")
        g.add_edge("decide", END)
        return g.compile()
    async def _fail_session(self, etype, emsg):
        fail_session_calls.append((etype, emsg))
        await super()._fail_session(etype, emsg)

@pytest.mark.asyncio
async def test_agent_occ_retry_exhaustion():
    global fail_session_calls
    fail_session_calls = []
    store = AlwaysOCCStore()
    agent = DummyAgent(agent_id="A1", agent_type="dummy", store=store, registry=None)
    application_id = "FAIL-001"
    with pytest.raises(OptimisticConcurrencyError):
        await agent.process_application(application_id)
    # Assert exactly MAX_OCC_RETRIES attempts
    assert store.append_calls == MAX_OCC_RETRIES
    # Assert _fail_session called once with OCC
    assert len(fail_session_calls) == 1
    assert fail_session_calls[0][0] == "OptimisticConcurrencyError"
