import pytest

from ledger.agents.base_agent import BaseApexAgent
from ledger.event_store import InMemoryEventStore
from src.models.events import DomainError


class _NoopLLM:
    async def generate(self, **kwargs):
        return ("noop", 0, 0, 0.0)


class _TimeoutThenSucceedAgent(BaseApexAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def build_graph(self):
        async def node_run(state):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("temporary upstream timeout")
            event = {
                "event_type": "DecisionGenerated",
                "event_version": 1,
                "payload": {"application_id": self.application_id, "agent": self.agent_type},
            }
            await self._append_with_retry(f"loan-{self.application_id}", [event])
            return {"next_agent_triggered": None}

        from langgraph.graph import END, StateGraph

        graph = StateGraph(dict)
        graph.add_node("run", node_run)
        graph.set_entry_point("run")
        graph.add_edge("run", END)
        return graph.compile()


class _FatalDomainErrorAgent(BaseApexAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def build_graph(self):
        async def node_run(state):
            self.calls += 1
            raise DomainError("invalid application state")

        from langgraph.graph import END, StateGraph

        graph = StateGraph(dict)
        graph.add_node("run", node_run)
        graph.set_entry_point("run")
        graph.add_edge("run", END)
        return graph.compile()


@pytest.mark.asyncio
async def test_retryable_timeout_is_retried_and_completes():
    store = InMemoryEventStore()
    agent = _TimeoutThenSucceedAgent(
        agent_id="A1",
        agent_type="decision_orchestrator",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    await agent.process_application("ERR-001")

    assert agent.calls == 2
    session_events = await store.load_stream(f"agent-{agent.agent_type}-{agent.session_id}")
    assert any(event["event_type"] == "AgentSessionCompleted" for event in session_events)
    assert not any(event["event_type"] == "AgentSessionFailed" for event in session_events)


@pytest.mark.asyncio
async def test_domain_error_is_fatal_and_not_retried():
    store = InMemoryEventStore()
    agent = _FatalDomainErrorAgent(
        agent_id="A2",
        agent_type="decision_orchestrator",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    with pytest.raises(DomainError):
        await agent.process_application("ERR-002")

    assert agent.calls == 1
    session_events = await store.load_stream(f"agent-{agent.agent_type}-{agent.session_id}")
    failed = next(event for event in session_events if event["event_type"] == "AgentSessionFailed")
    assert failed["payload"]["recoverable"] is False
    assert failed["payload"]["error_type"] == "DomainError"
