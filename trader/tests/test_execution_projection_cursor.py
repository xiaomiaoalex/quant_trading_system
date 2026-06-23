from __future__ import annotations

import pytest

from trader.adapters.persistence.execution_repository import ExecutionRepository
from trader.storage.in_memory import InMemoryStorage


@pytest.mark.asyncio
async def test_projection_cursor_reads_oldest_executions_before_advancing() -> None:
    storage = InMemoryStorage()
    repository = ExecutionRepository(storage)

    for ts_ms, execution_id in (
        (5000, "exec-5"),
        (4000, "exec-4"),
        (3000, "exec-3"),
        (2000, "exec-2"),
        (1000, "exec-1"),
    ):
        storage.create_execution(
            {
                "execution_id": execution_id,
                "cl_ord_id": f"order-{execution_id}",
                "exec_id": execution_id,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "quantity": "1",
                "price": "100",
                "ts_ms": ts_ms,
                "strategy_id": "strategy-a",
                "venue": "BINANCE",
            }
        )

    first_batch = await repository.list_executions_for_projection(limit=2)
    second_batch = await repository.list_executions_for_projection(
        after_ts_ms=2000,
        after_execution_id="exec-2",
        limit=2,
    )

    assert [item["execution_id"] for item in first_batch] == ["exec-1", "exec-2"]
    assert [item["execution_id"] for item in second_batch] == ["exec-3", "exec-4"]
