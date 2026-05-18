"""
test_backtest_risk_replay.py - P10 BacktestRiskReplayEngine 单测
================================================================
测试 BacktestRiskReplayEngine 的时间推进骨架。

场景：
1. approved - 成交后 position/account 变化
2. rejected - 不改变 position
3. clipped - 按裁剪量成交
4. 无 risk_engine fail-closed
5. sizing 解析失败不被吞
6. 多信号 equity_curve 长度
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel
from trader.core.domain.models.market_rules import OrderSide
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.backtesting.backtest_risk_replay import (
    BacktestRiskReplay,
    BacktestRiskReplayConfig,
    BacktestRiskReplayEngine,
    BacktestRiskReplayResult,
)


def _make_signal(
    signal_id: str = "sig1",
    symbol: str = "BTCUSDT",
    signal_type: SignalType = SignalType.BUY,
    quantity: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("50000"),
    timestamp_ms: int = 1000000,
) -> Signal:
    return Signal(
        signal_id=signal_id,
        strategy_name="test_strategy",
        signal_type=signal_type,
        symbol=symbol,
        price=price,
        quantity=quantity,
        timestamp=timestamp_ms,
    )


def _make_signal_with_dt(
    signal_id: str = "sig1",
    symbol: str = "BTCUSDT",
    signal_type: SignalType = SignalType.BUY,
    quantity: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("50000"),
    index: int = 0,
) -> Signal:
    return Signal(
        signal_id=signal_id,
        strategy_name="test_strategy",
        signal_type=signal_type,
        symbol=symbol,
        price=price,
        quantity=quantity,
        timestamp=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    )


def _make_mock_engine(side_effect_fn):
    mock_engine = AsyncMock()
    mock_engine.check_pre_trade = AsyncMock(side_effect=side_effect_fn)
    return mock_engine


def _approved_result(signal: Signal) -> RiskCheckResult:
    return RiskCheckResult(
        passed=True,
        risk_level=RiskLevel.LOW,
    )


def _rejected_result(signal: Signal, reason: str = "DAILY_LOSS_LIMIT") -> RiskCheckResult:
    return RiskCheckResult(
        passed=False,
        risk_level=RiskLevel.HIGH,
        rejection_reason=RejectionReason(reason),
        message="Daily loss limit exceeded",
    )


def _clipped_result(signal: Signal, max_allowed: str = "0.05") -> RiskCheckResult:
    return RiskCheckResult(
        passed=False,
        risk_level=RiskLevel.MEDIUM,
        rejection_reason=RejectionReason.INSUFFICIENT_BALANCE,
        message="Risk sizing limit",
        details={
            "risk_sizing_decision": {
                "max_allowed_qty": max_allowed,
                "original_quantity": str(signal.quantity),
            }
        },
    )


def _invalid_sizing_result(signal: Signal) -> RiskCheckResult:
    return RiskCheckResult(
        passed=False,
        risk_level=RiskLevel.HIGH,
        rejection_reason=RejectionReason.INSUFFICIENT_BALANCE,
        message="Risk sizing limit",
        details={
            "risk_sizing_decision": {
                "max_allowed_qty": "not-a-number",
            }
        },
    )


class TestFailClosedWithoutRiskEngine:
    """测试无 risk_engine 时 fail-closed"""

    @pytest.mark.asyncio
    async def test_no_risk_engine_rejects_signal(self):
        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.decisions) == 1
        assert result.decisions[0].decision == "REJECTED"

    @pytest.mark.asyncio
    async def test_no_risk_engine_rejection_reason_is_system_error(self):
        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert result.decisions[0].rejection_reason == "RISK_SYSTEM_ERROR"

    @pytest.mark.asyncio
    async def test_no_risk_engine_no_fill_created(self):
        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.fills) == 0


class TestReplayRiskEngineConsistency:
    """测试 replay decision 与直接 RiskEngine.check_pre_trade() 一致性

    同一 signal + 同一 risk_engine，replay 的 decision 分类
    应与直接调用 check_pre_trade() 的结果一致。
    """

    @pytest.mark.asyncio
    async def test_approved_signal_matches_direct_check(self):
        direct_result = _approved_result(None)

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(return_value=direct_result)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert result.decisions[0].decision == "APPROVED"
        assert result.decisions[0].effective_quantity == signal.quantity
        assert result.decisions[0].rejection_reason is None

    @pytest.mark.asyncio
    async def test_rejected_signal_matches_direct_check(self):
        direct_result = _rejected_result(None, "DAILY_LOSS_LIMIT")

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(return_value=direct_result)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[0].effective_quantity == Decimal("0")
        assert result.decisions[0].rejection_reason == "DAILY_LOSS_LIMIT"

    @pytest.mark.asyncio
    async def test_clipped_signal_matches_direct_check(self):
        signal = _make_signal(timestamp_ms=1000000)
        direct_result = _clipped_result(signal, "0.05")

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(return_value=direct_result)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        result = await engine.replay([signal])

        assert result.decisions[0].decision == "CLIPPED"
        assert result.decisions[0].effective_quantity == Decimal("0.05")
        assert result.decisions[0].sizing_decision is not None

    @pytest.mark.asyncio
    async def test_mixed_signals_match_direct_check(self):
        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]
        results = [
            _approved_result(signals[0]),
            _rejected_result(signals[1], "DAILY_LOSS_LIMIT"),
            _clipped_result(signals[2], "0.03"),
        ]
        call_idx = 0

        async def side_effect(signal):
            nonlocal call_idx
            r = results[call_idx]
            call_idx += 1
            return r

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        result = await engine.replay(signals)

        assert result.decisions[0].decision == "APPROVED"
        assert result.decisions[1].decision == "REJECTED"
        assert result.decisions[2].decision == "CLIPPED"

    @pytest.mark.asyncio
    async def test_replay_and_integration_classify_identically(self):
        from trader.services.backtesting.backtest_risk_integration import (
            BacktestRiskIntegration,
            BacktestSignalStatus,
        )

        signals = [_make_signal_with_dt(signal_id=f"sig{i}", index=i) for i in range(3)]
        results = [
            _approved_result(signals[0]),
            _rejected_result(signals[1], "DAILY_LOSS_LIMIT"),
            _clipped_result(signals[2], "0.03"),
        ]
        call_idx_replay = 0
        call_idx_integration = 0

        async def replay_side_effect(signal):
            nonlocal call_idx_replay
            r = results[call_idx_replay]
            call_idx_replay += 1
            return r

        async def integration_side_effect(signal):
            nonlocal call_idx_integration
            r = results[call_idx_integration]
            call_idx_integration += 1
            return r

        replay_engine = AsyncMock()
        replay_engine.check_pre_trade = AsyncMock(side_effect=replay_side_effect)

        integration_engine = AsyncMock()
        integration_engine.check_pre_trade = AsyncMock(side_effect=integration_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=replay_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        integration = BacktestRiskIntegration(integration_engine)

        replay_result = await engine.replay(signals)

        integration_statuses = []
        for sig in signals:
            ir = await integration.evaluate_signal(sig)
            integration_statuses.append(ir.status)

        expected_map = {
            BacktestSignalStatus.APPROVED: "APPROVED",
            BacktestSignalStatus.CLIPPED: "CLIPPED",
            BacktestSignalStatus.REJECTED: "REJECTED",
        }

        for i, (decision, int_status) in enumerate(
            zip(replay_result.decisions, integration_statuses)
        ):
            assert (
                decision.decision == expected_map[int_status]
            ), f"Signal {i}: replay={decision.decision} vs integration={int_status}"

    @pytest.mark.asyncio
    async def test_effective_quantity_matches_integration(self):
        from trader.services.backtesting.backtest_risk_integration import BacktestRiskIntegration

        signals = [_make_signal_with_dt(signal_id=f"sig{i}", index=i) for i in range(2)]
        results = [
            _approved_result(signals[0]),
            _clipped_result(signals[1], "0.03"),
        ]
        call_idx_replay = 0
        call_idx_integration = 0

        async def replay_side_effect(signal):
            nonlocal call_idx_replay
            r = results[call_idx_replay]
            call_idx_replay += 1
            return r

        async def integration_side_effect(signal):
            nonlocal call_idx_integration
            r = results[call_idx_integration]
            call_idx_integration += 1
            return r

        replay_engine = AsyncMock()
        replay_engine.check_pre_trade = AsyncMock(side_effect=replay_side_effect)

        integration_engine = AsyncMock()
        integration_engine.check_pre_trade = AsyncMock(side_effect=integration_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=replay_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        integration = BacktestRiskIntegration(integration_engine)

        replay_result = await engine.replay(signals)

        integration_results = []
        for sig in signals:
            ir = await integration.evaluate_signal(sig)
            integration_results.append(ir)

        for i, (decision, int_result) in enumerate(
            zip(replay_result.decisions, integration_results)
        ):
            assert (
                decision.effective_quantity == int_result.effective_quantity
            ), f"Signal {i}: replay eq={decision.effective_quantity} vs integration eq={int_result.effective_quantity}"

    @pytest.mark.asyncio
    async def test_rejection_reason_matches_integration(self):
        from trader.services.backtesting.backtest_risk_integration import BacktestRiskIntegration

        signals = [_make_signal_with_dt(signal_id=f"sig{i}", index=i) for i in range(2)]
        results = [
            _rejected_result(signals[0], "DAILY_LOSS_LIMIT"),
            _rejected_result(signals[1], "MAX_POSITIONS"),
        ]
        call_idx_replay = 0
        call_idx_integration = 0

        async def replay_side_effect(signal):
            nonlocal call_idx_replay
            r = results[call_idx_replay]
            call_idx_replay += 1
            return r

        async def integration_side_effect(signal):
            nonlocal call_idx_integration
            r = results[call_idx_integration]
            call_idx_integration += 1
            return r

        replay_engine = AsyncMock()
        replay_engine.check_pre_trade = AsyncMock(side_effect=replay_side_effect)

        integration_engine = AsyncMock()
        integration_engine.check_pre_trade = AsyncMock(side_effect=integration_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=replay_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        integration = BacktestRiskIntegration(integration_engine)

        replay_result = await engine.replay(signals)

        integration_results = []
        for sig in signals:
            ir = await integration.evaluate_signal(sig)
            integration_results.append(ir)

        for i, (decision, int_result) in enumerate(
            zip(replay_result.decisions, integration_results)
        ):
            assert (
                decision.rejection_reason == int_result.rejection_reason
            ), f"Signal {i}: replay reason={decision.rejection_reason} vs integration reason={int_result.rejection_reason}"


class TestVectorBTReplayConsistency:
    """测试 VectorBT risk-adjusted 与 replay 订单分类一致性

    简单单资产场景下，同一 signal 序列通过 VectorBTAdapterWithRisk
    和 BacktestRiskReplayEngine 处理，订单分类应一致。
    真正调用 VectorBTAdapterWithRisk._build_risk_adjusted_input_plan()，
    比较 plan 的 approved_orders / clipped_orders / rejected_orders
    与 replay 的 decision 分类。
    """

    @pytest.mark.asyncio
    async def test_order_classification_matches_vectorbt_plan(self):
        from trader.services.backtesting.ports import BacktestConfig
        from trader.services.backtesting.vectorbt_risk_adapter import (
            VectorBTAdapterWithRisk,
            VectorBTRiskAdapterConfig,
        )

        signals = [_make_signal_with_dt(signal_id=f"sig{i}", index=i) for i in range(3)]
        results = [
            _approved_result(signals[0]),
            _rejected_result(signals[1], "DAILY_LOSS_LIMIT"),
            _clipped_result(signals[2], "0.03"),
        ]
        call_idx_replay = 0
        call_idx_vbt = 0

        async def replay_side_effect(signal):
            nonlocal call_idx_replay
            r = results[call_idx_replay]
            call_idx_replay += 1
            return r

        async def vbt_side_effect(signal):
            nonlocal call_idx_vbt
            r = results[call_idx_vbt]
            call_idx_vbt += 1
            return r

        replay_engine = AsyncMock()
        replay_engine.check_pre_trade = AsyncMock(side_effect=replay_side_effect)

        vbt_engine = AsyncMock()
        vbt_engine.check_pre_trade = AsyncMock(side_effect=vbt_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=replay_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        replay_result = await engine.replay(signals)

        class _MockBaseAdapter:
            pass

        vbt_config = VectorBTRiskAdapterConfig(
            default_order_quantity=Decimal("0.1"),
        )
        adapter = VectorBTAdapterWithRisk(
            base_adapter=_MockBaseAdapter(),
            risk_engine=vbt_engine,
            config=vbt_config,
        )

        bt_config = BacktestConfig(
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
            interval="1m",
        )

        class _MockKline:
            def __init__(self, close_price, ts):
                self.close = Decimal(str(close_price))
                self.timestamp = ts

        klines = [_MockKline("50000", signals[i].timestamp) for i in range(3)]

        plan = await adapter._build_risk_adjusted_input_plan(bt_config, klines, signals)

        replay_approved = [d for d in replay_result.decisions if d.decision == "APPROVED"]
        replay_clipped = [d for d in replay_result.decisions if d.decision == "CLIPPED"]
        replay_rejected = [d for d in replay_result.decisions if d.decision == "REJECTED"]

        assert len(replay_approved) == len(
            plan.approved_orders
        ), f"approved count: replay={len(replay_approved)} vs vbt={len(plan.approved_orders)}"
        assert len(replay_clipped) == len(
            plan.clipped_orders
        ), f"clipped count: replay={len(replay_clipped)} vs vbt={len(plan.clipped_orders)}"
        assert len(replay_rejected) == len(
            plan.rejected_orders
        ), f"rejected count: replay={len(replay_rejected)} vs vbt={len(plan.rejected_orders)}"

        for i, decision in enumerate(replay_result.decisions):
            sig = signals[i]
            if decision.decision == "APPROVED":
                assert any(
                    o["signal_id"] == sig.signal_id for o in plan.approved_orders
                ), f"Signal {i}: replay APPROVED but not in vbt approved_orders"
            elif decision.decision == "CLIPPED":
                assert any(
                    o["signal_id"] == sig.signal_id for o in plan.clipped_orders
                ), f"Signal {i}: replay CLIPPED but not in vbt clipped_orders"
            elif decision.decision == "REJECTED":
                assert any(
                    o["signal_id"] == sig.signal_id for o in plan.rejected_orders
                ), f"Signal {i}: replay REJECTED but not in vbt rejected_orders"

    @pytest.mark.asyncio
    async def test_effective_quantity_matches_vectorbt_plan(self):
        from trader.services.backtesting.ports import BacktestConfig
        from trader.services.backtesting.vectorbt_risk_adapter import (
            VectorBTAdapterWithRisk,
            VectorBTRiskAdapterConfig,
        )

        signals = [_make_signal_with_dt(signal_id=f"sig{i}", index=i) for i in range(2)]
        results = [
            _approved_result(signals[0]),
            _clipped_result(signals[1], "0.03"),
        ]
        call_idx_replay = 0
        call_idx_vbt = 0

        async def replay_side_effect(signal):
            nonlocal call_idx_replay
            r = results[call_idx_replay]
            call_idx_replay += 1
            return r

        async def vbt_side_effect(signal):
            nonlocal call_idx_vbt
            r = results[call_idx_vbt]
            call_idx_vbt += 1
            return r

        replay_engine = AsyncMock()
        replay_engine.check_pre_trade = AsyncMock(side_effect=replay_side_effect)

        vbt_engine = AsyncMock()
        vbt_engine.check_pre_trade = AsyncMock(side_effect=vbt_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=replay_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        replay_result = await engine.replay(signals)

        class _MockBaseAdapter:
            pass

        adapter = VectorBTAdapterWithRisk(
            base_adapter=_MockBaseAdapter(),
            risk_engine=vbt_engine,
            config=VectorBTRiskAdapterConfig(default_order_quantity=Decimal("0.1")),
        )

        bt_config = BacktestConfig(
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
            interval="1m",
        )

        class _MockKline:
            def __init__(self, close_price, ts):
                self.close = Decimal(str(close_price))
                self.timestamp = ts

        klines = [_MockKline("50000", signals[i].timestamp) for i in range(2)]

        plan = await adapter._build_risk_adjusted_input_plan(bt_config, klines, signals)

        approved_decision = replay_result.decisions[0]
        assert approved_decision.decision == "APPROVED"
        assert len(plan.approved_orders) == 1
        assert (
            Decimal(plan.approved_orders[0]["effective_quantity"])
            == approved_decision.effective_quantity
        )

        clipped_decision = replay_result.decisions[1]
        assert clipped_decision.decision == "CLIPPED"
        assert len(plan.clipped_orders) == 1
        assert (
            Decimal(plan.clipped_orders[0]["effective_quantity"])
            == clipped_decision.effective_quantity
        )

    @pytest.mark.asyncio
    async def test_rejection_reason_counts_match_vectorbt_plan(self):
        from trader.services.backtesting.ports import BacktestConfig
        from trader.services.backtesting.vectorbt_risk_adapter import (
            VectorBTAdapterWithRisk,
            VectorBTRiskAdapterConfig,
        )

        signals = [_make_signal_with_dt(signal_id=f"sig{i}", index=i) for i in range(3)]
        results = [
            _approved_result(signals[0]),
            _rejected_result(signals[1], "DAILY_LOSS_LIMIT"),
            _rejected_result(signals[2], "MAX_POSITIONS"),
        ]
        call_idx_replay = 0
        call_idx_vbt = 0

        async def replay_side_effect(signal):
            nonlocal call_idx_replay
            r = results[call_idx_replay]
            call_idx_replay += 1
            return r

        async def vbt_side_effect(signal):
            nonlocal call_idx_vbt
            r = results[call_idx_vbt]
            call_idx_vbt += 1
            return r

        replay_engine = AsyncMock()
        replay_engine.check_pre_trade = AsyncMock(side_effect=replay_side_effect)

        vbt_engine = AsyncMock()
        vbt_engine.check_pre_trade = AsyncMock(side_effect=vbt_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=replay_engine,
        )

        engine = BacktestRiskReplayEngine(config)
        replay_result = await engine.replay(signals)

        class _MockBaseAdapter:
            pass

        adapter = VectorBTAdapterWithRisk(
            base_adapter=_MockBaseAdapter(),
            risk_engine=vbt_engine,
            config=VectorBTRiskAdapterConfig(default_order_quantity=Decimal("0.1")),
        )

        bt_config = BacktestConfig(
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
            interval="1m",
        )

        class _MockKline:
            def __init__(self, close_price, ts):
                self.close = Decimal(str(close_price))
                self.timestamp = ts

        klines = [_MockKline("50000", signals[i].timestamp) for i in range(3)]

        plan = await adapter._build_risk_adjusted_input_plan(bt_config, klines, signals)

        replay_rejection_reasons = {}
        for d in replay_result.decisions:
            if d.decision == "REJECTED" and d.rejection_reason:
                replay_rejection_reasons[d.rejection_reason] = (
                    replay_rejection_reasons.get(d.rejection_reason, 0) + 1
                )

        assert replay_rejection_reasons == plan.rejection_reason_counts


class TestRiskAdjustedMetrics:
    """测试 risk_adjusted_metrics 从 replay 状态推导"""

    @pytest.mark.asyncio
    async def test_metrics_default_when_no_risk_events(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert metrics.clip_counts == 0
        assert metrics.risk_avoided_notional == Decimal("0")
        assert metrics.max_drawdown_before_risk == Decimal("0")
        assert metrics.max_drawdown_after_risk == Decimal("0")
        assert metrics.risk_mode_durations == {}
        assert metrics.max_exposure_after_risk == Decimal("0")
        assert metrics.max_margin_ratio_after_risk == Decimal("0")
        assert len(metrics.risk_adjusted_equity_curve) == 3

    @pytest.mark.asyncio
    async def test_rejection_counts_in_metrics(self):
        call_count = 0

        def side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _rejected_result(signal, "DAILY_LOSS_LIMIT")
            return _approved_result(signal)

        mock_engine = _make_mock_engine(side_effect_fn=side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert metrics.rejection_counts.get("DAILY_LOSS_LIMIT", 0) >= 2

    @pytest.mark.asyncio
    async def test_clip_counts_in_metrics(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _clipped_result(s, "0.05"))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert metrics.clip_counts == 3

    @pytest.mark.asyncio
    async def test_risk_avoided_notional_rejected(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _rejected_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(quantity=Decimal("0.1"), price=Decimal("50000"))

        result = await engine.replay([signal])

        metrics = result.risk_adjusted_metrics
        assert metrics.risk_avoided_notional == Decimal("5000")

    @pytest.mark.asyncio
    async def test_risk_avoided_notional_clipped(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _clipped_result(s, "0.05"))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(quantity=Decimal("0.1"), price=Decimal("50000"))

        result = await engine.replay([signal])

        metrics = result.risk_adjusted_metrics
        expected_avoided = Decimal("0.1") * Decimal("50000") - Decimal("0.05") * Decimal("50000")
        assert metrics.risk_avoided_notional == expected_avoided

    @pytest.mark.asyncio
    async def test_risk_mode_durations_tracked(self):
        call_count = 0

        def side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _rejected_result(signal, "DAILY_LOSS_LIMIT")
            return _approved_result(signal)

        mock_engine = _make_mock_engine(side_effect_fn=side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert len(metrics.risk_mode_durations) > 0
        assert any(d > 0 for d in metrics.risk_mode_durations.values())

    @pytest.mark.asyncio
    async def test_max_exposure_before_and_after_risk(self):
        call_count = 0

        def side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return _rejected_result(signal, "DAILY_LOSS_LIMIT")
            return _approved_result(signal)

        mock_engine = _make_mock_engine(side_effect_fn=side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(4)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert metrics.max_exposure_before_risk >= Decimal("0")
        assert metrics.max_exposure_after_risk >= Decimal("0")

    @pytest.mark.asyncio
    async def test_drawdown_before_after_risk(self):
        call_count = 0

        def side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return _rejected_result(signal, "DAILY_LOSS_LIMIT")
            return _approved_result(signal)

        mock_engine = _make_mock_engine(side_effect_fn=side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(4)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert metrics.max_drawdown_before_risk >= Decimal("0")
        assert metrics.max_drawdown_after_risk >= Decimal("0")

    @pytest.mark.asyncio
    async def test_max_margin_ratio_after_risk(self):
        call_count = 0

        def side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return _rejected_result(signal, "DAILY_LOSS_LIMIT")
            return _approved_result(signal)

        mock_engine = _make_mock_engine(side_effect_fn=side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(4)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert metrics.max_margin_ratio_after_risk >= Decimal("0")

    @pytest.mark.asyncio
    async def test_no_risk_mode_after_risk_metrics_are_zero(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        assert metrics.max_exposure_after_risk == Decimal("0")
        assert metrics.max_margin_ratio_after_risk == Decimal("0")
        assert metrics.max_drawdown_after_risk == Decimal("0")

    @pytest.mark.asyncio
    async def test_multi_level_risk_mode_durations_no_overlap(self):
        call_count = 0

        def side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return _rejected_result(signal, "DAILY_LOSS_LIMIT")
            elif call_count <= 2:
                return _rejected_result(signal, "DAILY_LOSS_LIMIT")
            return _approved_result(signal)

        mock_engine = _make_mock_engine(side_effect_fn=side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(4)
        ]

        result = await engine.replay(signals)

        metrics = result.risk_adjusted_metrics
        durations = metrics.risk_mode_durations
        if "CLOSE_ONLY" in durations and "CANCEL_ALL_AND_HALT" in durations:
            total_duration = sum(durations.values())
            last_ts = result.risk_timeline[-1].timestamp_ms
            first_risk_ts = min(
                t.timestamp_ms for t in result.risk_timeline if t.risk_mode != "NORMAL"
            )
            wall_clock = last_ts - first_risk_ts
            assert (
                total_duration <= wall_clock
            ), f"Durations overlap: total={total_duration} > wall_clock={wall_clock}"


class TestRiskSnapshotFields:
    """测试 RiskSnapshot 的 account_summary 和 position_summary 填充"""

    @pytest.mark.asyncio
    async def test_risk_snapshot_has_account_summary(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        snap = result.risk_timeline[0]
        assert snap.account_summary is not None
        assert "cash" in snap.account_summary

    @pytest.mark.asyncio
    async def test_risk_snapshot_has_position_summary(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        snap = result.risk_timeline[0]
        assert snap.position_summary is not None
        assert "BTCUSDT" in snap.position_summary
        assert "qty" in snap.position_summary["BTCUSDT"]

    @pytest.mark.asyncio
    async def test_risk_snapshot_decision_and_rejection_reason(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _rejected_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        snap = result.risk_timeline[0]
        assert snap.decision == "REJECTED"
        assert snap.rejection_reason is not None

    @pytest.mark.asyncio
    async def test_risk_snapshot_sizing_decision_when_clipped(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _clipped_result(s, "0.05"))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        snap = result.risk_timeline[0]
        assert snap.decision == "CLIPPED"
        assert snap.sizing_decision is not None


class TestRiskModeIntegration:
    """测试 RiskMode 接入 replay"""

    @pytest.mark.asyncio
    async def test_risk_mode_escalates_on_rejection(self):
        from trader.core.application.risk_engine import RejectionReason, RiskLevel

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
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]

        result = await engine.replay(signals)

        assert len(result.risk_mode_transitions) > 0
        assert result.risk_mode_transitions[0].mode_after in [
            "CLOSE_ONLY",
            "CANCEL_ALL_AND_HALT",
            "LIQUIDATE_AND_DISCONNECT",
        ]

    @pytest.mark.asyncio
    async def test_risk_mode_rejects_after_escalation(self):
        from trader.core.application.risk_engine import RejectionReason, RiskLevel

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
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(5)
        ]

        result = await engine.replay(signals)

        assert len(result.risk_mode_transitions) > 0
        last_mode = result.risk_mode_transitions[-1].mode_after
        assert last_mode in ["CLOSE_ONLY", "CANCEL_ALL_AND_HALT", "LIQUIDATE_AND_DISCONNECT"]

        last_decision = result.decisions[-1]
        assert last_decision.decision == "REJECTED"

    @pytest.mark.asyncio
    async def test_risk_mode_not_enabled_ignored(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=False,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.fills) == 1
        assert len(result.risk_mode_transitions) == 0

    @pytest.mark.asyncio
    async def test_risk_mode_replay_idempotent(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        sig1 = _make_signal(signal_id="sig1", timestamp_ms=1000000)
        result1 = await engine.replay([sig1])

        sig2 = _make_signal(signal_id="sig2", timestamp_ms=2000000)
        result2 = await engine.replay([sig2])

        assert len(result1.risk_mode_transitions) == 0
        assert len(result2.risk_mode_transitions) == 0
        assert result1.fills[0].symbol == result2.fills[0].symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_risk_mode_replay_count_reset_between_runs(self):
        from trader.core.application.risk_engine import RejectionReason, RiskLevel

        async def single_rejection_side_effect(signal):
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.DAILY_LOSS_LIMIT,
                message="Daily loss limit exceeded",
            )

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=single_rejection_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        sig1 = _make_signal(signal_id="sig1", timestamp_ms=1000000)
        result1 = await engine.replay([sig1])

        sig2 = _make_signal(signal_id="sig2", timestamp_ms=2000000)
        result2 = await engine.replay([sig2])

        assert len(result1.risk_mode_transitions) == 1
        assert result1.risk_mode_transitions[0].mode_after == "CLOSE_ONLY"

        assert len(result2.risk_mode_transitions) == 1
        assert result2.risk_mode_transitions[0].mode_before == "NORMAL"
        assert result2.risk_mode_transitions[0].mode_after == "CLOSE_ONLY"

    @pytest.mark.asyncio
    async def test_risk_timeline_shows_risk_mode_changes(self):
        from trader.core.application.risk_engine import RejectionReason, RiskLevel

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
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(4)
        ]

        result = await engine.replay(signals)

        assert len(result.risk_timeline) == 4
        non_normal_modes = [snap for snap in result.risk_timeline if snap.risk_mode != "NORMAL"]
        assert (
            len(non_normal_modes) >= 1
        ), "timeline should contain at least one non-NORMAL risk mode after rejections"

    @pytest.mark.asyncio
    async def test_clipped_signal_blocked_by_risk_mode(self):
        from trader.core.application.risk_engine import RejectionReason, RiskLevel

        call_count = 0

        async def mixed_side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.HIGH,
                    rejection_reason=RejectionReason.DAILY_LOSS_LIMIT,
                    message="Daily loss limit exceeded",
                )
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.MEDIUM,
                rejection_reason=RejectionReason.INSUFFICIENT_BALANCE,
                message="Risk sizing limit",
                details={"risk_sizing_decision": {"max_allowed_qty": "0.05"}},
            )

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=mixed_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(4)
        ]

        result = await engine.replay(signals)

        assert len(result.decisions) == 4
        clipped_decision = result.decisions[3]
        assert clipped_decision.decision == "REJECTED"
        assert clipped_decision.rejection_reason == "RISK_MODE_CANCEL_ALL_AND_HALT"
        assert clipped_decision.effective_quantity == Decimal("0")
        assert len(result.fills) == 0
        assert result.final_positions == {}

    @pytest.mark.asyncio
    async def test_close_only_blocks_new_position(self):
        from trader.core.application.risk_engine import RejectionReason, RiskLevel

        call_count = 0

        async def mixed_side_effect(signal):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.HIGH,
                    rejection_reason=RejectionReason.DAILY_LOSS_LIMIT,
                    message="Daily loss limit exceeded",
                )
            return RiskCheckResult(
                passed=True,
                risk_level=RiskLevel.LOW,
                message="OK",
            )

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=mixed_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(2)
        ]

        result = await engine.replay(signals)

        assert len(result.decisions) == 2
        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[1].decision == "REJECTED"
        assert result.decisions[1].rejection_reason == "RISK_MODE_CLOSE_ONLY"
        assert result.decisions[1].effective_quantity == Decimal("0")
        assert len(result.fills) == 0
        assert result.final_positions == {}

    @pytest.mark.asyncio
    async def test_no_new_positions_blocks_new_position(self):
        from trader.core.application.risk_engine import RejectionReason, RiskLevel
        from trader.core.domain.models.risk_mode import RiskMode

        async def approved_side_effect(signal):
            return RiskCheckResult(
                passed=True,
                risk_level=RiskLevel.LOW,
                message="OK",
            )

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=approved_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
            enable_risk_mode=True,
        )

        engine = BacktestRiskReplayEngine(config)

        original_reset = engine._reset_state

        def patched_reset():
            original_reset()
            engine._risk_mode_controller.force_mode(
                RiskMode.NO_NEW_POSITIONS,
                reason="test_setup",
                triggered_by="test",
            )
            engine._current_risk_mode = RiskMode.NO_NEW_POSITIONS.name

        engine._reset_state = patched_reset

        signals = [_make_signal(signal_id="sig1", timestamp_ms=1000000)]

        result = await engine.replay(signals)

        assert len(result.decisions) == 1
        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[0].rejection_reason == "RISK_MODE_NO_NEW_POSITIONS"
        assert result.decisions[0].effective_quantity == Decimal("0")
        assert len(result.fills) == 0
        assert result.final_positions == {}


class TestFailClosedWithMalformedSignal:
    """测试 malformed signal + risk_engine raises 的组合场景"""

    @pytest.mark.asyncio
    async def test_unknown_signal_type_and_risk_engine_raises_rejected(self):
        async def failing_side_effect(signal):
            raise RuntimeError("Risk engine unavailable")

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=failing_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()
        signal.signal_type = None

        result = await engine.replay([signal])

        assert len(result.decisions) == 1
        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[0].rejection_reason == "RISK_SYSTEM_ERROR"
        assert result.decisions[0].side is not None

    @pytest.mark.asyncio
    async def test_unknown_signal_type_and_risk_engine_raises_no_fill(self):
        async def failing_side_effect(signal):
            raise RuntimeError("Risk engine unavailable")

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=failing_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()
        signal.signal_type = None

        result = await engine.replay([signal])

        assert len(result.fills) == 0

    @pytest.mark.asyncio
    async def test_unknown_signal_type_decision_side_is_order_side(self):
        async def failing_side_effect(signal):
            raise RuntimeError("Risk engine unavailable")

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=failing_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()
        signal.signal_type = None

        result = await engine.replay([signal])

        decision = result.decisions[0]
        assert isinstance(decision.side, OrderSide)


class TestInvalidSizingDecision:
    """测试 sizing 解析失败不被吞"""

    @pytest.mark.asyncio
    async def test_invalid_max_allowed_qty_rejected(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _invalid_sizing_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.fills) == 0
        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[0].rejection_reason == "INVALID_RISK_SIZING_DECISION"

    @pytest.mark.asyncio
    async def test_invalid_sizing_writes_to_errors(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _invalid_sizing_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.errors) > 0
        assert any("Invalid risk_sizing_decision" in err for err in result.errors)


class TestRiskEngineException:
    """测试 risk_engine.check_pre_trade() 抛异常时的 fail-closed"""

    @pytest.mark.asyncio
    async def test_risk_engine_exception_rejected(self):
        async def failing_side_effect(signal):
            raise RuntimeError("Risk engine unavailable")

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=failing_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.decisions) == 1
        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[0].rejection_reason == "RISK_SYSTEM_ERROR"

    @pytest.mark.asyncio
    async def test_risk_engine_exception_writes_to_errors(self):
        async def failing_side_effect(signal):
            raise RuntimeError("Risk engine unavailable")

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=failing_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.errors) > 0
        assert any("Risk check failed" in err for err in result.errors)

    @pytest.mark.asyncio
    async def test_risk_engine_exception_does_not_crash_replay(self):
        async def failing_side_effect(signal):
            if signal.signal_id == "sig1":
                raise RuntimeError("Risk engine unavailable")
            return _approved_result(signal)

        mock_engine = AsyncMock()
        mock_engine.check_pre_trade = AsyncMock(side_effect=failing_side_effect)

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        sig1 = _make_signal(signal_id="sig1")
        sig2 = _make_signal(signal_id="sig2", timestamp_ms=2000000)

        result = await engine.replay([sig1, sig2])

        assert len(result.decisions) == 2
        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[1].decision == "APPROVED"
        assert len(result.errors) >= 1


class TestReplayApproved:
    """测试 approved 成交后 position/account 变化"""

    @pytest.mark.asyncio
    async def test_approved_signal_creates_fill(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(signal_id="sig1", timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert len(result.fills) == 1
        assert result.fills[0].symbol == "BTCUSDT"
        assert result.fills[0].quantity == Decimal("0.1")
        assert result.fills[0].price == Decimal("50000")

    @pytest.mark.asyncio
    async def test_approved_signal_updates_position_after_fill(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        sig1 = _make_signal(signal_id="sig1", timestamp_ms=1000000)
        sig2 = _make_signal(signal_id="sig2", timestamp_ms=2000000)

        result = await engine.replay([sig1, sig2])

        assert len(result.fills) == 2

        assert "BTCUSDT" in result.final_positions
        assert result.final_positions["BTCUSDT"] > Decimal("0")

    @pytest.mark.asyncio
    async def test_approved_signal_updates_equity(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            commission_rate=Decimal("0"),
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(
            quantity=Decimal("1"),
            price=Decimal("50000"),
            timestamp_ms=1000000,
        )

        result = await engine.replay([signal])

        assert len(result.risk_timeline) == 1

        initial_cost = Decimal("1") * Decimal("50000")
        expected_equity = Decimal("100000") - initial_cost + initial_cost * Decimal("0.1")

        assert result.risk_timeline[0].equity < Decimal("100000")


class TestReplayRejected:
    """测试 rejected 不改变 position"""

    @pytest.mark.asyncio
    async def test_rejected_signal_creates_no_fill(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _rejected_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert len(result.fills) == 0

    @pytest.mark.asyncio
    async def test_rejected_signal_unchanged_position(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _rejected_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        sig1 = _make_signal(signal_id="sig1", timestamp_ms=1000000)
        sig2 = _make_signal(signal_id="sig2", timestamp_ms=2000000)

        result = await engine.replay([sig1, sig2])

        assert len(result.fills) == 0

        assert "BTCUSDT" not in result.final_positions or result.final_positions[
            "BTCUSDT"
        ] == Decimal("0")

    @pytest.mark.asyncio
    async def test_rejected_signal_unchanged_equity(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _rejected_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert len(result.fills) == 0

        assert result.risk_timeline[0].equity == Decimal("100000")

    @pytest.mark.asyncio
    async def test_rejected_also_records_account_timeline(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _rejected_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert len(result.account_timeline) == 1
        assert result.account_timeline[0].total_equity == Decimal("100000")

    @pytest.mark.asyncio
    async def test_rejected_also_records_equity_curve(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _rejected_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert len(result.equity_curve) == 1
        assert result.equity_curve[0] == Decimal("100000")


class TestReplayClipped:
    """测试 clipped 按裁剪量成交"""

    @pytest.mark.asyncio
    async def test_clipped_signal_creates_fill_with_max_allowed_qty(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _clipped_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(
            quantity=Decimal("0.1"),
            timestamp_ms=1000000,
        )

        result = await engine.replay([signal])

        assert len(result.fills) == 1

        assert result.fills[0].quantity == Decimal("0.05")

    @pytest.mark.asyncio
    async def test_clipped_signal_updates_position_with_clipped_qty(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _clipped_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        sig1 = _make_signal(signal_id="sig1", quantity=Decimal("0.1"), timestamp_ms=1000000)
        sig2 = _make_signal(signal_id="sig2", quantity=Decimal("0.1"), timestamp_ms=2000000)

        result = await engine.replay([sig1, sig2])

        assert len(result.fills) == 2

        expected_pos = Decimal("0.05") + Decimal("0.05")
        assert result.final_positions["BTCUSDT"] == expected_pos

    @pytest.mark.asyncio
    async def test_clipped_signal_equity_less_than_full(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _clipped_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            commission_rate=Decimal("0"),
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(
            quantity=Decimal("0.1"),
            price=Decimal("50000"),
            timestamp_ms=1000000,
        )

        result = await engine.replay([signal])

        clipped_cost = Decimal("0.05") * Decimal("50000")
        margin_added = clipped_cost * Decimal("0.1")
        expected_equity = Decimal("100000") - clipped_cost + margin_added

        assert result.risk_timeline[0].equity == expected_equity


class TestEquityCurve:
    """测试 equity_curve 逐点记录"""

    @pytest.mark.asyncio
    async def test_equity_curve_length_equals_signal_count(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            commission_rate=Decimal("0"),
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(5)
        ]

        result = await engine.replay(signals)

        assert len(result.equity_curve) == 5

    @pytest.mark.asyncio
    async def test_equity_curve_tracks_approved_signals(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            commission_rate=Decimal("0"),
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(
                signal_id=f"sig{i}", quantity=Decimal("0.1"), timestamp_ms=1000000 + i * 1000000
            )
            for i in range(3)
        ]

        result = await engine.replay(signals)

        assert len(result.equity_curve) == 3

        for eq in result.equity_curve:
            assert eq < Decimal("100000")


class TestTimelines:
    """测试 account_timeline 和 position_timeline 填充"""

    @pytest.mark.asyncio
    async def test_account_timeline_populated_per_signal(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signals = [
            _make_signal(signal_id=f"sig{i}", timestamp_ms=1000000 + i * 1000000) for i in range(3)
        ]

        result = await engine.replay(signals)

        assert len(result.account_timeline) == 3

    @pytest.mark.asyncio
    async def test_position_timeline_populated_when_position_exists(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await engine.replay([signal])

        assert len(result.position_timeline) >= 1

        pos = result.position_timeline[0]
        assert pos.symbol == "BTCUSDT"
        assert pos.quantity > Decimal("0")

    @pytest.mark.asyncio
    async def test_signals_list_populated(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()

        result = await engine.replay([signal])

        assert len(result.signals) == 1
        assert result.signals[0]["signal_id"] == "sig1"
        assert result.signals[0]["symbol"] == "BTCUSDT"


class TestBacktestRiskReplayFacade:
    """测试 BacktestRiskReplay 门面"""

    @pytest.mark.asyncio
    async def test_facade_delegates_to_engine(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        replay = BacktestRiskReplay(config)

        signal = _make_signal(timestamp_ms=1000000)

        result = await replay.replay([signal])

        assert isinstance(result, BacktestRiskReplayResult)
        assert len(result.fills) == 1


class TestDecimalIntegrity:
    """测试 DTO 输出全部为 Decimal，无 float 污染"""

    @pytest.mark.asyncio
    async def test_position_snapshot_avg_price_is_decimal(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            commission_rate=Decimal("0"),
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(quantity=Decimal("0.5"))

        result = await engine.replay([signal])

        pos = result.position_timeline[0]
        assert isinstance(pos.avg_price, Decimal)
        assert isinstance(pos.quantity, Decimal)
        assert isinstance(pos.market_value, Decimal)

    @pytest.mark.asyncio
    async def test_fill_commission_is_exact_decimal(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            commission_rate=Decimal("0.0004"),
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal(quantity=Decimal("1"), price=Decimal("50000"))

        result = await engine.replay([signal])

        fill = result.fills[0]
        assert isinstance(fill.commission, Decimal)
        expected_commission = Decimal("1") * Decimal("50000") * Decimal("0.0004")
        assert fill.commission == expected_commission

    @pytest.mark.asyncio
    async def test_engine_replay_idempotent_no_state_pollution(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            commission_rate=Decimal("0"),
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        sig1 = _make_signal(signal_id="sig1", timestamp_ms=1000000)

        result1 = await engine.replay([sig1])
        result2 = await engine.replay([sig1])

        assert len(result2.equity_curve) == 1
        assert result2.equity_curve[0] == result1.equity_curve[0]

        assert len(result2.fills) == 1
        assert result2.decisions[0].decision == "APPROVED"

        sig2 = _make_signal(signal_id="sig2", quantity=Decimal("0.2"), timestamp_ms=2000000)
        result3 = await engine.replay([sig2])

        assert len(result3.equity_curve) == 1
        assert result3.equity_curve[0] < result1.equity_curve[0]


class TestSignalValidation:
    """测试 malformed signal fail-closed"""

    @pytest.mark.asyncio
    async def test_unknown_signal_type_rejected(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()
        signal.signal_type = None

        result = await engine.replay([signal])

        assert len(result.decisions) == 1
        assert result.decisions[0].decision == "REJECTED"
        assert result.decisions[0].rejection_reason == "INVALID_SIGNAL_SIDE"

    @pytest.mark.asyncio
    async def test_unknown_signal_type_writes_to_errors(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()
        signal.signal_type = None

        result = await engine.replay([signal])

        assert len(result.errors) > 0
        assert any("Unknown signal_type" in err for err in result.errors)

    @pytest.mark.asyncio
    async def test_unknown_signal_type_no_fill_created(self):
        mock_engine = _make_mock_engine(side_effect_fn=lambda s: _approved_result(s))

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1m",
            risk_engine=mock_engine,
        )

        engine = BacktestRiskReplayEngine(config)

        signal = _make_signal()
        signal.signal_type = None

        result = await engine.replay([signal])

        assert len(result.fills) == 0
