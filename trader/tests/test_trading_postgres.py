"""Real PostgreSQL integration tests for the unified trading transaction boundary."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from trader.tests.test_trading_service import Clock, FakeExchange, bootstrap, command
from trader.trading.models import Conflict, Execution, OrderObservation, ReconcileSnapshot
from trader.trading.postgres import PostgresTradingStore
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService


def _asyncpg():
    # Import lazily: other test modules replace sys.modules["asyncpg"] with a mock.
    import importlib

    return importlib.import_module("asyncpg")


CONNECTION_STRING = os.getenv(
    "POSTGRES_CONNECTION_STRING", "postgresql://trader:trader_pwd@127.0.0.1:5432/trading"
)


async def _can_connect() -> bool:
    try:
        asyncpg = _asyncpg()
        connection = await asyncpg.connect(CONNECTION_STRING, timeout=3)
        await connection.close()
        return True
    except Exception:
        return False


@asynccontextmanager
async def pg_store():
    try:
        asyncpg = _asyncpg()
    except ImportError:
        pytest.skip("asyncpg not installed")
    schema = "qts_trading_test_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(CONNECTION_STRING)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = None
    try:
        pool = await asyncpg.create_pool(
            CONNECTION_STRING,
            min_size=1,
            max_size=6,
            server_settings={"search_path": schema},
        )
        store = PostgresTradingStore(pool)
        await store.initialize()
        yield store, pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


def service(store, clock=None, exchange=None):
    return TradingService(
        store=store,
        exchange=exchange or FakeExchange(),
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        clock=clock or Clock(),
    )


async def _bootstrap(store, clock=None):
    current = service(store, clock)
    await bootstrap(current)
    return current


@pytest.mark.asyncio
async def test_postgres_transaction_rolls_back_every_table_on_failure():
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with pg_store() as (store, pool):
        current = service(store)
        await bootstrap(current)
        receipt = await current.place(command(qty=Decimal("1"), price=Decimal("100")))

        original = current._post_execution

        def explode(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        current._post_execution = explode
        with pytest.raises(RuntimeError):
            await current.record_execution(
                Execution(
                    "acct",
                    receipt.cl_ord_id,
                    "BTCUSDT",
                    "e-1",
                    Decimal("1"),
                    Decimal("100"),
                    Decimal("0.1"),
                    "USDT",
                    1_000_100,
                )
            )
        current._post_execution = original

        state = await store.read("acct")
        assert state.executions == {}
        # Only the opening balance survives; the failed execution wrote nothing.
        assert [entry["kind"] for entry in state.ledger] == ["opening_balance"]
        assert state.balances["USDT"] == Decimal("1000")
        assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("0")
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM trading_executions") == 0
            assert await conn.fetchval("SELECT count(*) FROM trading_ledger") == 1

        # The same write succeeds after the transient failure.
        assert await current.record_execution(
            Execution(
                "acct",
                receipt.cl_ord_id,
                "BTCUSDT",
                "e-1",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
                1_000_100,
            )
        )
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM trading_executions") == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_orders_serialize_on_the_account_row():
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with pg_store() as (store, _pool):
        clock = Clock()
        await _bootstrap(store, clock)
        one = service(store, clock)
        two = service(store, clock)

        async def attempt(target, command_id):
            try:
                return await target.place(
                    command(command_id=command_id, qty=Decimal("6"), price=Decimal("100"))
                )
            except Exception as exc:
                return exc

        results = await asyncio.gather(attempt(one, "a"), attempt(two, "b"))
        assert len([value for value in results if not isinstance(value, Exception)]) == 1
        state = await store.read("acct")
        assert len(state.orders) == 1
        assert len(state.holds) == 1


@pytest.mark.asyncio
async def test_postgres_execution_dedup_and_restart_replay():
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with pg_store() as (store, _pool):
        first = service(store)
        await bootstrap(first)
        receipt = await first.place(command(qty=Decimal("2"), price=Decimal("100")))
        execution = Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_100,
        )
        assert await first.record_execution(execution) is True

        restarted = service(store)
        results = await asyncio.gather(
            restarted.record_execution(execution),
            restarted.record_execution(execution),
        )
        assert results == [False, False]
        state = await store.read("acct")
        assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("1")
        assert state.balances["USDT"] == Decimal("899.9")
        kinds = [entry["kind"] for entry in state.ledger]
        # 1 opening balance + quote/base legs + the fee leg of a single fill.
        assert kinds.count("opening_balance") == 1
        assert len([kind for kind in kinds if kind in {"trade", "fee"}]) == 3

        conflicting = Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-1",
            Decimal("1"),
            Decimal("101"),
            Decimal("0.1"),
            "USDT",
            1_000_100,
        )
        with pytest.raises(Conflict):
            await restarted.record_execution(conflicting)
        assert "EXECUTION_KEY_CONFLICT" in (await restarted.risk_view("acct")).reasons


@pytest.mark.asyncio
async def test_postgres_outbox_and_lease_fencing():
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with pg_store() as (store, _pool):
        current = service(store)
        await bootstrap(current)
        await current.place(command())
        pending = await store.pending_outbox("acct")
        assert [item["data"]["event_type"] for item in pending] == [
            "order.queued",
            "order.accepted",
        ]
        await store.mark_outbox_delivered("acct", [pending[0]["event_id"]])
        remaining = await store.pending_outbox("acct")
        assert [item["event_id"] for item in remaining] == [pending[1]["event_id"]]

        token = await store.acquire_lease("trading:acct", "runtime-1", 30)
        assert token is not None
        assert await store.acquire_lease("trading:acct", "runtime-2", 30) is None
        assert await store.renew_lease("trading:acct", "runtime-1", token, 30) is True
        await store.release_lease("trading:acct", "runtime-1", token)
        assert await store.acquire_lease("trading:acct", "runtime-2", 30) is not None


@pytest.mark.asyncio
async def test_postgres_recovery_from_persisted_state():
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with pg_store() as (store, _pool):
        clock = Clock(1_000_100)
        first = service(store, clock)
        await bootstrap(first)
        receipt = await first.place(command(qty=Decimal("2"), price=Decimal("100")))
        execution = Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_100,
        )
        await first.record_execution(execution)

        restarted = service(store, clock)
        view = await restarted.recover(
            ReconcileSnapshot(
                "acct",
                1_000_100,
                2,
                {"USDT": Decimal("899.9"), "BTC": Decimal("1")},
                open_orders=[
                    OrderObservation(
                        "acct",
                        receipt.cl_ord_id,
                        "PARTIALLY_FILLED",
                        Decimal("1"),
                        "b-1",
                        1_000_100,
                    )
                ],
                executions=[execution],
                marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_100}},
            )
        )
        assert view.ready
        state = await store.read("acct")
        assert state.holds[receipt.cl_ord_id].amount == Decimal("100.100")
        assert state.positions["BTCUSDT"] == Decimal("1")
