from __future__ import annotations

import asyncio
from typing import Any

from ledger.projections.base import Projection, ProjectionLag


class ProjectionDaemon:
    def __init__(self, store, projections: list[Projection], max_retries: int = 2):
        self._store = store
        self._projections = {p.name: p for p in projections}
        self._max_retries = max_retries
        self._running = False
        self._errors: dict[str, int] = {p.name: 0 for p in projections}

    async def run_forever(self, poll_interval_ms: int = 100, batch_size: int = 500) -> None:
        self._running = True
        while self._running:
            await self._process_batch(batch_size=batch_size)
            await asyncio.sleep(poll_interval_ms / 1000.0)

    def stop(self) -> None:
        self._running = False

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

        for event in events:
            global_position = int(event.get("global_position", -1))
            for projection in self._projections.values():
                projection.note_latest(event)

            for name, projection in self._projections.items():
                if global_position < next_positions[name]:
                    continue

                handled = projection.handles(str(event.get("event_type")))
                if handled:
                    applied = await self._apply_with_retry(projection, event)
                    if not applied:
                        # After exhausting retries, skip event and continue.
                        self._errors[name] += 1
                projection.mark_progress(event)
                next_positions[name] = global_position + 1
                await self._store.save_checkpoint(name, next_positions[name])
                processed_any += 1

        return processed_any

    async def _apply_with_retry(self, projection: Projection, event: dict[str, Any]) -> bool:
        attempts = 0
        while attempts <= self._max_retries:
            try:
                await projection.process_event(event)
                return True
            except Exception:
                attempts += 1
                if attempts > self._max_retries:
                    return False
                await asyncio.sleep(0)
        return False

    def get_lag(self, projection_name: str) -> ProjectionLag:
        projection = self._projections[projection_name]
        return projection.get_lag()

    def get_all_lags(self) -> dict[str, ProjectionLag]:
        return {name: projection.get_lag() for name, projection in self._projections.items()}

    def get_error_counts(self) -> dict[str, int]:
        return dict(self._errors)
