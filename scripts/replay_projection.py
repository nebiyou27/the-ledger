from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from ledger.event_store import EventStore
from ledger.metrics import REPLAY_EVENT_TYPES, set_replay_progress
from ledger.projections import (
    AgentPerformanceProjection,
    ApplicationSummaryProjection,
    ComplianceAuditProjection,
    ManualReviewsProjection,
    Projection,
    ProjectionDaemon,
)


ProjectionFactory = Callable[[], Projection]

PROJECTION_FACTORIES: dict[str, ProjectionFactory] = {
    "application_summary": ApplicationSummaryProjection,
    "agent_performance": AgentPerformanceProjection,
    "compliance_audit": ComplianceAuditProjection,
    "manual_reviews": ManualReviewsProjection,
}

PROJECTION_ALIASES: dict[str, str] = {
    "ApplicationSummaryProjection": "application_summary",
    "AgentPerformanceProjection": "agent_performance",
    "ComplianceAuditProjection": "compliance_audit",
    "ManualReviewsProjection": "manual_reviews",
}

PROJECTION_ORDER = [
    "application_summary",
    "agent_performance",
    "compliance_audit",
    "manual_reviews",
]


def _default_db_url() -> str:
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DB_URL")
        or "postgresql://postgres:apex@localhost:5432/apex_ledger"
    )


def _normalize_projection_name(value: str) -> str:
    key = value.strip()
    if key in PROJECTION_FACTORIES:
        return key
    if key in PROJECTION_ALIASES:
        return PROJECTION_ALIASES[key]
    raise KeyError(key)


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--:--"
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


async def _count_events(store: EventStore) -> int:
    pool = store._require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS total_events FROM events WHERE event_type <> ALL($1::text[])",
            list(REPLAY_EVENT_TYPES),
        )
    return int(row["total_events"]) if row else 0


async def _clear_checkpoint(store: EventStore, projection_name: str) -> None:
    pool = store._require_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM projection_checkpoints WHERE projection_name = $1", projection_name)


async def _replay_projection(store: EventStore, projection_name: str) -> None:
    factory = PROJECTION_FACTORIES[projection_name]
    projection = factory()
    daemon = ProjectionDaemon(store, [projection])
    total_events = await _count_events(store)
    started_at = time.perf_counter()
    started_iso = datetime.now(timezone.utc).isoformat()
    next_progress_mark = 100

    await _clear_checkpoint(store, projection.name)
    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_STARTED",
            "projection_name": projection.name,
            "events_processed": 0,
            "total_events": total_events,
            "percent_complete": 0.0,
            "started_at": started_iso,
            "estimated_completion": None,
        },
    )

    print(
        f"Starting replay for {projection.__class__.__name__} "
        f"({projection.name}) from global_position=0 with {total_events} events",
        flush=True,
    )

    if total_events == 0:
        await set_replay_progress(
            store,
            {
                "event_type": "REPLAY_COMPLETED",
                "projection_name": projection.name,
                "events_processed": 0,
                "total_events": 0,
                "percent_complete": 100.0,
                "started_at": started_iso,
                "estimated_completion": started_iso,
            },
        )
        print(f"[{projection.name}] No events found. Checkpoint cleared and replay complete.", flush=True)
        return

    batch_size = 500
    processed_events = 0

    try:
        while True:
            processed = await daemon._process_batch(batch_size=batch_size)
            processed_events = min(total_events, processed_events + processed)

            checkpoint = await store.load_checkpoint(projection.name)
            current_global_position = checkpoint - 1
            percent = min(100.0, (processed_events / total_events) * 100.0) if total_events else 100.0
            elapsed = time.perf_counter() - started_at
            eta_seconds: float | None = None
            if processed_events > 0 and processed_events < total_events:
                eta_seconds = (elapsed / processed_events) * (total_events - processed_events)

            while processed_events >= next_progress_mark and next_progress_mark < total_events:
                progress_percent = min(100.0, (next_progress_mark / total_events) * 100.0)
                eta_iso: str | None = None
                if processed_events > 0 and processed_events < total_events:
                    projected_remaining = total_events - next_progress_mark
                    eta_seconds_at_mark = max(0.0, (elapsed / processed_events) * projected_remaining)
                    eta_iso = (datetime.now(timezone.utc) + timedelta(seconds=eta_seconds_at_mark)).isoformat()
                await set_replay_progress(
                    store,
                    {
                        "event_type": "REPLAY_PROGRESS",
                        "projection_name": projection.name,
                        "events_processed": next_progress_mark,
                        "total_events": total_events,
                        "percent_complete": progress_percent,
                        "started_at": started_iso,
                        "estimated_completion": eta_iso,
                    },
                )
                next_progress_mark += 100

            print(
                f"[{projection.name}] global_position={current_global_position} "
                f"total_events={total_events} "
                f"progress={percent:.2f}% "
                f"eta={_format_duration(eta_seconds)}",
                flush=True,
            )

            if processed == 0:
                break

        final_checkpoint = await store.load_checkpoint(projection.name)
        if final_checkpoint >= total_events:
            await set_replay_progress(
                store,
                {
                    "event_type": "REPLAY_COMPLETED",
                    "projection_name": projection.name,
                    "events_processed": total_events,
                    "total_events": total_events,
                    "percent_complete": 100.0,
                    "started_at": started_iso,
                    "estimated_completion": datetime.now(timezone.utc).isoformat(),
                },
            )
            print(
                f"[{projection.name}] Replay complete. "
                f"checkpoint={final_checkpoint}, total_events={total_events}.",
                flush=True,
            )
        else:
            await set_replay_progress(
                store,
                {
                    "event_type": "REPLAY_FAILED",
                    "projection_name": projection.name,
                    "events_processed_before_failure": processed_events,
                    "total_events": total_events,
                    "percent_complete": round((processed_events / total_events) * 100.0, 2) if total_events else 0.0,
                    "started_at": started_iso,
                    "reason": "Replay stopped before reaching the end of the event log.",
                },
            )
            print(
                f"[{projection.name}] Replay stopped early. "
                f"checkpoint={final_checkpoint}, total_events={total_events}.",
                flush=True,
            )
    except Exception as exc:
        await set_replay_progress(
            store,
            {
                "event_type": "REPLAY_FAILED",
                "projection_name": projection.name,
                "events_processed_before_failure": processed_events,
                "total_events": total_events,
                "percent_complete": round((processed_events / total_events) * 100.0, 2) if total_events else 0.0,
                "started_at": started_iso,
                "reason": str(exc),
            },
        )
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clear a projection checkpoint and replay the projection from the "
            "beginning of the event log."
        )
    )
    parser.add_argument(
        "projection",
        nargs="?",
        help=(
            "Projection name or class name, for example "
            "ComplianceAuditProjection or compliance_audit."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Replay every registered projection in sequence.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required safety flag. Replay will overwrite the current projection state.",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Database URL, defaults to DATABASE_URL / TEST_DB_URL / the local postgres default.",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    if not args.confirm:
        raise SystemExit(
            "Refusing to run without --confirm. Replay will overwrite the current projection state."
        )

    if args.all and args.projection:
        raise SystemExit("Use either a projection name or --all, not both.")

    if not args.all and not args.projection:
        raise SystemExit("Provide a projection name or pass --all.")

    db_url = args.db_url or _default_db_url()
    store = EventStore(db_url)
    await store.connect()
    try:
        await store.initialize_schema()

        if args.all:
            target_projections = PROJECTION_ORDER
        else:
            try:
                target_projections = [_normalize_projection_name(args.projection)]
            except KeyError as exc:
                available = ", ".join(
                    [*PROJECTION_ORDER, *sorted(PROJECTION_ALIASES.keys())]
                )
                raise SystemExit(
                    f"Unknown projection '{exc.args[0]}'. Available projections: {available}"
                ) from None

        for projection_name in target_projections:
            await _replay_projection(store, projection_name)

        print("Projection replay finished successfully.", flush=True)
        return 0
    except Exception as exc:
        print(f"Projection replay failed: {exc}", file=sys.stderr, flush=True)
        raise
    finally:
        await store.close()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
