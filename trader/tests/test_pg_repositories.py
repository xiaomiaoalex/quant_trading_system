"""Tests for PG-first repositories."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.adapters.persistence.execution_repository import ExecutionRepository
from trader.adapters.persistence.killswitch_repository import KillSwitchRepository
from trader.adapters.persistence.position_repository import PositionRepository
from trader.adapters.persistence.postgres import is_postgres_available
from trader.adapters.persistence.runtime_state_repository import RuntimeStateRepository
from trader.api.models.schemas import KillSwitchSetRequest
from trader.services.killswitch import KillSwitchService
from trader.storage.in_memory import ControlPlaneInMemoryStorage, reset_storage


skip_if_no_postgres = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL not configured for repository integration tests",
)


@pytest.fixture(autouse=True)
def reset_memory():
    reset_storage()
    yield
    reset_storage()


async def _execute_pg_cleanup(repo: Any, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
    if not await repo._ensure_postgres():
        return
    async with repo._postgres_storage._pool.acquire() as conn:
        for sql, params in statements:
            await conn.execute(sql, *params)


@pytest.mark.asyncio
async def test_execution_repository_strict_write_raises_when_pg_down():
    repo = ExecutionRepository()
    repo._ensure_postgres = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="PostgreSQL unavailable"):
        await repo.save_execution({"cl_ord_id": "c", "exec_id": "e"})


@pytest.mark.asyncio
async def test_execution_best_effort_memory_idempotent():
    repo = ExecutionRepository()
    repo._ensure_postgres = AsyncMock(return_value=False)
    data = {"cl_ord_id": "c", "exec_id": "e", "symbol": "BTCUSDT"}
    _, created1 = await repo.save_execution_best_effort(data)
    _, created2 = await repo.save_execution_best_effort(data)
    assert created1 is True
    assert created2 is False


@pytest.mark.asyncio
async def test_killswitch_repository_strict_write_raises_when_pg_down():
    repo = KillSwitchRepository()
    repo._ensure_postgres = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="PostgreSQL unavailable"):
        await repo.save_state("GLOBAL", 1, "test", "tester", 0)


@pytest.mark.asyncio
async def test_killswitch_service_durable_propagates_failure():
    mock_repo = MagicMock()
    mock_repo.get_state.return_value = {
        "scope": "GLOBAL",
        "level": 0,
        "reason": None,
        "updated_at": "2026-01-01T00:00:00Z",
        "updated_by": "system",
    }
    mock_repo.save_state = AsyncMock(side_effect=RuntimeError("PG down"))
    service = KillSwitchService(reset_storage(), repository=mock_repo, require_durable_writes=True)
    with pytest.raises(RuntimeError, match="PG down"):
        await service.set_state_durable(KillSwitchSetRequest(
            scope="GLOBAL",
            level=1,
            reason="test",
            updated_by="tester",
        ))


@pytest.mark.asyncio
async def test_runtime_state_repository_memory_fallback_uses_deployment_id():
    repo = RuntimeStateRepository()
    repo._ensure_postgres = AsyncMock(return_value=False)
    await repo.save_state({"deployment_id": "d1", "strategy_id": "s", "status": "RUNNING"})
    assert (await repo.get_state("d1"))["strategy_id"] == "s"
    assert len(await repo.list_running_states()) == 1


@pytest.mark.asyncio
async def test_position_repository_pg_down_returns_empty_or_false():
    repo = PositionRepository()
    repo._ensure_postgres = AsyncMock(return_value=False)
    assert await repo.save_position_projection("s:BTC", {"strategy_id": "s"}) is False
    assert await repo.get_position_projection("s:BTC") is None
    assert await repo.list_lots("s", "BTCUSDT") == []


@skip_if_no_postgres
@pytest.mark.asyncio
async def test_execution_repository_persists_and_deduplicates():
    repo = ExecutionRepository()
    suffix = uuid.uuid4().hex
    cl_ord_id = f"pg-cl-{suffix}"
    exec_id = f"pg-exec-{suffix}"
    data = {
        "execution_id": f"pg-execution-{suffix}",
        "cl_ord_id": cl_ord_id,
        "exec_id": exec_id,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "1.25",
        "price": "50000",
        "ts_ms": 1700000000000,
        "strategy_id": "pg_strategy",
        "venue": "binance_demo",
    }
    try:
        first_id, first_created = await repo.save_execution(data)
        second_id, second_created = await repo.save_execution(data)
        stored = await repo.get_execution(cl_ord_id, exec_id)
        assert first_created is True
        assert second_created is False
        assert second_id == first_id
        assert stored["strategy_id"] == "pg_strategy"
    finally:
        await _execute_pg_cleanup(repo, [("DELETE FROM executions WHERE cl_ord_id = $1", (cl_ord_id,))])


@skip_if_no_postgres
@pytest.mark.asyncio
async def test_killswitch_repository_persists_audit_log():
    repo = KillSwitchRepository()
    scope = f"PG_TEST_{uuid.uuid4().hex}"
    try:
        state = await repo.save_state(scope, 2, "pg integration", "pytest", 0)
        changes = await repo.get_recent_changes(scope=scope, limit=5)
        assert state["level"] == 2
        assert changes[0]["scope"] == scope
    finally:
        await _execute_pg_cleanup(repo, [("DELETE FROM killswitch_log WHERE scope = $1", (scope,))])


@skip_if_no_postgres
@pytest.mark.asyncio
async def test_runtime_state_repository_recovers_from_pg():
    deployment_id = f"deploy-pg-{uuid.uuid4().hex}"
    writer = RuntimeStateRepository()
    reader = RuntimeStateRepository(ControlPlaneInMemoryStorage())
    try:
        await writer.save_state({
            "deployment_id": deployment_id,
            "strategy_id": "strategy_pg",
            "status": "RUNNING",
            "config": {"lookback": 20},
            "symbols": ["BTCUSDT"],
            "env": "test",
        })
        recovered = await reader.get_state(deployment_id)
        assert recovered["config"] == {"lookback": 20}
        assert any(item["deployment_id"] == deployment_id for item in await reader.list_running_states())
    finally:
        await writer.delete_state(deployment_id)


@skip_if_no_postgres
@pytest.mark.asyncio
async def test_position_repository_persists_lot_projection_and_reconciliation():
    repo = PositionRepository()
    suffix = uuid.uuid4().hex
    strategy_id = f"strategy_{suffix}"
    symbol = "BTCUSDT"
    position_id = f"{strategy_id}:{symbol}"
    lot_id = f"lot-{suffix}"
    try:
        assert await repo.save_lot({
            "lot_id": lot_id,
            "position_id": position_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "original_qty": "2",
            "remaining_qty": "2",
            "fill_price": "50000",
            "filled_at": datetime.now(timezone.utc),
        }) is True
        assert len(await repo.list_lots(strategy_id, symbol)) == 1
        assert await repo.update_lot_on_reduce(lot_id, Decimal("1.25"), Decimal("125")) is True
        assert await repo.save_position_projection(position_id, {"strategy_id": strategy_id, "symbol": symbol}) is True
        projection = await repo.get_position_projection(position_id)
        assert projection["state"]["strategy_id"] == strategy_id
        assert await repo.save_reconciliation(
            symbol=symbol,
            broker_qty=Decimal("1.25"),
            oms_total_qty=Decimal("1.25"),
            difference=Decimal("0"),
            tolerance=Decimal("0.001"),
            status="CONSISTENT",
            details={"strategy_id": strategy_id},
        ) is True
        recs = await repo.list_reconciliations(symbol=symbol, status="CONSISTENT", limit=20)
        assert any(item["details"].get("strategy_id") == strategy_id for item in recs)
    finally:
        await _execute_pg_cleanup(
            repo,
            [
                ("DELETE FROM position_lots WHERE lot_id = $1", (lot_id,)),
                ("DELETE FROM strategy_positions_proj WHERE aggregate_id = $1", (position_id,)),
                ("DELETE FROM reconciliation_log WHERE details->>'strategy_id' = $1", (strategy_id,)),
            ],
        )
