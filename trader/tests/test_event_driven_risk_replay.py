"""
test_event_driven_risk_replay.py - P9.4 EventDrivenRiskReplay v1 单元测试
========================================================================
测试 EventDrivenRiskReplay 的各项功能：
1. APPROVED 订单进入模拟执行
2. REJECTED 订单记录但不执行
3. CLIPPED 订单使用 effective_quantity
4. 风控异常 fail-closed 生成 REJECTED 结果
5. 输出包含 equity_curve、max_drawdown、risk_decisions、final_positions、errors

参考: docs/INTERFACE_CONTRACTS.md 8.11.6 EventDrivenRiskReplay v1 契约
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trader.core.domain.models.market_rules import OrderSide
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.backtesting.backtest_risk_integration import (
    BacktestSignalResult,
    BacktestSignalStatus,
)
from trader.services.backtesting.event_driven_risk_replay import (
    EventDrivenRiskReplay,
    EventDrivenRiskReplayResult,
    OrderDecision,
)


def _make_signal(symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        signal_id="test-signal-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol=symbol,
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=None,
    )


def _approved_result(signal: Signal) -> BacktestSignalResult:
    return BacktestSignalResult(
        signal=signal,
        status=BacktestSignalStatus.APPROVED,
        effective_quantity=signal.quantity,
    )


def _rejected_result(signal: Signal, reason: str) -> BacktestSignalResult:
    return BacktestSignalResult(
        signal=signal,
        status=BacktestSignalStatus.REJECTED,
        rejection_reason=reason,
        effective_quantity=None,
    )


def _clipped_result(signal: Signal) -> BacktestSignalResult:
    return BacktestSignalResult(
        signal=signal,
        status=BacktestSignalStatus.CLIPPED,
        effective_quantity=signal.quantity / Decimal("2"),
    )


class TestReplayApprovedOrders:
    @pytest.mark.asyncio
    async def test_approved_order_creates_fill(self):
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(return_value=_approved_result(_make_signal()))

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[_make_signal()])

        assert len(result.raw_signals) == 1
        assert len(result.approved_orders) == 1
        assert len(result.fills) == 1
        assert len(result.clipped_orders) == 0
        assert len(result.rejected_orders) == 0

    @pytest.mark.asyncio
    async def test_approved_order_updates_positions(self):
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(return_value=_approved_result(_make_signal()))

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[_make_signal()])

        assert "BTCUSDT" in result.final_positions
        assert result.final_positions["BTCUSDT"] == Decimal("0.1")

    @pytest.mark.asyncio
    async def test_approved_order_records_equity_curve(self):
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(return_value=_approved_result(_make_signal()))

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[_make_signal()])

        assert len(result.equity_curve) == 1
        assert result.max_drawdown >= Decimal("0")
        assert len(result.risk_decisions) == 1
        assert result.risk_decisions[0].decision == OrderDecision.APPROVED


class TestReplayRejectedOrders:
    @pytest.mark.asyncio
    async def test_rejected_order_no_fill(self):
        signal = _make_signal()
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(
            return_value=_rejected_result(signal, "DUPLICATE_SIGNAL")
        )

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal])

        assert len(result.raw_signals) == 1
        assert len(result.rejected_orders) == 1
        assert len(result.approved_orders) == 0
        assert len(result.clipped_orders) == 0
        assert len(result.fills) == 0

    @pytest.mark.asyncio
    async def test_rejected_order_counts_rejection_reason(self):
        signal1 = _make_signal("BTCUSDT")
        signal2 = _make_signal("ETHUSDT")

        async def mock_evaluate(sig):
            if sig.symbol == "BTCUSDT":
                return _rejected_result(sig, "DUPLICATE_SIGNAL")
            return _approved_result(sig)

        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(side_effect=mock_evaluate)

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal1, signal2])

        assert result.rejection_reason_counts.get("DUPLICATE_SIGNAL") == 1


class TestReplayClippedOrders:
    @pytest.mark.asyncio
    async def test_clipped_order_uses_effective_quantity(self):
        signal = _make_signal()
        signal.quantity = Decimal("0.2")
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(return_value=_clipped_result(signal))

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal])

        assert len(result.clipped_orders) == 1
        assert result.clipped_orders[0].normalized_qty == Decimal("0.1")
        assert result.clipped_orders[0].fills[0].qty == Decimal("0.1")


class TestReplayExceptionHandling:
    @pytest.mark.asyncio
    async def test_exception_generates_rejected_and_error(self):
        signal = _make_signal()
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(
            side_effect=RuntimeError("Risk engine unavailable")
        )

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal])

        assert len(result.rejected_orders) == 1
        assert result.rejected_orders[0].rejection_reason == "RISK_ENGINE_EXCEPTION"
        assert result.rejection_reason_counts.get("RISK_ENGINE_EXCEPTION") == 1
        assert len(result.errors) == 1
        assert "Risk engine unavailable" in result.errors[0]
        assert len(result.risk_decisions) == 1
        assert result.risk_decisions[0].decision == OrderDecision.REJECTED
        assert len(result.fills) == 0


class TestReplayRiskDecisions:
    @pytest.mark.asyncio
    async def test_risk_decisions_recorded(self):
        signal = _make_signal()
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(
            return_value=_rejected_result(signal, "RISK_CHECK")
        )

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal])

        assert len(result.risk_decisions) == 1
        assert result.risk_decisions[0].decision == OrderDecision.REJECTED
        assert result.risk_decisions[0].rejection_reason == "RISK_CHECK"


class TestEmptySignals:
    @pytest.mark.asyncio
    async def test_empty_signals_returns_empty_result(self):
        mock_integration = AsyncMock()
        replay = EventDrivenRiskReplay(risk_integration=mock_integration)

        result = await replay.replay(signals=[])

        assert len(result.raw_signals) == 0
        assert len(result.approved_orders) == 0
        assert len(result.rejected_orders) == 0
        assert len(result.clipped_orders) == 0
        assert len(result.errors) == 0
        assert len(result.risk_decisions) == 0


class TestInvalidSideHandling:
    @pytest.mark.asyncio
    async def test_none_signal_type_generates_invalid_side_rejected(self):
        signal = Signal(
            signal_id="test-none-signal",
            strategy_name="test_strategy",
            signal_type=SignalType.NONE,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            timestamp=None,
        )
        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(return_value=_approved_result(signal))

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal])

        assert len(result.rejected_orders) == 1
        assert result.rejected_orders[0].rejection_reason == "INVALID_SIDE"
        assert len(result.errors) == 1
        assert "test-none-signal" in result.errors[0]
        assert len(result.fills) == 0

    @pytest.mark.asyncio
    async def test_invalid_side_does_not_break_replay(self):
        signal_valid = _make_signal("BTCUSDT")
        signal_invalid = Signal(
            signal_id="test-none-signal",
            strategy_name="test_strategy",
            signal_type=SignalType.NONE,
            symbol="ETHUSDT",
            price=Decimal("3000"),
            quantity=Decimal("0.5"),
            timestamp=None,
        )

        async def mock_evaluate(sig):
            return _approved_result(sig)

        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(side_effect=mock_evaluate)

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal_valid, signal_invalid])

        assert len(result.raw_signals) == 2
        assert len(result.fills) == 1
        assert len(result.rejected_orders) == 1
        assert result.rejected_orders[0].rejection_reason == "INVALID_SIDE"


class TestMissingEffectiveQuantity:
    @pytest.mark.asyncio
    async def test_clipped_with_none_effective_quantity_rejected(self):
        signal = _make_signal()
        signal.quantity = Decimal("0.2")

        clipped_result = BacktestSignalResult(
            signal=signal,
            status=BacktestSignalStatus.CLIPPED,
            effective_quantity=None,
        )

        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(return_value=clipped_result)

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal])

        assert len(result.rejected_orders) == 1
        assert result.rejected_orders[0].rejection_reason == "MISSING_EFFECTIVE_QTY"
        assert len(result.clipped_orders) == 0
        assert len(result.fills) == 0

    @pytest.mark.asyncio
    async def test_clipped_with_zero_effective_quantity_rejected(self):
        signal = _make_signal()
        signal.quantity = Decimal("0.2")

        clipped_result = BacktestSignalResult(
            signal=signal,
            status=BacktestSignalStatus.CLIPPED,
            effective_quantity=Decimal("0"),
        )

        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(return_value=clipped_result)

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal])

        assert len(result.rejected_orders) == 1
        assert result.rejected_orders[0].rejection_reason == "MISSING_EFFECTIVE_QTY"
        assert len(result.clipped_orders) == 0
        assert len(result.fills) == 0


class TestSellSignalHandling:
    @pytest.mark.asyncio
    async def test_sell_signal_updates_positions_negative(self):
        buy_signal = _make_signal()
        buy_signal.signal_id = "buy-1"

        sell_signal = Signal(
            signal_id="sell-1",
            strategy_name="test_strategy",
            signal_type=SignalType.SELL,
            symbol="BTCUSDT",
            price=Decimal("51000"),
            quantity=Decimal("0.1"),
            timestamp=None,
        )

        async def mock_evaluate(sig):
            return _approved_result(sig)

        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(side_effect=mock_evaluate)

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[buy_signal, sell_signal])

        assert len(result.fills) == 2
        assert result.fills[0].side == OrderSide.BUY
        assert result.fills[1].side == OrderSide.SELL

        assert "BTCUSDT" in result.final_positions
        assert result.final_positions["BTCUSDT"] == Decimal("0")

    @pytest.mark.asyncio
    async def test_sell_signal_reduces_position(self):
        signal1 = _make_signal()
        signal1.signal_id = "buy-1"
        signal1.quantity = Decimal("0.3")

        signal2 = Signal(
            signal_id="sell-1",
            strategy_name="test_strategy",
            signal_type=SignalType.SELL,
            symbol="BTCUSDT",
            price=Decimal("51000"),
            quantity=Decimal("0.2"),
            timestamp=None,
        )

        async def mock_evaluate(sig):
            return _approved_result(sig)

        mock_integration = AsyncMock()
        mock_integration.evaluate_signal = AsyncMock(side_effect=mock_evaluate)

        replay = EventDrivenRiskReplay(risk_integration=mock_integration)
        result = await replay.replay(signals=[signal1, signal2])

        assert len(result.fills) == 2
        assert result.fills[0].side == OrderSide.BUY
        assert result.fills[1].side == OrderSide.SELL
        assert "BTCUSDT" in result.final_positions
        assert result.final_positions["BTCUSDT"] == Decimal("0.1")
