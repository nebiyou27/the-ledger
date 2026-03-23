from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

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
    snapshot_count: int = 0
    last_snapshot_node: str | None = None
    events: list[dict] = field(default_factory=list)

    def assert_context_loaded(self) -> None:
        if not self.context_loaded:
            raise DomainError("Agent session requires loaded context")

    def assert_model_version_matches(self, model_version: str) -> None:
        if self.model_version and str(model_version) != self.model_version:
            raise DomainError("Agent session model version changed mid-session")

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
        handler = self._event_handlers().get(event_type)
        if handler is not None:
            handler(payload)

    def _event_handlers(self) -> dict[str, Callable[[dict], None]]:
        return {
            "AgentSessionStarted": self._apply_agent_session_started,
            "AgentNodeExecuted": self._apply_agent_node_executed,
            "AgentToolCalled": self._apply_agent_tool_called,
            "AgentOutputWritten": self._apply_agent_output_written,
            "AgentSessionCompleted": self._apply_agent_session_completed,
            "AgentSessionFailed": self._apply_agent_session_failed,
            "AgentSessionRecovered": self._apply_agent_session_recovered,
            "AgentSessionSnapshot": self._apply_agent_session_snapshot,
            "AgentInputValidated": self._apply_agent_input_validated,
            "AgentInputValidationFailed": self._apply_agent_input_validation_failed,
        }

    def _apply_agent_session_started(self, payload: dict) -> None:
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

    def _validate_identity(self, payload: dict) -> None:
        if payload.get("session_id") and self.session_id and payload.get("session_id") != self.session_id:
            raise DomainError("Mismatched session_id in AgentSession stream")
        if payload.get("agent_type") and self.agent_type and str(payload.get("agent_type")) != self.agent_type:
            raise DomainError("Mismatched agent_type in AgentSession stream")

    def _apply_agent_node_executed(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
        self.node_count += 1

    def _apply_agent_tool_called(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
        self.tool_calls += 1

    def _apply_agent_output_written(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
        self.output_events_written += len(payload.get("events_written") or [])

    def _apply_agent_session_completed(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
        self.assert_model_version_matches(str(payload.get("model_version") or self.model_version or ""))
        self.state = AgentSessionState.COMPLETED

    def _apply_agent_session_failed(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
        self.state = AgentSessionState.FAILED

    def _apply_agent_session_recovered(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
        self.state = AgentSessionState.STARTED

    def _apply_agent_session_snapshot(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
        self.snapshot_count += 1
        self.last_snapshot_node = payload.get("last_completed_node")

    def _apply_agent_input_validated(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()

    def _apply_agent_input_validation_failed(self, payload: dict) -> None:
        self._validate_identity(payload)
        self.assert_context_loaded()
