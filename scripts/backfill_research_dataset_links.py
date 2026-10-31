#!/usr/bin/env python3
"""Backfill Research dataset links for already published history datasets.

Historical datasets that were imported before the Research bridge existed
carry no ``research_dataset_id``. This command registers the matching Research
dataset and its provenance link **without touching the existing dataset id or
any published report**.

Rules:

- Default mode is a dry run: nothing is written.
- The local archive ZIP is preferred and is re-verified against the registered
  SHA-256 and the UTC month coverage.
- If the local archive is missing, the official archive is downloaded again and
  **must** hash to the registered SHA-256. An official revision (different
  hash) is reported as a conflict and never published.
- ``--apply`` performs the writes; the publish itself is idempotent.

Usage::

    python scripts/backfill_research_dataset_links.py                # dry run
    python scripts/backfill_research_dataset_links.py --apply        # write
    python scripts/backfill_research_dataset_links.py --symbol BTCUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

for _env_file in (_ROOT / ".env", _ROOT / ".env.postgres"):
    if _env_file.exists():
        load_dotenv(_env_file, override=False)

from trader.adapters.market_data.binance_vision import (  # noqa: E402
    AiohttpArchiveHttpClient,
    ArchiveError,
    BinanceVisionArchiveClient,
    build_archive_ref,
    read_klines_from_archive,
    sha256_file,
)
from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore  # noqa: E402
from trader.adapters.persistence.research_history_bridge_store import (  # noqa: E402
    ResearchHistoryBridgeStore,
)
from trader.core.domain.services.ohlcv_archive import (  # noqa: E402
    ARCHIVE_SOURCE_NAME,
    ARCHIVE_SUPPORTED_MARKET,
    OhlcvQualityError,
    parse_month,
    validate_month_coverage,
)
from trader.trading.datasets import dataset_metadata  # noqa: E402
from trader.trading.research_bridge import (  # noqa: E402
    CONVERSION_VERSION,
    build_research_dataset,
    build_research_provenance,
)

OUTCOME_PLANNED = "PLANNED"
OUTCOME_APPLIED = "APPLIED"
OUTCOME_ALREADY_LINKED = "ALREADY_LINKED"
OUTCOME_CONFLICT = "CONFLICT"
OUTCOME_INVALID = "INVALID"


def _provenance(dataset: Dict[str, Any], coverage: Dict[str, Any]) -> Dict[str, Any]:
    return build_research_provenance(
        history_dataset_id=str(dataset["dataset_id"]),
        source_url=str(dataset["source_url"]),
        checksum_url=str(dataset["checksum_url"]),
        official_sha256=dataset.get("official_sha256"),
        archive_sha256=str(dataset["archive_sha256"]),
        archive_version=int(dataset.get("version") or 1),
        feature_version=str(dataset["feature_version"]),
        symbol=str(dataset["symbol"]),
        interval=str(dataset["interval"]),
        month=str(dataset["month"]),
        coverage_start_ms=coverage.get("month_start_ms"),
        coverage_end_ms=coverage.get("month_end_exclusive_ms"),
        bar_count=int(dataset.get("row_count") or 0),
        conversion_version=CONVERSION_VERSION,
        quality=dataset.get("quality") or {},
    )


async def _load_bars(
    dataset: Dict[str, Any],
    client: BinanceVisionArchiveClient,
) -> tuple[Optional[List[Any]], Optional[str]]:
    """Read the archive bars, preferring the local copy.

    Returns ``(bars, conflict_reason)``. A conflict means the official archive
    no longer hashes to the registered value, i.e. it was revised upstream.
    """
    symbol = str(dataset["symbol"])
    interval = str(dataset["interval"])
    month = str(dataset["month"])
    ref = build_archive_ref(symbol, interval, month)
    local_path = str(dataset.get("archive_path") or "")

    if local_path and os.path.exists(local_path):
        actual = sha256_file(local_path)
        if actual != str(dataset["archive_sha256"]):
            return None, (
                f"local archive sha256 {actual} does not match the registered "
                f"{dataset['archive_sha256']}"
            )
        return read_klines_from_archive(local_path, ref), None

    downloaded = await client.download(ref)
    try:
        if downloaded.sha256 != str(dataset["archive_sha256"]):
            return None, (
                f"official archive sha256 {downloaded.sha256} does not match the "
                f"registered {dataset['archive_sha256']} (archive was revised)"
            )
        return read_klines_from_archive(downloaded.temp_path, ref), None
    finally:
        client.discard(downloaded)


async def backfill(
    *,
    apply: bool,
    symbol: Optional[str] = None,
    limit: int = 200,
    store: Optional[HistoryDatasetStore] = None,
    bridge: Optional[ResearchHistoryBridgeStore] = None,
    client: Optional[BinanceVisionArchiveClient] = None,
) -> List[Dict[str, Any]]:
    owns_store = store is None
    owns_client = client is None
    store = store or HistoryDatasetStore()
    if owns_store:
        await store.connect()
    client = client or BinanceVisionArchiveClient(AiohttpArchiveHttpClient())
    try:
        bridge = bridge or ResearchHistoryBridgeStore(storage=store.storage)
        if owns_store:
            await bridge.ensure_research_schema()
        datasets = await store.list_datasets(
            source=ARCHIVE_SOURCE_NAME,
            market=ARCHIVE_SUPPORTED_MARKET,
            symbol=symbol,
            limit=limit,
        )
        results: List[Dict[str, Any]] = []
        for dataset in datasets:
            if str(dataset.get("status")) != "PUBLISHED":
                continue
            if dataset.get("research_dataset_id"):
                results.append(
                    {
                        "dataset_id": dataset["dataset_id"],
                        "outcome": OUTCOME_ALREADY_LINKED,
                        "research_dataset_id": dataset["research_dataset_id"],
                    }
                )
                continue
            try:
                year, month_number = parse_month(str(dataset["month"]))
                bars, conflict = await _load_bars(dataset, client)
            except (OhlcvQualityError, ArchiveError, OSError) as exc:
                results.append(
                    {
                        "dataset_id": dataset.get("dataset_id"),
                        "outcome": OUTCOME_INVALID,
                        "reason": str(exc),
                    }
                )
                continue
            if conflict is not None:
                results.append(
                    {
                        "dataset_id": dataset["dataset_id"],
                        "outcome": OUTCOME_CONFLICT,
                        "reason": conflict,
                    }
                )
                continue
            try:
                coverage = validate_month_coverage(bars, year, month_number).to_dict()
            except OhlcvQualityError as exc:
                results.append(
                    {
                        "dataset_id": dataset["dataset_id"],
                        "outcome": OUTCOME_INVALID,
                        "reason": str(exc),
                    }
                )
                continue

            document = build_research_dataset(
                bars,
                symbol=str(dataset["symbol"]),
                archive_sha256=str(dataset["archive_sha256"]),
                month=str(dataset["month"]),
            )
            link = _provenance(dataset, coverage)
            record = {
                "dataset_id": dataset["dataset_id"],
                "outcome": OUTCOME_PLANNED,
                "research_dataset_id": document["dataset_id"],
                "provenance_hash": link["provenance_hash"],
                "bar_count": document["quality"]["bar_count"],
            }
            if apply:
                await bridge.publish_dataset(
                    job_id=None,
                    history_dataset_id=str(dataset["dataset_id"]),
                    feature_version=str(dataset["feature_version"]),
                    row_count=int(dataset.get("row_count") or 0),
                    expected_points=int(dataset.get("expected_points") or 0),
                    gap_count=int(dataset.get("gap_count") or 0),
                    first_ts_ms=dataset.get("first_ts_ms"),
                    last_ts_ms=dataset.get("last_ts_ms"),
                    quality=dataset.get("quality") or {},
                    link=link,
                    research_document=document,
                    research_metadata=dataset_metadata(document),
                )
                record["outcome"] = OUTCOME_APPLIED
            results.append(record)
        return results
    finally:
        if owns_client:
            await client.close()
        if owns_store:
            await store.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the writes (default is a dry run)",
    )
    parser.add_argument("--symbol", default=None, help="only process this symbol")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    results = asyncio.run(backfill(apply=args.apply, symbol=args.symbol, limit=args.limit))
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "results": results}, indent=2))


if __name__ == "__main__":
    main()
