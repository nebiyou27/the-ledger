"""PostgreSQL-backed event store and test double used throughout the starter."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator
from uuid import UUID, uuid4

try:
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - exercised only in lean local envs
    asyncpg = None


@dataclass(slots=True)
class OptimisticConcurrencyError(Exception):
    """Raised when expected_version doesn't match the current stream version."""

    stream_id: str
    expected: int
    actual: int

    @classmethod
    def from_versions(cls, stream_id: str, expected: int, actual: int) -> "OptimisticConcurrencyError":
        return cls(stream_id=stream_id, expected=expected, actual=actual)

    def __post_init__(self) -> None:
        Exception.__init__(self, f"OCC on '{self.stream_id}': expected v{self.expected}, actual v{self.actual}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_recorded_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _utcnow()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _row_to_event(row: Any) -> dict[str, Any]:
    event = dict(row)
    payload = event.get("payload")
    metadata = event.get("metadata")

    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    event["payload"] = dict(payload or {})
    event["metadata"] = dict(metadata or {})
    return event


SCHEMA_SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "event_store.sql"


class EventStore:
    """Append-only PostgreSQL event store with optimistic concurrency control."""

    def __init__(self, db_url: str, upcaster_registry=None):
        self.db_url = db_url
        self.upcasters = upcaster_registry or _default_upcaster_registry()
        self._pool: Any | None = None

    async def connect(self) -> None:
        if asyncpg is None:
            raise RuntimeError("asyncpg is required to use PostgreSQL EventStore")
        self._pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=10)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def initialize_schema(self) -> None:
        pool = self._require_pool()
        schema_sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        async with pool.acquire() as conn:
            await conn.execute(schema_sql)

    def _require_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("EventStore.connect() must be called before use")
        return self._pool

    async def stream_version(self, stream_id: str) -> int:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT current_version FROM event_streams WHERE stream_id = $1",
                stream_id,
            )
            return row["current_version"] if row else -1

    async def append(
        self,
        stream_id: str,
        events: list[dict],
        expected_version: int,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        metadata: dict | None = None,
    ) -> list[int]:
        if not events:
            return []

        pool = self._require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT stream_id, current_version, archived_at, metadata "
                    "FROM event_streams WHERE stream_id = $1 FOR UPDATE",
                    stream_id,
                )

                current = row["current_version"] if row else -1
                if current != expected_version:
                    raise OptimisticConcurrencyError.from_versions(stream_id, expected_version, current)

                if row and row["archived_at"] is not None:
                    raise ValueError(f"Cannot append to archived stream '{stream_id}'")

                if row is None:
                    await conn.execute(
                        "INSERT INTO event_streams "
                        "(stream_id, aggregate_type, current_version, metadata) "
                        "VALUES ($1, $2, $3, $4::jsonb)",
                        stream_id,
                        self._aggregate_type_for(stream_id),
                        -1,
                        _json_dumps({}),
                    )

                base_metadata = dict(metadata or {})
                if correlation_id is not None:
                    base_metadata["correlation_id"] = correlation_id
                if causation_id is not None:
                    base_metadata["causation_id"] = causation_id

                positions: list[int] = []
                new_version = expected_version

                for offset, event in enumerate(events):
                    position = expected_version + offset + 1
                    stored_at = _normalize_recorded_at(event.get("recorded_at"))
                    event_row = await conn.fetchrow(
                        "INSERT INTO events "
                        "(stream_id, stream_position, event_type, event_version, payload, metadata, recorded_at) "
                        "VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7) "
                        "RETURNING event_id, stream_position, global_position",
                        stream_id,
                        position,
                        event["event_type"],
                        event.get("event_version", 1),
                        _json_dumps(event.get("payload", {})),
                        _json_dumps(base_metadata),
                        stored_at,
                    )

                    await conn.execute(
                        "INSERT INTO outbox (event_id, destination, payload) "
                        "VALUES ($1, $2, $3::jsonb)",
                        event_row["event_id"],
                        base_metadata.get("outbox_destination", "internal.projections"),
                        _json_dumps(
                            {
                                "event_id": str(event_row["event_id"]),
                                "stream_id": stream_id,
                                "stream_position": event_row["stream_position"],
                                "global_position": event_row["global_position"],
                                "event_type": event["event_type"],
                                "event_version": event.get("event_version", 1),
                                "payload": event.get("payload", {}),
                                "metadata": base_metadata,
                                "recorded_at": stored_at,
                            }
                        ),
                    )

                    positions.append(position)
                    new_version = position

                await conn.execute(
                    "UPDATE event_streams SET current_version = $1 WHERE stream_id = $2",
                    new_version,
                    stream_id,
                )
                return positions

    async def load_stream(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: int | None = None,
    ) -> list[dict]:
        pool = self._require_pool()
        query = (
            "SELECT event_id, stream_id, stream_position, global_position, event_type, "
            "event_version, payload, metadata, recorded_at "
            "FROM events WHERE stream_id = $1 AND stream_position >= $2"
        )
        params: list[Any] = [stream_id, from_position]
        if to_position is not None:
            query += " AND stream_position <= $3"
            params.append(to_position)
        query += " ORDER BY stream_position ASC"

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        events = [_row_to_event(row) for row in rows]
        if self.upcasters:
            return [self.upcasters.upcast(dict(event)) for event in events]
        return events

    async def load_all(
        self,
        from_position: int = 0,
        batch_size: int = 500,
        event_types: list[str] | None = None,
        application_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        pool = self._require_pool()
        last_seen = from_position - 1

        async with pool.acquire() as conn:
            while True:
                if event_types and application_id is not None:
                    rows = await conn.fetch(
                        "SELECT event_id, stream_id, stream_position, global_position, event_type, "
                        "event_version, payload, metadata, recorded_at "
                        "FROM events WHERE global_position > $1 AND event_type = ANY($2::text[]) "
                        "AND payload->>'application_id' = $3 "
                        "ORDER BY global_position ASC LIMIT $4",
                        last_seen,
                        event_types,
                        application_id,
                        batch_size,
                    )
                elif event_types:
                    rows = await conn.fetch(
                        "SELECT event_id, stream_id, stream_position, global_position, event_type, "
                        "event_version, payload, metadata, recorded_at "
                        "FROM events WHERE global_position > $1 AND event_type = ANY($2::text[]) "
                        "ORDER BY global_position ASC LIMIT $3",
                        last_seen,
                        event_types,
                        batch_size,
                    )
                elif application_id is not None:
                    rows = await conn.fetch(
                        "SELECT event_id, stream_id, stream_position, global_position, event_type, "
                        "event_version, payload, metadata, recorded_at "
                        "FROM events WHERE global_position > $1 AND payload->>'application_id' = $2 "
                        "ORDER BY global_position ASC LIMIT $3",
                        last_seen,
                        application_id,
                        batch_size,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT event_id, stream_id, stream_position, global_position, event_type, "
                        "event_version, payload, metadata, recorded_at "
                        "FROM events WHERE global_position > $1 "
                        "ORDER BY global_position ASC LIMIT $2",
                        last_seen,
                        batch_size,
                    )

                if not rows:
                    break

                for row in rows:
                    event = _row_to_event(row)
                    if self.upcasters:
                        event = self.upcasters.upcast(dict(event))
                    yield event

                last_seen = rows[-1]["global_position"]
                if len(rows) < batch_size:
                    break

    async def get_event(self, event_id: UUID) -> dict | None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT event_id, stream_id, stream_position, global_position, event_type, "
                "event_version, payload, metadata, recorded_at "
                "FROM events WHERE event_id = $1",
                event_id,
            )
        if row is None:
            return None
        event = _row_to_event(row)
        if self.upcasters:
            event = self.upcasters.upcast(dict(event))
        return event

    async def archive_stream(self, stream_id: str) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE event_streams SET archived_at = NOW() WHERE stream_id = $1",
                stream_id,
            )

    async def get_stream_metadata(self, stream_id: str) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT stream_id, aggregate_type, current_version, created_at, archived_at, metadata "
                "FROM event_streams WHERE stream_id = $1",
                stream_id,
            )
        if row is None:
            return None
        metadata = dict(row)
        metadata["metadata"] = dict(metadata.get("metadata") or {})
        return metadata

    async def save_checkpoint(self, projection_name: str, position: int) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO projection_checkpoints (projection_name, last_position, updated_at) "
                "VALUES ($1, $2, NOW()) "
                "ON CONFLICT (projection_name) DO UPDATE "
                "SET last_position = EXCLUDED.last_position, updated_at = NOW()",
                projection_name,
                position,
            )

    async def load_checkpoint(self, projection_name: str) -> int:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_position FROM projection_checkpoints WHERE projection_name = $1",
                projection_name,
            )
        return row["last_position"] if row else 0

    @staticmethod
    def _aggregate_type_for(stream_id: str) -> str:
        return stream_id.split("-", 1)[0] if "-" in stream_id else stream_id


class UpcasterRegistry:
    """Transforms old event versions to current versions on load."""

    def __init__(self):
        self._upcasters: dict[str, dict[int, callable]] = {}

    def upcaster(self, event_type: str, from_version: int, to_version: int):
        if to_version != from_version + 1:
            raise ValueError("Upcasters must advance exactly one version at a time")

        def decorator(fn):
            self._upcasters.setdefault(event_type, {})[from_version] = fn
            return fn

        return decorator

    def upcast(self, event: dict) -> dict:
        event = dict(event)
        event["payload"] = dict(event.get("payload") or {})

        event_type = event["event_type"]
        version = event.get("event_version", 1)
        chain = self._upcasters.get(event_type, {})
        while version in chain:
            event["payload"] = chain[version](dict(event["payload"]))
            version += 1
            event["event_version"] = version
        return event


class InMemoryEventStore:
    """Async-safe in-memory store used by the starter's fast tests."""

    def __init__(self, upcaster_registry=None):
        self.upcasters = upcaster_registry or _default_upcaster_registry()
        self._streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._versions: dict[str, int] = {}
        self._global: list[dict[str, Any]] = []
        self._checkpoints: dict[str, int] = {}
        self._stream_metadata: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def stream_version(self, stream_id: str) -> int:
        return self._versions.get(stream_id, -1)

    async def append(
        self,
        stream_id: str,
        events: list[dict],
        expected_version: int,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        metadata: dict | None = None,
    ) -> list[int]:
        if not events:
            return []

        async with self._locks[stream_id]:
            current = self._versions.get(stream_id, -1)
            if current != expected_version:
                raise OptimisticConcurrencyError.from_versions(stream_id, expected_version, current)

            stream_meta = self._stream_metadata.get(stream_id)
            if stream_meta and stream_meta.get("archived_at") is not None:
                raise ValueError(f"Cannot append to archived stream '{stream_id}'")

            if stream_id not in self._stream_metadata:
                self._stream_metadata[stream_id] = {
                    "aggregate_type": EventStore._aggregate_type_for(stream_id),
                    "created_at": _utcnow(),
                    "archived_at": None,
                    "metadata": {},
                }

            base_metadata = dict(metadata or {})
            if correlation_id is not None:
                base_metadata["correlation_id"] = correlation_id
            if causation_id is not None:
                base_metadata["causation_id"] = causation_id

            positions: list[int] = []
            for offset, event in enumerate(events):
                position = current + offset + 1
                stored = {
                    "event_id": str(uuid4()),
                    "stream_id": stream_id,
                    "stream_position": position,
                    "global_position": len(self._global),
                    "event_type": event["event_type"],
                    "event_version": event.get("event_version", 1),
                    "payload": dict(event.get("payload", {})),
                    "metadata": dict(base_metadata),
                    "recorded_at": _normalize_recorded_at(event.get("recorded_at")),
                }
                self._streams[stream_id].append(stored)
                self._global.append(stored)
                positions.append(position)

            self._versions[stream_id] = current + len(events)
            return positions

    async def load_stream(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: int | None = None,
    ) -> list[dict]:
        events = [
            dict(event)
            for event in self._streams.get(stream_id, [])
            if event["stream_position"] >= from_position
            and (to_position is None or event["stream_position"] <= to_position)
        ]
        if self.upcasters:
            return [self.upcasters.upcast(dict(event)) for event in events]
        return events

    async def load_all(
        self,
        from_position: int = 0,
        batch_size: int = 500,
        event_types: list[str] | None = None,
        application_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        yielded = 0
        for event in self._global:
            if event["global_position"] < from_position:
                continue
            if event_types and event["event_type"] not in event_types:
                continue
            if application_id is not None and str(event.get("payload", {}).get("application_id")) != application_id:
                continue
            yielded += 1
            if self.upcasters:
                yield self.upcasters.upcast(dict(event))
            else:
                yield dict(event)
            if yielded % batch_size == 0:
                await asyncio.sleep(0)

    async def get_event(self, event_id: UUID | str) -> dict | None:
        event_id_str = str(event_id)
        for event in self._global:
            if event["event_id"] == event_id_str:
                return dict(event)
        return None

    async def archive_stream(self, stream_id: str) -> None:
        if stream_id in self._stream_metadata:
            self._stream_metadata[stream_id]["archived_at"] = _utcnow()

    async def get_stream_metadata(self, stream_id: str) -> dict[str, Any] | None:
        meta = self._stream_metadata.get(stream_id)
        if meta is None:
            return None
        return {
            "aggregate_type": meta["aggregate_type"],
            "current_version": self._versions.get(stream_id, -1),
            "created_at": meta["created_at"],
            "archived_at": meta["archived_at"],
            "metadata": dict(meta["metadata"]),
        }

    async def save_checkpoint(self, projection_name: str, position: int) -> None:
        self._checkpoints[projection_name] = position

    async def load_checkpoint(self, projection_name: str) -> int:
        return self._checkpoints.get(projection_name, 0)


def _default_upcaster_registry() -> UpcasterRegistry:
    """Default read-time migrations registered for event schema evolution."""
    # Import lazily to avoid circular imports at module import time.
    from ledger.upcasting.upcasters import build_default_upcaster_registry

    return build_default_upcaster_registry()







