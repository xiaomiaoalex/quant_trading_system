"""Research worker process. Claims durable jobs and publishes under fencing tokens."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket

from trader.trading.jobs import PostgresResearchStore, ResearchWorker
from trader.trading.pg import create_pool
from trader.trading.research import ResearchExecutor

logger = logging.getLogger(__name__)


async def run(once: bool = False) -> None:
    pool = await create_pool(min_size=1, max_size=5)
    try:
        store = PostgresResearchStore(pool)
        await store.initialize()
        executor = ResearchExecutor(store)
        worker = ResearchWorker(
            store,
            owner=os.getenv("QTS_WORKER_OWNER", f"{socket.gethostname()}:{os.getpid()}"),
            lease_seconds=int(os.getenv("QTS_WORKER_LEASE_SECONDS", "30")),
        )
        interval = float(os.getenv("QTS_WORKER_POLL_SECONDS", "2"))
        while True:
            finished = await worker.run_once(executor.execute)
            if finished is not None:
                logger.info(
                    "Research job finished: run_id=%s status=%s error=%s",
                    finished["run_id"],
                    finished["status"],
                    finished.get("error"),
                )
            if once:
                return
            await asyncio.sleep(interval)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS research worker")
    parser.add_argument("--once", action="store_true", help="claim and run at most one job")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
