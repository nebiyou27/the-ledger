from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


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
