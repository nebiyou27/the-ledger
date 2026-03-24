from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

REPLAY_PROGRESS_STREAM = "system-replay-progress"
REPLAY_EVENT_TYPES: tuple[str, ...] = (
    "REPLAY_STARTED",
    "REPLAY_PROGRESS",
    "REPLAY_COMPLETED",
    "REPLAY_FAILED",
)

STREAM_SIZE_FAMILIES: tuple[tuple[str, str], ...] = (
    ("LoanApplication", "loan-"),
    ("ComplianceRecord", "compliance-"),
    ("AgentSession", "agent-"),
    ("AuditLedger", "audit-"),
)


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _iso_or_none(value: Any) -> str | None:
    parsed = _to_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _empty_replay_progress() -> dict[str, Any]:
    return {
        "status": "IDLE",
        "is_replaying": False,
        "projection_name": None,
        "events_processed": 0,
        "total_events": 0,
        "percent_complete": 0.0,
        "started_at": None,
        "estimated_completion": None,
        "last_updated": None,
    }


def _status_from_event_type(event_type: str) -> str:
    if event_type == "REPLAY_STARTED" or event_type == "REPLAY_PROGRESS":
        return "REPLAYING"
    if event_type == "REPLAY_COMPLETED":
        return "COMPLETED"
    if event_type == "REPLAY_FAILED":
        return "FAILED"
    return "IDLE"


def _events_processed_from_payload(payload: dict[str, Any]) -> int:
    if "events_processed_before_failure" in payload:
        return int(payload.get("events_processed_before_failure") or 0)
    return int(payload.get("events_processed") or 0)


async def set_replay_progress(store, update: dict[str, Any]) -> list[int]:
    event_type = str(update.get("event_type") or "REPLAY_PROGRESS").upper()
    if event_type not in REPLAY_EVENT_TYPES:
        raise ValueError(f"Unsupported replay progress event type '{event_type}'")

    payload = dict(update)
    now = _to_datetime(payload.get("last_updated")) or datetime.now(timezone.utc)
    started_at = _to_datetime(payload.get("started_at")) or now
    payload["started_at"] = started_at.isoformat()
    payload["last_updated"] = now.isoformat()
    payload["projection_name"] = payload.get("projection_name")
    payload["events_processed"] = int(payload.get("events_processed") or 0)
    payload["total_events"] = int(payload.get("total_events") or 0)
    payload["percent_complete"] = float(payload.get("percent_complete") or 0.0)
    payload["estimated_completion"] = _iso_or_none(payload.get("estimated_completion"))
    payload["status"] = _status_from_event_type(event_type)

    if event_type == "REPLAY_FAILED" and "events_processed_before_failure" not in payload:
        payload["events_processed_before_failure"] = payload["events_processed"]
    if event_type == "REPLAY_COMPLETED":
        payload["percent_complete"] = 100.0

    current_version = await store.stream_version(REPLAY_PROGRESS_STREAM)
    return await store.append(
        REPLAY_PROGRESS_STREAM,
        [
            {
                "event_type": event_type,
                "event_version": 1,
                "payload": payload,
                "recorded_at": now,
            }
        ],
        expected_version=current_version,
    )


async def get_replay_progress(store) -> dict[str, Any]:
    latest_event: dict[str, Any] | None = None
    async for event in store.load_all(from_position=0, event_types=list(REPLAY_EVENT_TYPES)):
        latest_event = event

    if latest_event is None:
        return _empty_replay_progress()

    payload = dict(latest_event.get("payload") or {})
    event_type = str(latest_event.get("event_type") or "")
    status = str(payload.get("status") or _status_from_event_type(event_type))
    is_replaying = status == "REPLAYING"
    events_processed = _events_processed_from_payload(payload)
    total_events = int(payload.get("total_events") or 0)
    percent_complete = payload.get("percent_complete")
    if percent_complete is None:
        percent_complete = (events_processed / total_events * 100.0) if total_events else 0.0

    started_at = _iso_or_none(payload.get("started_at"))
    estimated_completion = _iso_or_none(payload.get("estimated_completion"))
    last_updated = _iso_or_none(payload.get("last_updated")) or _iso_or_none(latest_event.get("recorded_at"))

    return {
        "status": status,
        "is_replaying": is_replaying,
        "projection_name": payload.get("projection_name"),
        "events_processed": events_processed,
        "total_events": total_events,
        "percent_complete": round(float(percent_complete), 2),
        "started_at": started_at,
        "estimated_completion": estimated_completion,
        "last_updated": last_updated,
    }


def build_manual_review_backlog_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pending_rows = [row for row in rows if str(row.get("status", "")).upper() == "PENDING"]
    resolved_rows = [row for row in rows if str(row.get("status", "")).upper() == "RESOLVED"]
    assigned_rows = [row for row in pending_rows if row.get("assigned_to")]
    unassigned_rows = [row for row in pending_rows if not row.get("assigned_to")]

    now = datetime.now(timezone.utc)
    pending_ages: list[int] = []
    oldest_pending_at: datetime | None = None

    for row in pending_rows:
        requested_at = _to_datetime(row.get("requested_at"))
        if requested_at is None:
            continue
        age_ms = max(0, int((now - requested_at).total_seconds() * 1000))
        pending_ages.append(age_ms)
        if oldest_pending_at is None or requested_at < oldest_pending_at:
            oldest_pending_at = requested_at

    backlog_count = len(pending_rows)
    average_pending_age_ms = int(sum(pending_ages) / len(pending_ages)) if pending_ages else 0
    oldest_pending_age_ms = max(pending_ages) if pending_ages else 0

    return {
        "backlogCount": backlog_count,
        "pendingCount": backlog_count,
        "resolvedCount": len(resolved_rows),
        "assignedCount": len(assigned_rows),
        "unassignedCount": len(unassigned_rows),
        "averagePendingAgeMillis": average_pending_age_ms,
        "oldestPendingAgeMillis": oldest_pending_age_ms,
        "oldestPendingAt": oldest_pending_at.isoformat() if oldest_pending_at else None,
        "staleCount": sum(1 for age_ms in pending_ages if age_ms >= 24 * 60 * 60 * 1000),
    }


async def compute_stream_sizes(store) -> list[dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {
        stream_name: {
            "streamName": stream_name,
            "eventCount": 0,
            "streamPosition": -1,
            "firstEventAt": None,
            "lastEventAt": None,
        }
        for stream_name, _prefix in STREAM_SIZE_FAMILIES
    }

    async for event in store.load_all(from_position=0):
        stream_id = str(event.get("stream_id") or "")
        recorded_at = _to_datetime(event.get("recorded_at"))

        for stream_name, prefix in STREAM_SIZE_FAMILIES:
            if not stream_id.startswith(prefix):
                continue

            snapshot = snapshots[stream_name]
            snapshot["eventCount"] += 1

            stream_position = int(event.get("stream_position", -1))
            if stream_position > snapshot["streamPosition"]:
                snapshot["streamPosition"] = stream_position

            if recorded_at is None:
                break

            current_first = _to_datetime(snapshot["firstEventAt"])
            if current_first is None or recorded_at < current_first:
                snapshot["firstEventAt"] = recorded_at.isoformat()

            current_last = _to_datetime(snapshot["lastEventAt"])
            if current_last is None or recorded_at > current_last:
                snapshot["lastEventAt"] = recorded_at.isoformat()
            break

    return [snapshots[stream_name] for stream_name, _prefix in STREAM_SIZE_FAMILIES]


async def build_event_throughput_snapshot(
    store,
    *,
    window_minutes: int = 60,
    bucket_minutes: int = 5,
) -> dict[str, Any]:
    if window_minutes <= 0:
        raise ValueError("window_minutes must be greater than zero")
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be greater than zero")

    events: list[datetime] = []
    latest_event_at: datetime | None = None
    async for event in store.load_all(from_position=0):
        if str(event.get("event_type") or "") in REPLAY_EVENT_TYPES:
            continue
        recorded_at = _to_datetime(event.get("recorded_at"))
        if recorded_at is None:
            continue
        events.append(recorded_at)
        if latest_event_at is None or recorded_at > latest_event_at:
            latest_event_at = recorded_at

    anchor = latest_event_at or datetime.now(timezone.utc)
    window_start = anchor - timedelta(minutes=window_minutes)
    bucket_count = max(1, math.ceil(window_minutes / bucket_minutes))
    bucket_span = timedelta(minutes=bucket_minutes)
    bucket_counts = [0 for _ in range(bucket_count)]

    for recorded_at in events:
        if recorded_at < window_start or recorded_at > anchor:
            continue
        bucket_index = int((recorded_at - window_start).total_seconds() // bucket_span.total_seconds())
        bucket_counts[min(bucket_index, bucket_count - 1)] += 1

    buckets: list[dict[str, Any]] = []
    peak_bucket_events = 0
    peak_bucket_label = "No events"
    for index, count in enumerate(bucket_counts):
        bucket_start = window_start + bucket_span * index
        bucket_end = min(bucket_start + bucket_span, anchor)
        label = bucket_start.astimezone(timezone.utc).strftime("%H:%M")
        buckets.append(
            {
                "label": label,
                "events": count,
                "startAt": bucket_start.isoformat(),
                "endAt": bucket_end.isoformat(),
            }
        )
        if count >= peak_bucket_events:
            peak_bucket_events = count
            peak_bucket_label = label

    total_events = sum(bucket_counts)
    events_per_minute = total_events / window_minutes

    return {
        "windowMinutes": window_minutes,
        "bucketMinutes": bucket_minutes,
        "windowStartAt": window_start.isoformat(),
        "windowEndAt": anchor.isoformat(),
        "latestEventAt": anchor.isoformat(),
        "totalEvents": total_events,
        "eventsPerMinute": round(events_per_minute, 2),
        "eventsPerHour": round(events_per_minute * 60, 2),
        "peakBucketEvents": peak_bucket_events,
        "peakBucketLabel": peak_bucket_label,
        "buckets": buckets,
    }
