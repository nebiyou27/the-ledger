from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    context_text: str
    last_event_position: int
    pending_work: list[str] = field(default_factory=list)
    session_health_status: str = "HEALTHY"
    stream_id: str | None = None


async def _find_agent_stream(store, agent_id: str, session_id: str) -> tuple[str | None, list[dict[str, Any]]]:
    direct = f"agent-{agent_id}-{session_id}"
    events = await store.load_stream(direct)
    if events:
        return direct, events

    # Fallback scan for repositories that use agent-{agent_type}-{session_id}.
    found_stream: str | None = None
    found_events: list[dict[str, Any]] = []
    async for event in store.load_all(from_position=0):
        if str(event.get("event_type", "")).startswith("Agent"):
            payload = event.get("payload") or {}
            if str(payload.get("session_id", "")) == session_id and str(payload.get("agent_id", "")) == agent_id:
                found_stream = str(event.get("stream_id"))
                if not found_events:
                    found_events = await store.load_stream(found_stream)
                break
    return found_stream, found_events


def _derive_pending_work(events: list[dict[str, Any]]) -> list[str]:
    pending: list[str] = []
    for event in events:
        etype = str(event.get("event_type", ""))
        payload = event.get("payload") or {}
        if etype.endswith("Requested"):
            pending.append(etype)
        if etype.endswith("Completed"):
            req = etype.replace("Completed", "Requested")
            if req in pending:
                pending.remove(req)
        if etype == "AgentSessionFailed":
            pending.append(f"recover:{payload.get('error_type', 'unknown')}")
    return pending


def _summarize(events: list[dict[str, Any]], token_budget: int) -> str:
    if not events:
        return "No prior events."
    prefix = "Session replay summary:\n"
    lines = [f"- {ev.get('event_type')} @ pos={ev.get('stream_position')}" for ev in events[:-3]]
    verbatim = []
    for ev in events[-3:]:
        verbatim.append(
            f"- VERBATIM {ev.get('event_type')} payload={ev.get('payload')}"
        )
    text = prefix + "\n".join(lines + verbatim)
    max_chars = max(200, token_budget * 4)
    return text[:max_chars]


async def reconstruct_agent_context(
    store,
    agent_id: str,
    session_id: str,
    token_budget: int = 8000,
) -> AgentContext:
    stream_id, events = await _find_agent_stream(store, agent_id=agent_id, session_id=session_id)
    if not events:
        return AgentContext(
            context_text="No agent session events found.",
            last_event_position=-1,
            pending_work=[],
            session_health_status="NOT_FOUND",
            stream_id=stream_id,
        )

    latest_snapshot_index = -1
    latest_snapshot_payload: dict[str, Any] | None = None
    for idx, event in enumerate(events):
        if str(event.get("event_type", "")) == "AgentSessionSnapshot":
            latest_snapshot_index = idx
            latest_snapshot_payload = event.get("payload") or {}

    if latest_snapshot_payload is not None:
        pending_work = list(latest_snapshot_payload.get("pending_work") or [])
        pending_work.extend(_derive_pending_work(events[latest_snapshot_index + 1 :]))
    else:
        pending_work = _derive_pending_work(events)

    last_event = events[-1]
    last_event_type = str(last_event.get("event_type", ""))

    status = "HEALTHY"
    if last_event_type in {"AgentSessionFailed", "AgentInputValidationFailed"}:
        status = "NEEDS_RECONCILIATION"
    if last_event_type in {"DecisionGenerated", "AgentOutputWritten"} and "AgentSessionCompleted" not in {
        str(e.get("event_type", "")) for e in events
    }:
        status = "NEEDS_RECONCILIATION"

    if latest_snapshot_payload is not None:
        snapshot_reason = str(latest_snapshot_payload.get("snapshot_reason") or "checkpoint")
        last_completed_node = str(latest_snapshot_payload.get("last_completed_node") or "unknown")
        snapshot_prefix = (
            f"Snapshot checkpoint: reason={snapshot_reason}, "
            f"node={last_completed_node}, "
            f"sequence={latest_snapshot_payload.get('node_sequence', -1)}\n"
        )
        context_text = snapshot_prefix + _summarize(events[latest_snapshot_index + 1 :], token_budget=token_budget)
    else:
        context_text = _summarize(events, token_budget=token_budget)
    return AgentContext(
        context_text=context_text,
        last_event_position=int(last_event.get("stream_position", -1)),
        pending_work=pending_work,
        session_health_status=status,
        stream_id=stream_id,
    )
