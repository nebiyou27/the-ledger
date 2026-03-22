from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class StoreMetrics:
    append_calls: int = 0
    append_events: int = 0
    append_failures: int = 0
    occ_failures: int = 0
    load_stream_calls: int = 0
    load_all_calls: int = 0
    get_event_calls: int = 0
    get_stream_metadata_calls: int = 0
    save_checkpoint_calls: int = 0
    load_checkpoint_calls: int = 0
    archive_stream_calls: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class ProjectionDaemonMetrics:
    batches_processed: int = 0
    events_seen: int = 0
    retries: int = 0
    failures: int = 0
    shutdowns: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)
