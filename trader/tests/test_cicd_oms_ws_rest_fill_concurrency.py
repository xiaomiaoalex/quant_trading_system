"""WS/REST same-fill concurrency through the unified trading service.

Both the private WebSocket stream and REST reconciliation deliver executions to
``TradingService.record_execution``.  The idempotency key is
``account_id + cl_ord_id + exec_id``; this module exercises the real service
(not a storage shortcut) under concurrent delivery and observes the persisted
execution, order aggregate and append-only ledger.

These tests are part of the P0 group (C1): a regression here would either
double-book a fill or silently drop a distinct one.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from trader.tests.test_trading_service import FakeExchange, bootstrap, command, make_service
from trader.trading.models import Execution
from trader.trading.store import MemoryTradingStore

ACCOUNT_ID = "acct"


async def _service_with_open_order(qty: str = "1"):
    store = MemoryTradingStore()
    engine = make_service(FakeExchange(), store=store)
    await bootstrap(engine)
    receipt = await engine.place(command(qty=Decimal(qty), price=Decimal("100")))
    return engine, store, receipt.cl_ord_id


def _execution(cl_ord_id: str, exec_id: str, qty: str = "0.4", price: str = "100") -> Execution:
    return Execution(
        ACCOUNT_ID,
        cl_ord_id,
        "BTCUSDT",
        exec_id,
        Decimal(qty),
        Decimal(price),
        Decimal("0.1"),
        "USDT",
        1_000_100,
    )


def _booked_legs(state) -> list[dict]:
    return [entry for entry in state.ledger if entry["kind"] in {"trade", "fee"}]


@pytest.mark.asyncio
async def test_ws_and_rest_same_execution_are_booked_once_under_concurrency():
    engine, store, cl_ord_id = await _service_with_open_order()
    fill = _execution(cl_ord_id, "exec-1")

    first, second = await asyncio.gather(
        engine.record_execution(fill),
        engine.record_execution(fill),
    )

    assert sorted([first, second]) == [False, True]
    state = await store.read(ACCOUNT_ID)
    assert list(state.executions) == [fill.key]
    assert state.orders[cl_ord_id].booked_qty == Decimal("0.4")
    assert state.orders[cl_ord_id].status == "PARTIALLY_FILLED"
    assert len(_booked_legs(state)) == 3
    assert "EXECUTION_KEY_CONFLICT" not in [
        entry["code"] for entry in state.reconciliation["unresolved"]
    ]


@pytest.mark.asyncio
async def test_concurrent_distinct_executions_are_both_booked_without_lost_update():
    engine, store, cl_ord_id = await _service_with_open_order()
    first = _execution(cl_ord_id, "exec-1", qty="0.4", price="100")
    second = _execution(cl_ord_id, "exec-2", qty="0.6", price="101")

    results = await asyncio.gather(
        engine.record_execution(first),
        engine.record_execution(second),
    )

    assert results == [True, True]
    state = await store.read(ACCOUNT_ID)
    assert set(state.executions) == {first.key, second.key}
    assert state.orders[cl_ord_id].booked_qty == Decimal("1.0")
    assert state.orders[cl_ord_id].status == "FILLED"
    assert len(_booked_legs(state)) == 6
    assert "EXECUTION_KEY_CONFLICT" not in [
        entry["code"] for entry in state.reconciliation["unresolved"]
    ]
