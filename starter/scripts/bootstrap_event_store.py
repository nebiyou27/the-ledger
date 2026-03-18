from __future__ import annotations

import asyncio
import os

from ledger.event_store import EventStore


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DB_URL")
    if not db_url:
        raise SystemExit("Set DATABASE_URL or TEST_DB_URL first.")

    store = EventStore(db_url)
    await store.connect()
    try:
        await store.initialize_schema()
        print("Event store schema initialized.")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
