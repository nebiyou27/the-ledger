"""PostgreSQL-backed event store and test double used throughout the starter."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator
from uuid import UUID, uuid4

from ledger.observability import StoreMetrics
from src.models.events import StoredEvent, StreamMetadata

try:
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - exercised only in lean local envs
    asyncpg = None

from ledger.exceptions import OptimisticConcurrencyError


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


def _row_to_stored_event(row: Any) -> StoredEvent:
    event = _row_to_event(row)
    return StoredEvent.model_validate(event)


def _normalize_append_metadata(
    metadata: dict | None,
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    base_metadata = dict(metadata or {})
    if correlation_id is not None:
        base_metadata["correlation_id"] = correlation_id
    if causation_id is not None:
        base_metadata["causation_id"] = causation_id
    return base_metadata


def _events_match(existing_events: list[dict[str, Any]], new_events: list[dict[str, Any]], base_metadata: dict[str, Any]) -> bool:
    if len(existing_events) != len(new_events):
        return False
    for existing, new in zip(existing_events, new_events):
        if existing.get("event_type") != new.get("event_type"):
            return False
        if int(existing.get("event_version", 1)) != int(new.get("event_version", 1)):
            return False
        if dict(existing.get("payload") or {}) != dict(new.get("payload") or {}):
            return False
        if dict(existing.get("metadata") or {}) != base_metadata:
            return False
    return True


def _row_to_stream_metadata(row: Any) -> StreamMetadata:
    metadata = dict(row)
    raw_json = metadata.get("metadata")
    if isinstance(raw_json, str):
        metadata["metadata"] = json.loads(raw_json)
    elif raw_json is None:
        metadata["metadata"] = {}
    return StreamMetadata.model_validate(metadata)


def _extract_application_id(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") or {}
    application_id = payload.get("application_id")
    return str(application_id) if application_id else None


async def _collect_application_history(store: Any, application_id: str) -> list[dict[str, Any]]:
    loader = getattr(store, "load_application_events", None)
    if loader is not None:
        return [dict(event) for event in await loader(application_id)]

    history: list[dict[str, Any]] = []
    async for event in store.load_all(from_position=0, application_id=application_id):
        history.append(dict(event))
    return history


SCHEMA_SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "event_store.sql"


class EventStore:
    """Append-only PostgreSQL event store with optimistic concurrency control."""

    def __init__(
        self,
        db_url: str,
        upcaster_registry=None,
        metrics: StoreMetrics | None = None,
        logger: logging.Logger | None = None,
    ):
        self.db_url = db_url
        self.upcasters = upcaster_registry or _default_upcaster_registry()
        self._metrics = metrics or StoreMetrics()
        self._logger = logger or logging.getLogger(__name__)
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

    async def load_application_events(self, application_id: str) -> list[dict[str, Any]]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT event_id, stream_id, stream_position, global_position, event_type, "
                "event_version, payload, metadata, recorded_at "
                "FROM events WHERE payload->>'application_id' = $1 "
                "ORDER BY global_position ASC",
                application_id,
            )
        return [_row_to_event(row) for row in rows]

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

        self._metrics.append_calls += 1
        pool = self._require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT stream_id, current_version, archived_at, metadata "
                    "FROM event_streams WHERE stream_id = $1 FOR UPDATE",
                    stream_id,
                )

                current = row["current_version"] if row else -1
                base_metadata = _normalize_append_metadata(metadata, correlation_id, causation_id)
                replay_start = current - len(events) + 1
                allow_idempotent_replay = bool(base_metadata.get("correlation_id") or base_metadata.get("causation_id"))
                if allow_idempotent_replay and replay_start >= 0:
                    replay_rows = await conn.fetch(
                        "SELECT event_type, event_version, payload, metadata, stream_position "
                        "FROM events WHERE stream_id = $1 AND stream_position >= $2 AND stream_position <= $3 "
                        "ORDER BY stream_position ASC",
                        stream_id,
                        replay_start,
                        current,
                    )
                    replay_events = [_row_to_event(row) for row in replay_rows]
                    if _events_match(replay_events, events, base_metadata):
                        positions = [int(row["stream_position"]) for row in replay_rows]
                        self._logger.info(
                            "event_store.append.idempotent_replay",
                            extra={
                                "stream_id": stream_id,
                                "event_count": len(events),
                                "expected_version": expected_version,
                                "current_version": current,
                            },
                        )
                        return positions
                if current != expected_version:
                    self._metrics.occ_failures += 1
                    self._logger.warning(
                        "event_store.occ",
                        extra={"stream_id": stream_id, "expected": expected_version, "actual": current},
                    )
                    raise OptimisticConcurrencyError.from_versions(stream_id, expected_version, current)

                if row and row["archived_at"] is not None:
                    self._metrics.append_failures += 1
                    self._logger.warning("event_store.append_archived", extra={"stream_id": stream_id})
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
                self._metrics.append_events += len(events)
                self._logger.info(
                    "event_store.append",
                    extra={
                        "stream_id": stream_id,
                        "event_count": len(events),
                        "expected_version": expected_version,
                        "new_version": new_version,
                    },
                )
                return positions

    async def load_stream(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: int | None = None,
    ) -> list[dict]:
        self._metrics.load_stream_calls += 1
        return [record.model_dump(mode="python") for record in await self.load_stream_records(stream_id, from_position, to_position)]

    async def load_stream_records(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: int | None = None,
    ) -> list[StoredEvent]:
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

        events = [_row_to_stored_event(row) for row in rows]
        if self.upcasters:
            application_id = next((app_id for app_id in (_extract_application_id(event.model_dump(mode="python")) for event in events) if app_id), None)
            history_events: list[dict[str, Any]] = []
            if application_id is not None:
                history_events = await _collect_application_history(self, application_id)

            upcasted: list[StoredEvent] = []
            for event in events:
                raw = event.model_dump(mode="python")
                context = None
                if history_events:
                    current_position = int(raw.get("global_position", -1))
                    context = {
                        "application_id": application_id,
                        "history_events": [history for history in history_events if int(history.get("global_position", -1)) < current_position],
                    }
                upcasted.append(StoredEvent.model_validate(self.upcasters.upcast(raw, context=context)))
            return upcasted
        return events

    async def load_all(
        self,
        from_position: int = 0,
        from_global_position: int | None = None,
        batch_size: int = 500,
        event_types: list[str] | None = None,
        application_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        self._metrics.load_all_calls += 1
        pool = self._require_pool()
        start_position = from_global_position if from_global_position is not None else from_position
        last_seen = start_position - 1
        history_by_application: dict[str, list[dict[str, Any]]] = {}

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
                    app_id = application_id or _extract_application_id(event)
                    context = None
                    if self.upcasters and app_id:
                        context = {
                            "application_id": app_id,
                            "history_events": [dict(previous) for previous in history_by_application.get(app_id, [])],
                        }
                    if self.upcasters:
                        event = self.upcasters.upcast(dict(event), context=context)
                    if app_id:
                        history_by_application.setdefault(app_id, []).append(dict(event))
                    yield event

                last_seen = rows[-1]["global_position"]
                if len(rows) < batch_size:
                    break

    async def get_event(self, event_id: UUID) -> dict | None:
        self._metrics.get_event_calls += 1
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
            app_id = _extract_application_id(event)
            context = None
            if app_id:
                history_events = await _collect_application_history(self, app_id)
                current_position = int(event.get("global_position", -1))
                context = {
                    "application_id": app_id,
                    "history_events": [history for history in history_events if int(history.get("global_position", -1)) < current_position],
                }
            event = self.upcasters.upcast(dict(event), context=context)
        return event

    async def archive_stream(self, stream_id: str) -> None:
        self._metrics.archive_stream_calls += 1
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE event_streams SET archived_at = NOW() WHERE stream_id = $1",
                stream_id,
            )

    async def get_stream_metadata(self, stream_id: str) -> dict[str, Any] | None:
        self._metrics.get_stream_metadata_calls += 1
        metadata = await self.get_stream_metadata_record(stream_id)
        if metadata is None:
            return None
        return metadata.model_dump(mode="python")

    async def get_stream_metadata_record(self, stream_id: str) -> StreamMetadata | None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT stream_id, aggregate_type, current_version, created_at, archived_at, metadata "
                "FROM event_streams WHERE stream_id = $1",
                stream_id,
            )
        if row is None:
            return None
        return _row_to_stream_metadata(row)

    async def save_checkpoint(self, projection_name: str, position: int) -> None:
        self._metrics.save_checkpoint_calls += 1
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
        self._metrics.load_checkpoint_calls += 1
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_position FROM projection_checkpoints WHERE projection_name = $1",
                projection_name,
            )
        return row["last_position"] if row else 0

    async def save_projection_dead_letter(
        self,
        projection_name: str,
        event: dict[str, Any],
        error: Exception,
        attempts: int,
    ) -> None:
        self._metrics.projection_dead_letter_calls += 1
        event_id = event.get("event_id")
        if event_id is None:
            raise ValueError("Dead-lettered projection events must include an event_id")

        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO projection_dead_letters "
                "(projection_name, event_id, stream_id, global_position, event_type, event_version, event_data, error_type, error_message, attempts) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)",
                projection_name,
                UUID(str(event_id)),
                str(event.get("stream_id") or ""),
                int(event.get("global_position", -1)),
                str(event.get("event_type") or "unknown"),
                int(event.get("event_version", 1)),
                _json_dumps(event),
                error.__class__.__name__,
                str(error),
                attempts,
            )

    async def load_projection_dead_letters(self, projection_name: str) -> list[dict[str, Any]]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, projection_name, event_id, stream_id, global_position, event_type, event_version, event_data, error_type, error_message, attempts, created_at "
                "FROM projection_dead_letters WHERE projection_name = $1 ORDER BY created_at DESC",
                projection_name,
            )
        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            event_data = record.get("event_data")
            if isinstance(event_data, str):
                record["event_data"] = json.loads(event_data)
            records.append(record)
        return records

    async def save_agent_checkpoint(self, session_id: str, checkpoint: dict[str, Any]) -> None:
        self._metrics.save_checkpoint_calls += 1
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agent_checkpoints "
                "(session_id, agent_type, application_id, last_completed_node, node_sequence, checkpoint_data, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW()) "
                "ON CONFLICT (session_id) DO UPDATE "
                "SET agent_type = EXCLUDED.agent_type, "
                "application_id = EXCLUDED.application_id, "
                "last_completed_node = EXCLUDED.last_completed_node, "
                "node_sequence = EXCLUDED.node_sequence, "
                "checkpoint_data = EXCLUDED.checkpoint_data, "
                "updated_at = NOW()",
                session_id,
                checkpoint.get("agent_type", "unknown"),
                checkpoint.get("application_id", "unknown"),
                checkpoint.get("last_completed_node"),
                int(checkpoint.get("node_sequence", 0)),
                _json_dumps(checkpoint),
            )

    async def load_agent_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        self._metrics.load_checkpoint_calls += 1
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT session_id, agent_type, application_id, last_completed_node, node_sequence, checkpoint_data, updated_at "
                "FROM agent_checkpoints WHERE session_id = $1",
                session_id,
            )
        if row is None:
            return None
        checkpoint = dict(row)
        raw = checkpoint.get("checkpoint_data")
        if isinstance(raw, str):
            checkpoint["checkpoint_data"] = json.loads(raw)
        elif raw is None:
            checkpoint["checkpoint_data"] = {}
        return checkpoint

    def get_metrics_snapshot(self) -> dict[str, int]:
        return self._metrics.snapshot()

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

    def __init__(
        self,
        upcaster_registry=None,
        metrics: StoreMetrics | None = None,
        logger: logging.Logger | None = None,
    ):
        self.upcasters = upcaster_registry or _default_upcaster_registry()
        self._metrics = metrics or StoreMetrics()
        self._logger = logger or logging.getLogger(__name__)
        self._streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._versions: dict[str, int] = {}
        self._global: list[dict[str, Any]] = []
        self._checkpoints: dict[str, int] = {}
        self._agent_checkpoints: dict[str, dict[str, Any]] = {}
        self._projection_dead_letters: list[dict[str, Any]] = []
        self._stream_metadata: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def stream_version(self, stream_id: str) -> int:
        return self._versions.get(stream_id, -1)

    async def load_application_events(self, application_id: str) -> list[dict[str, Any]]:
        return [
            dict(event)
            for event in self._global
            if str(event.get("payload", {}).get("application_id") or "") == application_id
        ]

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

        self._metrics.append_calls += 1
        async with self._locks[stream_id]:
            current = self._versions.get(stream_id, -1)
            base_metadata = _normalize_append_metadata(metadata, correlation_id, causation_id)
            replay_start = current - len(events) + 1
            allow_idempotent_replay = bool(base_metadata.get("correlation_id") or base_metadata.get("causation_id"))
            if allow_idempotent_replay and replay_start >= 0:
                replay_events = [dict(event) for event in self._streams.get(stream_id, [])[replay_start : current + 1]]
                if _events_match(replay_events, events, base_metadata):
                    positions = [event["stream_position"] for event in replay_events]
                    self._logger.info(
                        "event_store.append.idempotent_replay",
                        extra={
                            "stream_id": stream_id,
                            "event_count": len(events),
                            "expected_version": expected_version,
                            "current_version": current,
                        },
                    )
                    return positions
            if current != expected_version:
                self._metrics.occ_failures += 1
                self._logger.warning(
                    "event_store.occ",
                    extra={"stream_id": stream_id, "expected": expected_version, "actual": current},
                )
                raise OptimisticConcurrencyError.from_versions(stream_id, expected_version, current)

            stream_meta = self._stream_metadata.get(stream_id)
            if stream_meta and stream_meta.get("archived_at") is not None:
                self._metrics.append_failures += 1
                self._logger.warning("event_store.append_archived", extra={"stream_id": stream_id})
                raise ValueError(f"Cannot append to archived stream '{stream_id}'")

            if stream_id not in self._stream_metadata:
                self._stream_metadata[stream_id] = {
                    "aggregate_type": EventStore._aggregate_type_for(stream_id),
                    "created_at": _utcnow(),
                    "archived_at": None,
                    "metadata": {},
                }

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
            self._metrics.append_events += len(events)
            self._logger.info(
                "event_store.append",
                extra={
                    "stream_id": stream_id,
                    "event_count": len(events),
                    "expected_version": expected_version,
                    "new_version": self._versions[stream_id],
                },
            )
            return positions

    async def load_stream(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: int | None = None,
    ) -> list[dict]:
        self._metrics.load_stream_calls += 1
        return [
            record.model_dump(mode="python")
            for record in await self.load_stream_records(stream_id, from_position, to_position)
        ]

    async def load_stream_records(
        self,
        stream_id: str,
        from_position: int = 0,
        to_position: int | None = None,
    ) -> list[StoredEvent]:
        events = [
            StoredEvent.model_validate(dict(event))
            for event in self._streams.get(stream_id, [])
            if event["stream_position"] >= from_position
            and (to_position is None or event["stream_position"] <= to_position)
        ]
        if self.upcasters:
            application_id = next((app_id for app_id in (_extract_application_id(event.model_dump(mode="python")) for event in events) if app_id), None)
            history_events: list[dict[str, Any]] = []
            if application_id is not None:
                history_events = await _collect_application_history(self, application_id)

            upcasted: list[StoredEvent] = []
            for event in events:
                raw = event.model_dump(mode="python")
                context = None
                if history_events:
                    current_position = int(raw.get("global_position", -1))
                    context = {
                        "application_id": application_id,
                        "history_events": [history for history in history_events if int(history.get("global_position", -1)) < current_position],
                    }
                upcasted.append(StoredEvent.model_validate(self.upcasters.upcast(raw, context=context)))
            return upcasted
        return events

    async def load_all(
        self,
        from_position: int = 0,
        from_global_position: int | None = None,
        batch_size: int = 500,
        event_types: list[str] | None = None,
        application_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        self._metrics.load_all_calls += 1
        start_position = from_global_position if from_global_position is not None else from_position
        yielded = 0
        history_by_application: dict[str, list[dict[str, Any]]] = {}
        for event in self._global:
            if event["global_position"] < start_position:
                continue
            if event_types and event["event_type"] not in event_types:
                continue
            if application_id is not None and str(event.get("payload", {}).get("application_id")) != application_id:
                continue
            yielded += 1
            current = dict(event)
            app_id = application_id or _extract_application_id(current)
            context = None
            if self.upcasters and app_id:
                context = {
                    "application_id": app_id,
                    "history_events": [dict(previous) for previous in history_by_application.get(app_id, [])],
                }
            if self.upcasters:
                current = self.upcasters.upcast(current, context=context)
            if app_id:
                history_by_application.setdefault(app_id, []).append(dict(current))
            yield current
            if yielded % batch_size == 0:
                await asyncio.sleep(0)

    async def get_event(self, event_id: UUID | str) -> dict | None:
        self._metrics.get_event_calls += 1
        event_id_str = str(event_id)
        for event in self._global:
            if event["event_id"] == event_id_str:
                current = dict(event)
                if self.upcasters:
                    app_id = _extract_application_id(current)
                    context = None
                    if app_id:
                        history_events = [dict(candidate) for candidate in self._global if candidate.get("global_position", -1) < current.get("global_position", -1) and str(candidate.get("payload", {}).get("application_id") or "") == app_id]
                        context = {
                            "application_id": app_id,
                            "history_events": history_events,
                        }
                    current = self.upcasters.upcast(current, context=context)
                return current
        return None

    async def archive_stream(self, stream_id: str) -> None:
        self._metrics.archive_stream_calls += 1
        if stream_id in self._stream_metadata:
            self._stream_metadata[stream_id]["archived_at"] = _utcnow()

    async def get_stream_metadata(self, stream_id: str) -> dict[str, Any] | None:
        self._metrics.get_stream_metadata_calls += 1
        metadata = await self.get_stream_metadata_record(stream_id)
        if metadata is None:
            return None
        return metadata.model_dump(mode="python")

    async def get_stream_metadata_record(self, stream_id: str) -> StreamMetadata | None:
        meta = self._stream_metadata.get(stream_id)
        if meta is None:
            return None
        return StreamMetadata(
            stream_id=stream_id,
            aggregate_type=meta["aggregate_type"],
            current_version=self._versions.get(stream_id, -1),
            created_at=meta["created_at"],
            archived_at=meta["archived_at"],
            metadata=dict(meta["metadata"]),
        )

    async def save_checkpoint(self, projection_name: str, position: int) -> None:
        self._metrics.save_checkpoint_calls += 1
        self._checkpoints[projection_name] = position

    async def load_checkpoint(self, projection_name: str) -> int:
        self._metrics.load_checkpoint_calls += 1
        return self._checkpoints.get(projection_name, 0)

    async def save_projection_dead_letter(
        self,
        projection_name: str,
        event: dict[str, Any],
        error: Exception,
        attempts: int,
    ) -> None:
        self._metrics.projection_dead_letter_calls += 1
        self._projection_dead_letters.append(
            {
                "id": str(uuid4()),
                "projection_name": projection_name,
                "event_id": str(event.get("event_id") or ""),
                "stream_id": str(event.get("stream_id") or ""),
                "global_position": int(event.get("global_position", -1)),
                "event_type": str(event.get("event_type") or "unknown"),
                "event_version": int(event.get("event_version", 1)),
                "event_data": dict(event),
                "error_type": error.__class__.__name__,
                "error_message": str(error),
                "attempts": int(attempts),
                "created_at": _utcnow(),
            }
        )

    async def load_projection_dead_letters(self, projection_name: str) -> list[dict[str, Any]]:
        return [
            dict(record)
            for record in self._projection_dead_letters
            if record["projection_name"] == projection_name
        ]

    async def save_agent_checkpoint(self, session_id: str, checkpoint: dict[str, Any]) -> None:
        self._metrics.save_checkpoint_calls += 1
        self._agent_checkpoints[session_id] = dict(checkpoint)

    async def load_agent_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        self._metrics.load_checkpoint_calls += 1
        checkpoint = self._agent_checkpoints.get(session_id)
        return dict(checkpoint) if checkpoint is not None else None

    def get_metrics_snapshot(self) -> dict[str, int]:
        return self._metrics.snapshot()


def _default_upcaster_registry() -> UpcasterRegistry:
    """Default read-time migrations registered for event schema evolution."""
    # Import lazily to avoid circular imports at module import time.
    from ledger.upcasting.upcasters import build_default_upcaster_registry

    return build_default_upcaster_registry()







