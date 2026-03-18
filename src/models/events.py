"""Event models and exceptions exposed at the challenge deliverable path."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ledger.event_store import OptimisticConcurrencyError
from ledger.schema.events import *  # re-export canonical event catalogue


class DomainError(ValueError):
    """Domain invariant violation."""


class StoredEvent(BaseModel):
    """Canonical shape returned by event-store loads."""

    event_id: UUID | str
    stream_id: str
    stream_position: int
    global_position: int
    event_type: str
    event_version: int
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime | str | None = None


class StreamMetadata(BaseModel):
    """Metadata record for a stream."""

    aggregate_type: str
    current_version: int
    created_at: datetime | str | None = None
    archived_at: datetime | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "BaseEvent",
    "StoredEvent",
    "StreamMetadata",
    "OptimisticConcurrencyError",
    "DomainError",
]
