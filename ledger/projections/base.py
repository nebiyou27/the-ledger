from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _to_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


@dataclass
class ProjectionLag:
    positions_behind: int
    millis: int


class Projection:
    """Base class for projection handlers."""

    name: str

    def __init__(self, name: str):
        self.name = name
        self._last_processed_position = -1
        self._last_processed_at: datetime | None = None
        self._latest_seen_position = -1
        self._latest_seen_at: datetime | None = None

    def handles(self, event_type: str) -> bool:
        return True

    async def process_event(self, event: dict[str, Any]) -> None:
        raise NotImplementedError

    def mark_progress(self, event: dict[str, Any]) -> None:
        position = int(event.get("global_position", -1))
        self._last_processed_position = position
        self._latest_seen_position = max(self._latest_seen_position, position)
        recorded_at = _to_utc(event.get("recorded_at"))
        if recorded_at is not None:
            self._last_processed_at = recorded_at
            if self._latest_seen_at is None or recorded_at > self._latest_seen_at:
                self._latest_seen_at = recorded_at

    def note_latest(self, event: dict[str, Any]) -> None:
        position = int(event.get("global_position", -1))
        self._latest_seen_position = max(self._latest_seen_position, position)
        recorded_at = _to_utc(event.get("recorded_at"))
        if recorded_at is not None and (self._latest_seen_at is None or recorded_at > self._latest_seen_at):
            self._latest_seen_at = recorded_at

    def get_lag(self) -> ProjectionLag:
        positions_behind = max(0, self._latest_seen_position - self._last_processed_position)
        if self._latest_seen_at is None or self._last_processed_at is None:
            return ProjectionLag(positions_behind=positions_behind, millis=0)
        delta_ms = int((self._latest_seen_at - self._last_processed_at).total_seconds() * 1000)
        return ProjectionLag(positions_behind=positions_behind, millis=max(0, delta_ms))
