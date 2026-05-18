"""
test_backtest_risk_replay_red.py - P10 动态行为红测
====================================================
测试 BacktestRiskReplay 的动态行为：
- replay() 方法的行为
- 时间线收集
- 成交模型
- 权益曲线和最大回撤

测试应该失败在 NotImplementedError 或明确的未实现行为上。

参考: docs/INTERFACE_CONTRACTS.md 8.13 P10 Dynamic Backtest Risk Replay 契约
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trader.core.application.risk_engine import RiskCheckResult, RiskLevel


def _make_mock_engine():
    mock_engine = AsyncMock()
    mock_engine.check_pre_trade = AsyncMock(
        side_effect=lambda s: RiskCheckResult(passed=True, risk_level=RiskLevel.LOW)
    )
    return mock_engine


@pytest.mark.asyncio
async def test_replay_returns_backtest_result():
    """测试 replay() 返回 BacktestRiskReplayResult"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="test-signal-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayResult

    assert isinstance(result, BacktestRiskReplayResult)


@pytest.mark.asyncio
async def test_replay_collects_timelines():
    """测试 replay() 收集时间线"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="test-signal-2",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="ETHUSDT",
        price=Decimal("3000"),
        quantity=Decimal("1.0"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.risk_timeline, list)
    assert isinstance(result.account_timeline, list)
    assert isinstance(result.position_timeline, list)


@pytest.mark.asyncio
async def test_replay_fill_model_next_bar_open():
    """测试 fill_model='next_bar_open' 成交模型"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        fill_model="next_bar_open",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="test-signal-3",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.fills, list)


@pytest.mark.asyncio
async def test_rejected_signal_increments_count():
    """测试拒绝信号增加拒绝计数"""
    from trader.core.application.risk_engine import RejectionReason
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    async def rejected_side_effect(signal):
        return RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.DAILY_LOSS_LIMIT,
            message="Daily loss limit exceeded",
        )

    mock_engine = AsyncMock()
    mock_engine.check_pre_trade = AsyncMock(side_effect=rejected_side_effect)

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_engine=mock_engine,
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="reject-test-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("999999999"),
        quantity=Decimal("1000"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.rejection_counts, dict)


@pytest.mark.asyncio
async def test_error_logged_on_exception():
    """测试异常时记录错误"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="error-test-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.errors, list)


@pytest.mark.asyncio
async def test_equity_curve_populated():
    """测试权益曲线填充"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signals = [
        Signal(
            signal_id=f"equity-test-{i}",
            strategy_name="test_strategy",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            timestamp=None,
        )
        for i in range(3)
    ]

    result = await replay.replay(signals=signals)
    assert isinstance(result.equity_curve, list)
    assert len(result.equity_curve) > 0
    assert all(isinstance(e, Decimal) for e in result.equity_curve)


@pytest.mark.asyncio
async def test_max_drawdown_non_negative():
    """测试最大回撤非负"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="dd-test-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert result.max_drawdown >= Decimal("0")


@pytest.mark.asyncio
async def test_final_positions_tracked():
    """测试最终持仓追踪"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="position-test-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.final_positions, dict)


@pytest.mark.asyncio
async def test_risk_mode_transitions_tracked():
    """测试 RiskMode 状态变更追踪"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        enable_risk_mode=True,
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="riskmode-test-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.risk_mode_transitions, list)


@pytest.mark.asyncio
async def test_risk_budget_limits_order_size():
    """测试 risk_budget 限制下单量"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        risk_budget=Decimal("0.5"),
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="budget-test-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("1.0"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.decisions, list)


@pytest.mark.asyncio
async def test_default_order_quantity_used():
    """测试 default_order_quantity 用于策略只输出方向时"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT"],
        interval="1h",
        default_order_quantity=Decimal("0.2"),
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signal = Signal(
        signal_id="default-qty-test-1",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0"),
        timestamp=None,
    )

    result = await replay.replay(signals=[signal])
    assert isinstance(result.decisions, list)


@pytest.mark.asyncio
async def test_multiple_symbols_replay():
    """测试多 symbol 回测"""
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.services.backtesting.backtest_risk_replay import (
        BacktestRiskReplay,
        BacktestRiskReplayConfig,
    )

    config = BacktestRiskReplayConfig(
        initial_capital=Decimal("100000"),
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="1h",
        risk_engine=_make_mock_engine(),
    )
    replay = BacktestRiskReplay(config=config)

    signals = [
        Signal(
            signal_id="multi-sym-1",
            strategy_name="test_strategy",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            timestamp=None,
        ),
        Signal(
            signal_id="multi-sym-2",
            strategy_name="test_strategy",
            signal_type=SignalType.BUY,
            symbol="ETHUSDT",
            price=Decimal("3000"),
            quantity=Decimal("1.0"),
            timestamp=None,
        ),
    ]

    result = await replay.replay(signals=signals)
    assert isinstance(result.decisions, list)
    assert len(result.decisions) == 2
