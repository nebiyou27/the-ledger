from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.models.events import DomainError


class AgentSessionState(str, Enum):
    NEW = "NEW"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class AgentSessionAggregate:
    stream_id: str
    session_id: str | None = None
    agent_type: str | None = None
    agent_id: str | None = None
    application_id: str | None = None
    model_version: str | None = None
    context_source: str | None = None
    context_loaded: bool = False
    state: AgentSessionState = AgentSessionState.NEW
    version: int = -1
    node_count: int = 0
    tool_calls: int = 0
    output_events_written: int = 0
    events: list[dict] = field(default_factory=list)

    @classmethod
    async def load(cls, store, stream_id: str) -> "AgentSessionAggregate":
        agg = cls(stream_id=stream_id)
        stream_events = await store.load_stream(stream_id)
        for event in stream_events:
            agg.apply(event)
        return agg

    def apply(self, event: dict) -> None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        self.events.append(event)

        stream_position = event.get("stream_position")
        if isinstance(stream_position, int):
            self.version = stream_position
        else:
            self.version += 1

        # Gas Town invariant: first event must declare context.
        if self.state == AgentSessionState.NEW and event_type != "AgentSessionStarted":
            raise DomainError("AgentSession must start with AgentSessionStarted")

        if event_type == "AgentSessionStarted":
            if self.state != AgentSessionState.NEW:
                raise DomainError("Duplicate AgentSessionStarted event")
            self.session_id = payload.get("session_id")
            self.agent_type = str(payload.get("agent_type") or "")
            self.agent_id = payload.get("agent_id")
            self.application_id = payload.get("application_id")
            self.model_version = payload.get("model_version")
            self.context_source = payload.get("context_source")
            self.context_loaded = bool(self.context_source)
            self.state = AgentSessionState.STARTED
            return

        if self.state in (AgentSessionState.COMPLETED, AgentSessionState.FAILED):
            # Recovery is the only event allowed after a terminal state.
            if event_type != "AgentSessionRecovered":
                raise DomainError(f"Cannot append {event_type} after terminal state {self.state.value}")

        # Validate identity continuity in-stream.
        if payload.get("session_id") and self.session_id and payload.get("session_id") != self.session_id:
            raise DomainError("Mismatched session_id in AgentSession stream")
        if payload.get("agent_type") and self.agent_type and str(payload.get("agent_type")) != self.agent_type:
            raise DomainError("Mismatched agent_type in AgentSession stream")

        if event_type == "AgentNodeExecuted":
            self._require_context(event_type)
            self.node_count += 1
            return

        if event_type == "AgentToolCalled":
            self._require_context(event_type)
            self.tool_calls += 1
            return

        if event_type == "AgentOutputWritten":
            self._require_context(event_type)
            self.output_events_written += len(payload.get("events_written") or [])
            return

        if event_type == "AgentSessionCompleted":
            self._require_context(event_type)
            # Model-version locking: if event carries a version-like field, it must match.
            payload_model_version = payload.get("model_version")
            if payload_model_version and self.model_version and payload_model_version != self.model_version:
                raise DomainError("Agent session model version changed mid-session")
            self.state = AgentSessionState.COMPLETED
            return

        if event_type == "AgentSessionFailed":
            self._require_context(event_type)
            self.state = AgentSessionState.FAILED
            return

        if event_type == "AgentSessionRecovered":
            self._require_context(event_type)
            self.state = AgentSessionState.STARTED
            return

        if event_type in ("AgentInputValidated", "AgentInputValidationFailed"):
            self._require_context(event_type)
            return

    def _require_context(self, event_type: str) -> None:
        if not self.context_loaded:
            raise DomainError(f"{event_type} requires loaded context")
