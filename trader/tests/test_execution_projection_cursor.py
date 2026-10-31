"""Execution projection cursor 顺序契约 + 测试隔离回归。

隔离要点：本文件的所有用例都使用 ``allow_postgres=False`` 的内存仓库。
即便开发机上本地 PostgreSQL 可用，本文件也不会读到真实库里的遗留数据。

回归背景：``ExecutionRepository`` 默认会自动探测 PostgreSQL。本地 PG 可用时，
它会读真实 ``trading.executions``，导致断言拿到历史遗留 UUID 行，
而不是本测试写入的 ``exec-1`` / ``exec-2``。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from trader.adapters.persistence.execution_repository import ExecutionRepository
from trader.storage.in_memory import InMemoryStorage


def _execution_row(ts_ms: int, execution_id: str) -> Dict[str, Any]:
    return {
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


def _seed_executions(storage: InMemoryStorage) -> None:
    for ts_ms, execution_id in (
        (5000, "exec-5"),
        (4000, "exec-4"),
        (3000, "exec-3"),
        (2000, "exec-2"),
        (1000, "exec-1"),
    ):
        storage.create_execution(_execution_row(ts_ms, execution_id))


@pytest.mark.asyncio
async def test_projection_cursor_reads_oldest_executions_before_advancing() -> None:
    storage = InMemoryStorage()
    repository = ExecutionRepository(storage, allow_postgres=False)
    _seed_executions(storage)

    first_batch = await repository.list_executions_for_projection(limit=2)
    second_batch = await repository.list_executions_for_projection(
        after_ts_ms=2000,
        after_execution_id="exec-2",
        limit=2,
    )

    assert [item["execution_id"] for item in first_batch] == ["exec-1", "exec-2"]
    assert [item["execution_id"] for item in second_batch] == ["exec-3", "exec-4"]


@pytest.mark.asyncio
async def test_projection_cursor_never_touches_postgres_when_pinned_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    隔离回归：即使本地 PostgreSQL 可用，内存仓库也不会碰它。

    断言方式是「把 PG 探测结果伪造成可用，并把 PostgreSQLStorage 替换成
    一旦被构造就失败的桩」：只要代码路径真的去连库，测试就会立刻炸掉。
    """

    async def _fake_available(timeout: float = 2.0) -> tuple[bool, str]:
        return True, "connection successful"

    def _fail_if_constructed(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("memory-only ExecutionRepository must not construct PostgreSQLStorage")

    monkeypatch.setattr(
        "trader.adapters.persistence.execution_repository.check_postgres_connection",
        _fake_available,
    )
    monkeypatch.setattr(
        "trader.adapters.persistence.execution_repository.PostgreSQLStorage",
        _fail_if_constructed,
    )

    storage = InMemoryStorage()
    repository = ExecutionRepository(storage, allow_postgres=False)
    _seed_executions(storage)

    rows: List[Dict[str, Any]] = await repository.list_executions_for_projection(limit=10)

    assert repository.postgres_active is False
    assert repository.allow_postgres is False
    assert [item["execution_id"] for item in rows] == [
        "exec-1",
        "exec-2",
        "exec-3",
        "exec-4",
        "exec-5",
    ]
