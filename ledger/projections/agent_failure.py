from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ledger.projections.base import Projection


def _to_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _sort_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


class AgentSessionFailureProjection(Projection):
    def __init__(self):
        super().__init__("agent_session_failures")
        self._sessions: dict[str, dict[str, Any]] = {}

    def handles(self, event_type: str) -> bool:
        return event_type in {
            "AgentSessionStarted",
            "AgentSessionCompleted",
            "AgentSessionFailed",
            "AgentSessionRecovered",
            "AgentNodeExecuted",
            "AgentToolCalled",
            "AgentOutputWritten",
            "AgentSessionSnapshotted",
            "AgentInputValidated",
            "AgentInputValidationFailed",
        }

    async def process_event(self, event: dict[str, Any]) -> None:
        etype = str(event.get("event_type") or "")
        payload = event.get("payload") or {}
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return

        record = self._sessions.get(session_id)
        if record is None:
            record = {
                "session_id": session_id,
                "agent_type": str(payload.get("agent_type") or "unknown"),
                "agent_id": str(payload.get("agent_id") or "unknown-agent"),
                "application_id": str(payload.get("application_id") or "unknown"),
                "model_version": str(payload.get("model_version") or "unknown-model"),
                "context_source": str(payload.get("context_source") or ""),
                "status": "NEW",
                "started_at": None,
                "completed_at": None,
                "failed_at": None,
                "recovered_at": None,
                "last_event_type": None,
                "last_event_at": None,
                "last_global_position": -1,
            }
            self._sessions[session_id] = record

        self._update_activity(record, event, etype)

        if etype == "AgentSessionStarted":
            self._apply_started(record, payload, event)
            return
        if etype == "AgentSessionCompleted":
            self._apply_completed(record, payload, event)
            return
        if etype == "AgentSessionFailed":
            self._apply_failed(record, payload, event)
            return
        if etype == "AgentSessionRecovered":
            self._apply_recovered(record, payload, event)
            return
        if etype == "AgentNodeExecuted":
            record["node_count"] = int(record.get("node_count", 0)) + 1
            return
        if etype == "AgentToolCalled":
            record["tool_call_count"] = int(record.get("tool_call_count", 0)) + 1
            return
        if etype == "AgentOutputWritten":
            record["output_event_count"] = int(record.get("output_event_count", 0)) + len(payload.get("events_written") or [])
            return
        if etype == "AgentSessionSnapshotted":
            record["snapshot_count"] = int(record.get("snapshot_count", 0)) + 1

    def _update_activity(self, record: dict[str, Any], event: dict[str, Any], etype: str) -> None:
        record["last_event_type"] = etype
        record["last_event_at"] = _to_utc(event.get("recorded_at")) or record.get("last_event_at")
        record["last_global_position"] = int(event.get("global_position", record.get("last_global_position", -1)))

    def _apply_started(self, record: dict[str, Any], payload: dict[str, Any], event: dict[str, Any]) -> None:
        record["agent_type"] = str(payload.get("agent_type") or record.get("agent_type") or "unknown")
        record["agent_id"] = str(payload.get("agent_id") or record.get("agent_id") or "unknown-agent")
        record["application_id"] = str(payload.get("application_id") or record.get("application_id") or "unknown")
        record["model_version"] = str(payload.get("model_version") or record.get("model_version") or "unknown-model")
        record["context_source"] = str(payload.get("context_source") or record.get("context_source") or "")
        record["started_at"] = _to_utc(payload.get("started_at")) or _to_utc(event.get("recorded_at"))
        record["status"] = "STARTED"

    def _apply_completed(self, record: dict[str, Any], payload: dict[str, Any], event: dict[str, Any]) -> None:
        record["completed_at"] = _to_utc(payload.get("completed_at")) or _to_utc(event.get("recorded_at"))
        record["status"] = "COMPLETED"

    def _apply_failed(self, record: dict[str, Any], payload: dict[str, Any], event: dict[str, Any]) -> None:
        record["failed_at"] = _to_utc(payload.get("failed_at")) or _to_utc(event.get("recorded_at"))
        record["status"] = "FAILED"

    def _apply_recovered(self, record: dict[str, Any], payload: dict[str, Any], event: dict[str, Any]) -> None:
        record["recovered_at"] = _to_utc(payload.get("recovered_at")) or _to_utc(event.get("recorded_at"))
        record["status"] = "STARTED"

    def all_rows(self) -> list[dict[str, Any]]:
        rows = [self._public_row(record) for record in self._sessions.values()]
        rows.sort(key=lambda row: (_sort_dt(row["started_at"]), _sort_dt(row["last_event_at"]), row["session_id"]), reverse=True)
        return [deepcopy(row) for row in rows]

    def get_stuck_sessions(self, timeout_ms: int, now: datetime | None = None) -> list[dict[str, Any]]:
        current_time = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        current_time = current_time if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)
        overdue: list[dict[str, Any]] = []
        for record in self._sessions.values():
            if str(record.get("status")) != "STARTED":
                continue
            started_at = _to_utc(record.get("started_at"))
            if started_at is None:
                continue
            age_ms = int((current_time - started_at).total_seconds() * 1000)
            if age_ms < timeout_ms:
                continue
            row = self._public_row(record)
            row.update(
                {
                    "age_ms": age_ms,
                    "timeout_ms": timeout_ms,
                    "is_overdue": True,
                }
            )
            overdue.append(row)

        overdue.sort(
            key=lambda row: (
                -(row.get("age_ms") or 0),
                row.get("started_at") or "",
                row.get("session_id") or "",
            )
        )
        return [deepcopy(row) for row in overdue]

    def _public_row(self, record: dict[str, Any]) -> dict[str, Any]:
        started_at = _to_utc(record.get("started_at"))
        completed_at = _to_utc(record.get("completed_at"))
        failed_at = _to_utc(record.get("failed_at"))
        recovered_at = _to_utc(record.get("recovered_at"))
        last_event_at = _to_utc(record.get("last_event_at"))
        return {
            "session_id": record.get("session_id"),
            "agent_type": record.get("agent_type"),
            "agent_id": record.get("agent_id"),
            "application_id": record.get("application_id"),
            "model_version": record.get("model_version"),
            "context_source": record.get("context_source"),
            "status": record.get("status"),
            "started_at": started_at,
            "completed_at": completed_at,
            "failed_at": failed_at,
            "recovered_at": recovered_at,
            "last_event_type": record.get("last_event_type"),
            "last_event_at": last_event_at,
            "last_global_position": record.get("last_global_position", -1),
            "node_count": int(record.get("node_count", 0)),
            "tool_call_count": int(record.get("tool_call_count", 0)),
            "output_event_count": int(record.get("output_event_count", 0)),
            "snapshot_count": int(record.get("snapshot_count", 0)),
        }
