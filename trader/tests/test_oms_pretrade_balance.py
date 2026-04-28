from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.domain.models.order import OrderStatus
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.oms_callback import (
    InsufficientBalanceError,
    OMSCallbackError,
    OMSCallbackHandler,
)
from trader.storage.in_memory import ControlPlaneInMemoryStorage


def _broker_with_balances(balances: list[dict[str, str]]) -> MagicMock:
    broker = MagicMock()
    broker.broker_name = "binance_spot_demo"
    broker.get_symbol_step_size = AsyncMock(return_value=Decimal("0.00001"))
    broker.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"filters": [{"filterType": "NOTIONAL", "minNotional": "10"}]}]
    })
    broker.get_ticker_prices = AsyncMock(return_value={"BTCUSDT": Decimal("50000")})
    broker._fetch_account = AsyncMock()
    broker._account_cache = {"balances": balances}
    broker.place_order = AsyncMock()
    return broker


def _submitted_order() -> MagicMock:
    order = MagicMock()
    order.broker_order_id = "broker-1"
    order.filled_quantity = Decimal("0")
    order.average_price = Decimal("0")
    order.status = OrderStatus.SUBMITTED
    order.created_at = None
    return order


def _signal(
    signal_type: SignalType = SignalType.BUY,
    quantity: Decimal = Decimal("0.01"),
    price: Decimal = Decimal("50000"),
) -> Signal:
    return Signal(
        strategy_name="test_strategy",
        signal_type=signal_type,
        symbol="BTCUSDT",
        quantity=quantity,
        price=price,
    )


@pytest.mark.asyncio
async def test_market_buy_uses_reference_price_for_balance_precheck() -> None:
    broker = _broker_with_balances([
        {"asset": "USDT", "free": "100", "locked": "0"},
        {"asset": "BTC", "free": "0", "locked": "0"},
    ])
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
    )

    with pytest.raises(InsufficientBalanceError):
        await handler.execute_signal(
            "test_strategy",
            _signal(price=Decimal("0")),
        )

    broker.get_ticker_prices.assert_awaited_once_with(["BTCUSDT"])
    broker.place_order.assert_not_awaited()
    stats = handler.get_dedup_stats()
    assert stats["reject_reason_counts"]["INSUFFICIENT_BALANCE"] == 1


@pytest.mark.asyncio
async def test_account_fetch_failure_is_fail_closed() -> None:
    broker = _broker_with_balances([
        {"asset": "USDT", "free": "10000", "locked": "0"},
    ])
    broker._fetch_account = AsyncMock(side_effect=RuntimeError("account unavailable"))
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
    )

    with pytest.raises(OMSCallbackError, match="Unable to fetch account balances"):
        await handler.execute_signal("test_strategy", _signal(quantity=Decimal("0.001")))

    broker.place_order.assert_not_awaited()
    assert handler.get_dedup_stats()["order_submit_error"] == 1


@pytest.mark.asyncio
async def test_sell_insufficient_base_balance_rejected_before_place_order() -> None:
    broker = _broker_with_balances([
        {"asset": "USDT", "free": "10000", "locked": "0"},
        {"asset": "BTC", "free": "0.001", "locked": "0"},
    ])
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
    )

    with pytest.raises(InsufficientBalanceError):
        await handler.execute_signal(
            "test_strategy",
            _signal(signal_type=SignalType.SELL, quantity=Decimal("0.01")),
        )

    broker.place_order.assert_not_awaited()
    assert handler.get_dedup_stats()["reject_reason_counts"]["INSUFFICIENT_BALANCE"] == 1


@pytest.mark.asyncio
async def test_local_reservation_prevents_sequential_overcommit() -> None:
    broker = _broker_with_balances([
        {"asset": "USDT", "free": "100", "locked": "0"},
        {"asset": "BTC", "free": "0", "locked": "0"},
    ])
    broker.place_order.return_value = _submitted_order()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
    )

    first = await handler.execute_signal(
        "test_strategy",
        _signal(quantity=Decimal("0.0015"), price=Decimal("50000")),
    )
    assert first is not None

    with pytest.raises(InsufficientBalanceError):
        await handler.execute_signal(
            "test_strategy",
            _signal(quantity=Decimal("0.0015"), price=Decimal("50000")),
        )

    assert broker.place_order.await_count == 1
    stats = handler.get_dedup_stats()
    assert stats["order_submit_ok"] == 1
    assert stats["reject_reason_counts"]["INSUFFICIENT_BALANCE"] == 1
