from __future__ import annotations

import asyncio
import logging
from typing import Any

from ledger.projections.base import Projection, ProjectionLag
from ledger.observability import ProjectionDaemonMetrics


class ProjectionDaemon:
    def __init__(
        self,
        store,
        projections: list[Projection],
        max_retries: int = 2,
        logger: logging.Logger | None = None,
        metrics: ProjectionDaemonMetrics | None = None,
    ):
        self._store = store
        self._projections = {p.name: p for p in projections}
        self._max_retries = max_retries
        self._logger = logger or logging.getLogger(__name__)
        self._metrics = metrics or ProjectionDaemonMetrics()
        self._running = False
        self._stop_event = asyncio.Event()
        self._errors: dict[str, int] = {p.name: 0 for p in projections}
        self._dead_letters: dict[str, int] = {p.name: 0 for p in projections}

    async def run_forever(self, poll_interval_ms: int = 100, batch_size: int = 500) -> None:
        self._running = True
        try:
            while self._running and not self._stop_event.is_set():
                await self._process_batch(batch_size=batch_size)
                await asyncio.sleep(poll_interval_ms / 1000.0)
        except asyncio.CancelledError:
            self._metrics.shutdowns += 1
            self._logger.info("projection_daemon.cancelled")
            raise
        finally:
            self._running = False
            self._stop_event.set()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()

    async def _process_batch(self, batch_size: int = 500) -> int:
        if not self._projections:
            return 0

        next_positions: dict[str, int] = {}
        for name in self._projections:
            next_positions[name] = int(await self._store.load_checkpoint(name))

        from_position = min(next_positions.values())
        processed_any = 0

        events: list[dict[str, Any]] = []
        async for event in self._store.load_all(from_position=from_position, batch_size=batch_size):
            events.append(event)
            if len(events) >= batch_size:
                break

        if not events:
            return 0

        self._metrics.batches_processed += 1
        for event in events:
            global_position = int(event.get("global_position", -1))
            self._metrics.events_seen += 1
            for projection in self._projections.values():
                projection.note_latest(event)

            for name, projection in self._projections.items():
                if global_position < next_positions[name]:
                    continue

                handled = projection.handles(str(event.get("event_type")))
                if handled:
                    applied, error = await self._apply_with_retry(projection, event)
                    if not applied:
                        dead_lettered = await self._dead_letter_with_retry(name, event, error)
                        if dead_lettered:
                            self._errors[name] += 1
                            projection.mark_progress(event)
                            checkpoint_position = global_position + 1
                            if await self._save_checkpoint_with_retry(name, checkpoint_position):
                                next_positions[name] = checkpoint_position
                                processed_any += 1
                        continue

                projection.mark_progress(event)
                checkpoint_position = global_position + 1
                if await self._save_checkpoint_with_retry(name, checkpoint_position):
                    next_positions[name] = checkpoint_position
                    processed_any += 1

        return processed_any

    async def _apply_with_retry(self, projection: Projection, event: dict[str, Any]) -> tuple[bool, Exception | None]:
        attempts = 0
        last_error: Exception | None = None
        while attempts <= self._max_retries:
            try:
                await projection.process_event(event)
                return True, None
            except Exception as exc:
                last_error = exc
                attempts += 1
                self._metrics.retries += 1
                self._logger.warning(
                    "projection_daemon.retry",
                    extra={
                        "projection": projection.name,
                        "event_type": event.get("event_type"),
                        "attempt": attempts,
                        "error": exc.__class__.__name__,
                    },
                )
                if attempts > self._max_retries:
                    self._metrics.failures += 1
                    return False, last_error
                await asyncio.sleep(min(0.05 * attempts, 0.5))
        return False, last_error

    async def _dead_letter_with_retry(
        self,
        projection_name: str,
        event: dict[str, Any],
        error: Exception | None,
    ) -> bool:
        attempts = 0
        dead_letter_error = error or RuntimeError("projection processing failed")
        while attempts <= self._max_retries:
            try:
                saver = getattr(self._store, "save_projection_dead_letter", None)
                if saver is None:
                    raise AttributeError("store does not support projection dead letters")
                await saver(projection_name, event, dead_letter_error, attempts)
                self._metrics.dead_letters += 1
                self._dead_letters[projection_name] += 1
                self._logger.error(
                    "projection_daemon.dead_lettered",
                    extra={
                        "projection": projection_name,
                        "event_type": event.get("event_type"),
                        "global_position": event.get("global_position"),
                        "attempts": attempts,
                        "error": dead_letter_error.__class__.__name__,
                    },
                )
                return True
            except Exception as exc:
                attempts += 1
                self._metrics.retries += 1
                self._logger.warning(
                    "projection_daemon.dead_letter_retry",
                    extra={
                        "projection": projection_name,
                        "event_type": event.get("event_type"),
                        "attempt": attempts,
                        "error": exc.__class__.__name__,
                    },
                )
                if attempts > self._max_retries:
                    self._metrics.failures += 1
                    return False
                await asyncio.sleep(min(0.05 * attempts, 0.5))
        return False

    async def _save_checkpoint_with_retry(self, projection_name: str, position: int) -> bool:
        attempts = 0
        while attempts <= self._max_retries:
            try:
                await self._store.save_checkpoint(projection_name, position)
                return True
            except Exception as exc:
                attempts += 1
                self._metrics.retries += 1
                self._logger.warning(
                    "projection_daemon.checkpoint_retry",
                    extra={"projection": projection_name, "position": position, "attempt": attempts, "error": exc.__class__.__name__},
                )
                if attempts > self._max_retries:
                    self._metrics.failures += 1
                    return False
                await asyncio.sleep(min(0.05 * attempts, 0.5))
        return False

    def get_lag(self, projection_name: str) -> ProjectionLag:
        projection = self._projections[projection_name]
        return projection.get_lag()

    def get_all_lags(self) -> dict[str, ProjectionLag]:
        return {name: projection.get_lag() for name, projection in self._projections.items()}

    def get_error_counts(self) -> dict[str, int]:
        return dict(self._errors)

    def get_dead_letter_counts(self) -> dict[str, int]:
        return dict(self._dead_letters)

    def get_metrics(self) -> dict[str, int]:
        return self._metrics.snapshot()
