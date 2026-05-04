from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel
from trader.core.domain.models.order import OrderStatus
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.oms_callback import OMSCallbackHandler, RiskRejectedError
from trader.storage.in_memory import ControlPlaneInMemoryStorage


def _broker() -> MagicMock:
    broker = MagicMock()
    broker.broker_name = "binance_spot_demo"
    broker.get_symbol_step_size = AsyncMock(return_value=Decimal("0.00001"))
    broker.get_exchange_info = AsyncMock(
        return_value={"symbols": [{"filters": [{"filterType": "NOTIONAL", "minNotional": "10"}]}]}
    )
    broker.get_ticker_prices = AsyncMock(return_value={"BTCUSDT": Decimal("50000")})
    broker._fetch_account = AsyncMock()
    broker._account_cache = {
        "balances": [
            {"asset": "USDT", "free": "10000", "locked": "0"},
            {"asset": "BTC", "free": "1", "locked": "0"},
        ]
    }
    broker.place_order = AsyncMock(
        return_value=MagicMock(
            broker_order_id="broker-1",
            filled_quantity=Decimal("0"),
            average_price=Decimal("0"),
            status=OrderStatus.SUBMITTED,
            created_at=None,
        )
    )
    return broker


def _signal() -> Signal:
    return Signal(
        signal_type=SignalType.LONG,
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
    )


@pytest.mark.asyncio
async def test_oms_pretrade_risk_rejection_blocks_broker_place_order() -> None:
    risk_check = AsyncMock(
        return_value=RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.CRYPTO_OPEN_ORDER_EXPOSURE,
            message="symbol risk cap breached",
        )
    )
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
        pre_trade_risk_check=risk_check,
    )

    with pytest.raises(RiskRejectedError, match="symbol risk cap breached"):
        await handler.execute_signal("test_strategy", _signal())

    risk_check.assert_awaited_once()
    broker.place_order.assert_not_awaited()
    stats = handler.get_dedup_stats()
    assert stats["reject_reason_counts"]["CRYPTO_OPEN_ORDER_EXPOSURE"] == 1


@pytest.mark.asyncio
async def test_oms_pretrade_risk_check_exception_fails_closed() -> None:
    risk_check = AsyncMock(side_effect=RuntimeError("risk backend down"))
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
        pre_trade_risk_check=risk_check,
    )

    with pytest.raises(RiskRejectedError, match="risk backend down"):
        await handler.execute_signal("test_strategy", _signal())

    broker.place_order.assert_not_awaited()
    stats = handler.get_dedup_stats()
    assert stats["reject_reason_counts"]["RISK_SYSTEM_ERROR"] == 1


@pytest.mark.asyncio
async def test_oms_pretrade_risk_pass_allows_existing_order_flow() -> None:
    risk_check = AsyncMock(return_value=RiskCheckResult(passed=True))
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
        pre_trade_risk_check=risk_check,
    )

    result = await handler.execute_signal("test_strategy", _signal())

    assert result is not None
    risk_check.assert_awaited_once()
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_oms_pretrade_risk_check_can_be_late_bound_after_handler_creation() -> None:
    risk_check = AsyncMock(
        return_value=RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.CRYPTO_MARGIN_LIMIT,
            message="late-bound risk rejected",
        )
    )
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
    )

    handler.set_pre_trade_risk_check(risk_check)

    with pytest.raises(RiskRejectedError, match="late-bound risk rejected"):
        await handler.execute_signal("test_strategy", _signal())

    risk_check.assert_awaited_once()
    broker.place_order.assert_not_awaited()
