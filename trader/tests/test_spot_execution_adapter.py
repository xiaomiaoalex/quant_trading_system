"""Adapter contract tests for Binance spot exchange and snapshot mapping."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
import pytest

from trader.adapters.broker.spot_execution_adapter import (
    BinanceSpotExchangePort,
    BinanceSpotSnapshotProvider,
)
from trader.core.application.ports import BrokerBusinessError, BrokerNetworkError, BrokerOrder
from trader.core.domain.models.order import OrderSide, OrderStatus, OrderType
from trader.trading.models import ExchangeOutcome, PlaceOrderCommand


def broker_order(
    order_id="b-1",
    cl_ord_id="qts-1",
    status=OrderStatus.SUBMITTED,
    filled="0",
    symbol="BTCUSDT",
):
    return BrokerOrder(
        broker_order_id=order_id,
        cl_ord_id=cl_ord_id,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("1"),
        filled_qty=Decimal(filled),
        average_price=Decimal("100"),
        status=status,
        created_at=datetime.now(timezone.utc),
    )


class FakeBroker:
    def __init__(self):
        self.place_error = None
        self.cancel_error = None
        self.query_result = broker_order()
        self.account = {
            "balances": [
                {"asset": "USDT", "free": "900", "locked": "100"},
                {"asset": "BTC", "free": "0", "locked": "0"},
            ]
        }
        self.orders = [broker_order()]
        self.trades = {
            "BTCUSDT": [
                {
                    "id": 7,
                    "orderId": "b-1",
                    "price": "100",
                    "qty": "0.5",
                    "commission": "0.05",
                    "commissionAsset": "USDT",
                    "time": 1_000_100,
                }
            ]
        }
        self.place_calls = []
        self.cancel_calls = []

    async def _fetch_account(self):
        return self.account

    async def place_order(self, **kwargs):
        self.place_calls.append(kwargs)
        if self.place_error is not None:
            raise self.place_error
        return broker_order(order_id="b-9", cl_ord_id=kwargs["cl_ord_id"])

    async def cancel_order(self, **kwargs):
        self.cancel_calls.append(kwargs)
        if self.cancel_error is not None:
            raise self.cancel_error
        return True

    async def get_order(self, **kwargs):
        return self.query_result

    async def get_open_orders(self, symbol=None):
        return self.orders

    async def get_all_orders(self, symbol, start_time_ms=None, limit=1000):
        return self.orders

    async def get_my_trades(self, symbol, start_time_ms=None, from_id=None, limit=1000):
        return self.trades.get(symbol, [])

    async def get_ticker_prices(self, symbols):
        return {symbol: Decimal("100") for symbol in symbols}


def command():
    return PlaceOrderCommand(
        command_id="cmd-1",
        account_id="acct",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        side="BUY",
        qty=Decimal("1"),
        price=Decimal("100"),
    )


@pytest.mark.asyncio
async def test_place_outcomes_are_translated_to_port_results():
    broker = FakeBroker()
    port = BinanceSpotExchangePort(broker)
    accepted = await port.place_order(command(), "qts-1")
    assert accepted.outcome == ExchangeOutcome.ACCEPTED
    assert accepted.broker_order_id == "b-9"
    assert broker.place_calls[0]["cl_ord_id"] == "qts-1"

    broker.place_error = BrokerBusinessError("insufficient balance")
    assert (await port.place_order(command(), "qts-2")).outcome == ExchangeOutcome.REJECTED

    broker.place_error = BrokerNetworkError("timeout")
    assert (await port.place_order(command(), "qts-3")).outcome == ExchangeOutcome.UNKNOWN

    broker.place_error = aiohttp.ClientError("connection reset")
    assert (await port.place_order(command(), "qts-4")).outcome == ExchangeOutcome.UNKNOWN

    broker.place_error = RuntimeError("unexpected")
    assert (await port.place_order(command(), "qts-5")).outcome == ExchangeOutcome.UNAVAILABLE


@pytest.mark.asyncio
async def test_cancel_and_query_outcomes():
    broker = FakeBroker()
    port = BinanceSpotExchangePort(broker)
    assert (
        await port.cancel_order("acct", "qts-1", "c-1", "BTCUSDT")
    ).outcome == ExchangeOutcome.ACCEPTED
    assert broker.cancel_calls[0]["symbol"] == "BTCUSDT"

    broker.cancel_error = BrokerBusinessError("Unknown order sent")
    assert (
        await port.cancel_order("acct", "qts-1", "c-2", "BTCUSDT")
    ).outcome == ExchangeOutcome.NOT_FOUND

    broker.cancel_error = BrokerNetworkError("timeout")
    assert (
        await port.cancel_order("acct", "qts-1", "c-3", "BTCUSDT")
    ).outcome == ExchangeOutcome.UNKNOWN

    assert (await port.query_order("acct", "qts-1", "BTCUSDT")).outcome == ExchangeOutcome.ACCEPTED
    broker.query_result = None
    assert (await port.query_order("acct", "qts-1", "BTCUSDT")).outcome == ExchangeOutcome.NOT_FOUND


@pytest.mark.asyncio
async def test_snapshot_uses_total_balance_and_reports_trades():
    broker = FakeBroker()
    provider = BinanceSpotSnapshotProvider(
        broker, account_id="acct", symbols=["BTCUSDT"], clock=lambda: 1_000_100
    )
    snapshot = await provider.build()
    assert snapshot.complete is True
    assert snapshot.balances["USDT"] == Decimal("1000")
    assert snapshot.external_locked == {}
    assert snapshot.open_orders[0].cl_ord_id == "qts-1"
    assert snapshot.open_orders[0].status == "OPEN"
    assert snapshot.marks["BTCUSDT"]["price"] == Decimal("100")
    assert len(snapshot.executions) == 1
    execution = snapshot.executions[0]
    assert execution.exec_id == "7"
    assert execution.cl_ord_id == "qts-1"
    assert execution.qty == Decimal("0.5")
    assert execution.fee == Decimal("0.05")


@pytest.mark.asyncio
async def test_snapshot_is_incomplete_when_pagination_or_order_mapping_is_missing():
    broker = FakeBroker()
    truncated = BinanceSpotSnapshotProvider(
        broker, account_id="acct", symbols=["BTCUSDT"], clock=lambda: 1_000_100, page_limit=1
    )
    assert (await truncated.build()).complete is False

    broker.orders = [broker_order(order_id="other")]
    broker.trades = {"BTCUSDT": [{"id": 1, "orderId": 999, "qty": "1", "price": "1"}]}
    unmapped = BinanceSpotSnapshotProvider(
        broker, account_id="acct", symbols=["BTCUSDT"], clock=lambda: 1_000_100
    )
    assert (await unmapped.build()).complete is False
