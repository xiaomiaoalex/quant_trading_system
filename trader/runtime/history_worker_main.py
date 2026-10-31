"""Standalone history archive ingestion worker.

Runs the Binance Vision archive import loop outside the API process:

- A PostgreSQL session advisory lock guarantees that only one worker owns the
  database. Only the owner performs the startup recovery, so a fresh instance
  can never mark another instance's running job as interrupted.
- The startup recovery is retried until it succeeds; no job is claimed before
  the recovery completed.
- A single job runs at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
from pathlib import Path

from dotenv import load_dotenv

from trader.adapters.persistence.history_dataset_store import (
    HISTORY_WORKER_LOCK_KEY,
    HistoryDatasetStore,
)
from trader.adapters.persistence.research_history_bridge_store import ResearchHistoryBridgeStore
from trader.services.history_data_ingestion import HistoryIngestionService, HistoryJobWorker

_ROOT = Path(__file__).resolve().parents[2]
for _env_file in (_ROOT / ".env", _ROOT / ".env.postgres"):
    if _env_file.exists():
        load_dotenv(_env_file, override=False)

logger = logging.getLogger(__name__)


def _owner() -> str:
    return os.getenv("QTS_HISTORY_WORKER_OWNER", f"{socket.gethostname()}:{os.getpid()}")


async def run(once: bool = False) -> None:
    store = HistoryDatasetStore()
    await store.connect()
    try:
        if not await store.try_acquire_worker_lock(HISTORY_WORKER_LOCK_KEY):
            logger.error(
                "Another history worker already owns this database (owner=%s); exiting",
                _owner(),
            )
            return
        try:
            bridge = ResearchHistoryBridgeStore(storage=store.storage)
            await bridge.ensure_research_schema()
            service = HistoryIngestionService(store=store, bridge_store=bridge)
            if once:
                processed = await service.process_next_queued_job()
                if processed is not None:
                    logger.info(
                        "History job finished: job_id=%s status=%s",
                        processed.get("job_id"),
                        processed.get("status"),
                    )
                return
            worker = HistoryJobWorker(service)
            await worker.start()
            logger.info("History ingestion worker started (owner=%s)", _owner())
            try:
                await asyncio.Event().wait()
            finally:
                await worker.stop()
        finally:
            await store.release_worker_lock(HISTORY_WORKER_LOCK_KEY)
    finally:
        await store.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS history archive ingestion worker")
    parser.add_argument("--once", action="store_true", help="run at most one job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
