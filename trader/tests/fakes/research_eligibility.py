"""Test helpers that model a trusted published dataset provenance link.

The production gate is mandatory: a backtest cannot be submitted without a
trusted published provenance link. Tests that exercise unrelated research
behaviour attach this explicit link instead of disabling the gate.
"""

from __future__ import annotations

import uuid


async def always_eligible(dataset_id: str) -> bool:
    """Eligibility callback for tests whose subject is not the provenance gate."""
    return True


def trust_memory_dataset(
    store,
    dataset_id: str,
    *,
    provenance_hash: str | None = None,
    history_dataset_id: str | None = None,
    official_eligible: bool = True,
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    month: str = "2026-09",
) -> dict:
    """Attach a trusted link to an in-memory Research dataset."""
    link = {
        "research_dataset_id": dataset_id,
        "history_dataset_id": history_dataset_id or "hds_" + uuid.uuid4().hex[:12],
        "official_eligible": official_eligible,
        "provenance_hash": provenance_hash or ("a" * 64),
        "symbol": symbol,
        "interval": interval,
        "month": month,
        "conversion_version": "test_v1",
        "bar_count": 4,
    }
    store.register_dataset_link(dataset_id, link)
    return link


async def trust_postgres_dataset(pool, document: dict, **overrides) -> dict:
    """Insert a trusted link row for a dataset already registered in PostgreSQL."""
    dataset_id = document["dataset_id"]
    identity = document["identity"]
    link = {
        "history_dataset_id": "hds_" + uuid.uuid4().hex[:12],
        "symbol": identity["symbol"],
        "interval": identity["interval"],
        "month": "2026-09",
        "conversion_version": "test_v1",
        "provenance_hash": "b" * 64,
        "bar_count": len(document.get("bars") or []),
    }
    link.update(overrides)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO research.dataset_links (
                research_dataset_id, history_dataset_id, symbol, interval, month,
                conversion_version, provenance_hash, official_eligible, bar_count
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (research_dataset_id) DO UPDATE SET
                official_eligible = EXCLUDED.official_eligible,
                provenance_hash = EXCLUDED.provenance_hash
            """,
            dataset_id,
            link["history_dataset_id"],
            link["symbol"],
            link["interval"],
            link["month"],
            link["conversion_version"],
            link["provenance_hash"],
            True,
            link["bar_count"],
        )
    return link
