"""WS/REST same-fill concurrency through the real OMS callback interface.

The OMS fill path is the production entry point for both the private
WebSocket stream and REST reconciliation.  The two sources may deliver the
same exchange execution at the same time.  The idempotency key is
``cl_ord_id + exec_id``; this module exercises the real ``create_oms_callback``
handler (not the storage shortcut) and observes the persisted execution, the
strategy fill callback and the order projection.

These tests are part of the P0 group (C1): a regression here would either
double-book a fill or silently drop a distinct one.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trader.core.domain.models.order import OrderStatus
from trader.services.oms_callback import create_oms_callback
from trader.storage.in_memory import ControlPlaneInMemoryStorage


class _DummyBroker:
    broker_name = "dummy_broker"


def _update(*, exec_id: str, qty: str = "0.4", price: str = "50000") -> SimpleNamespace:
    return SimpleNamespace(
        cl_ord_id="cicdtest_abcdef1234567890",
        side="BUY",
        qty=Decimal(qty),
        price=Decimal(price),
        trade_id=exec_id,
        exec_id=exec_id,
        symbol="BTCUSDT",
        commission=Decimal("0"),
    )


def _seed_order(storage: ControlPlaneInMemoryStorage, qty: str = "1") -> None:
    storage.create_order(
        {
            "cl_ord_id": "cicdtest_abcdef1234567890",
            "strategy_id": "cicdtest",
            "instrument": "BTCUSDT",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": qty,
            "status": OrderStatus.SUBMITTED.value,
        }
    )


@pytest.mark.asyncio
async def test_ws_and_rest_same_execution_are_booked_once_under_concurrency():
    storage = ControlPlaneInMemoryStorage()
    _seed_order(storage)
    fills: list[tuple[str, str, str, Decimal, Decimal]] = []

    async def fill_callback(strategy_id, order_id, symbol, side, qty, price):
        fills.append((strategy_id, order_id, symbol, qty, price))

    _, fill_handler, handler = create_oms_callback(
        broker=_DummyBroker(),
        live_trading_enabled=True,
        fill_callback=fill_callback,
    )
    handler._storage = storage

    ws_update = _update(exec_id="exec-1")
    rest_update = _update(exec_id="exec-1")

    await asyncio.gather(fill_handler(ws_update), fill_handler(rest_update))
    for _ in range(4):
        await asyncio.sleep(0)

    executions = storage.list_executions(cl_ord_id="cicdtest_abcdef1234567890")
    assert len(executions) == 1
    assert executions[0]["exec_id"] == "exec-1"
    assert Decimal(str(executions[0]["fill_qty"])) == Decimal("0.4")
    assert len(fills) == 1
    assert fills[0][0] == "cicdtest"

    order = storage.get_order("cicdtest_abcdef1234567890")
    assert order is not None
    assert Decimal(str(order["filled_qty"])) == Decimal("0.4")
    assert order["status"] == OrderStatus.PARTIALLY_FILLED.value
    assert handler.get_dedup_stats()["exec_dedup_hits"] >= 1


@pytest.mark.asyncio
async def test_concurrent_distinct_executions_are_both_booked_without_lost_update():
    storage = ControlPlaneInMemoryStorage()
    _seed_order(storage)
    fills: list[Decimal] = []

    async def fill_callback(strategy_id, order_id, symbol, side, qty, price):
        fills.append(qty)

    _, fill_handler, handler = create_oms_callback(
        broker=_DummyBroker(),
        live_trading_enabled=True,
        fill_callback=fill_callback,
    )
    handler._storage = storage

    first = _update(exec_id="exec-1", qty="0.4", price="50000")
    second = _update(exec_id="exec-2", qty="0.6", price="51000")

    await asyncio.gather(fill_handler(first), fill_handler(second))
    for _ in range(4):
        await asyncio.sleep(0)

    executions = storage.list_executions(cl_ord_id="cicdtest_abcdef1234567890")
    assert [execution["exec_id"] for execution in executions] == ["exec-1", "exec-2"]
    assert len(fills) == 2

    order = storage.get_order("cicdtest_abcdef1234567890")
    assert order is not None
    assert Decimal(str(order["filled_qty"])) == Decimal("1.0")
    assert order["status"] == OrderStatus.FILLED.value
    assert Decimal(str(order["avg_price"])) == Decimal("50600")
