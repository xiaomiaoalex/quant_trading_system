"""
OMS Fill Idempotency Tests
==========================
"""
import asyncio
from types import SimpleNamespace

import pytest

from trader.services.oms_callback import create_oms_callback
from trader.storage.in_memory import get_storage, reset_storage


class _DummyBroker:
    broker_name = "dummy_broker"


@pytest.mark.asyncio
async def test_fill_handler_deduplicates_by_cl_ord_id_and_exec_id():
    reset_storage()
    fills = []

    async def fill_callback(strategy_id, order_id, symbol, side, qty, price):
        fills.append(
            {
                "strategy_id": strategy_id,
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
            }
        )

    _, fill_handler, _ = create_oms_callback(
        broker=_DummyBroker(),
        live_trading_enabled=True,
        fill_callback=fill_callback,
    )

    update = SimpleNamespace(
        cl_ord_id="fire_test_abcdef1234567890",
        side="BUY",
        qty=0.001,
        price=65000.0,
        trade_id=12345,
        exec_id="exec_12345",
        symbol="BTCUSDT",
        commission=0.0,
    )

    await fill_handler(update)
    await asyncio.sleep(0)  # Allow scheduled tasks to complete
    await fill_handler(update)  # duplicate
    await asyncio.sleep(0)  # Allow scheduled tasks to complete

    storage = get_storage()
    executions = storage.list_executions(cl_ord_id="fire_test_abcdef1234567890")
    assert len(executions) == 1
    assert executions[0]["exec_id"] == "exec_12345"
    assert len(fills) == 1
    assert fills[0]["strategy_id"] == "fire_test"


@pytest.mark.asyncio
async def test_fill_handler_uses_trade_id_when_exec_id_missing():
    reset_storage()
    fills = []

    async def fill_callback(strategy_id, order_id, symbol, side, qty, price):
        fills.append((strategy_id, order_id, symbol, side, qty, price))

    _, fill_handler, _ = create_oms_callback(
        broker=_DummyBroker(),
        live_trading_enabled=True,
        fill_callback=fill_callback,
    )

    update = SimpleNamespace(
        cl_ord_id="mybot_alpha_abcdef1234567890",
        side="SELL",
        qty=0.002,
        price=64000.0,
        trade_id=777,
        exec_id=None,
        symbol="BTCUSDT",
        commission=0.0,
    )

    await fill_handler(update)
    await asyncio.sleep(0)  # Allow scheduled tasks to complete

    storage = get_storage()
    executions = storage.list_executions(cl_ord_id="mybot_alpha_abcdef1234567890")
    assert len(executions) == 1
    assert executions[0]["exec_id"] == "777"
    assert len(fills) == 1
    assert fills[0][0] == "mybot_alpha"
