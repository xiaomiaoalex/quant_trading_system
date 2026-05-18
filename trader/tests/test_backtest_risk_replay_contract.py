"""
test_backtest_risk_replay_contract.py - P10 契约接口测试
========================================================
测试 P10 契约定义的接口、DTC 字段和默认值。

测试必须在真实接口缺失上失败（ImportError），
而不是 import 拼写错误或虚构路径。

参考: docs/INTERFACE_CONTRACTS.md 8.13 P10 Dynamic Backtest Risk Replay 契约
"""

from __future__ import annotations

from decimal import Decimal

import pytest


class TestBacktestRiskReplayConfigContract:
    """测试 BacktestRiskReplayConfig 契约"""

    def test_config_importable_from_service_layer(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayConfig

        assert BacktestRiskReplayConfig is not None

    def test_config_has_required_fields(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayConfig

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1h",
        )
        assert config.initial_capital == Decimal("100000")
        assert config.symbols == ["BTCUSDT"]
        assert config.interval == "1h"
        assert config.commission_rate == Decimal("0.0004")
        assert config.fill_model == "next_bar_open"

    def test_config_optional_fields(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayConfig

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT", "ETHUSDT"],
            interval="5m",
            risk_budget=Decimal("10"),
            default_order_quantity=Decimal("0.5"),
            enable_risk_mode=True,
        )
        assert config.risk_budget == Decimal("10")
        assert config.default_order_quantity == Decimal("0.5")
        assert config.enable_risk_mode is True
        assert config.snapshot_provider is None
        assert config.risk_engine is None

    def test_config_is_frozen(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayConfig

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1h",
        )
        with pytest.raises(AttributeError):
            config.initial_capital = Decimal("200000")

    def test_config_multiple_symbols(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayConfig

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            interval="1h",
        )
        assert len(config.symbols) == 3
        assert "BTCUSDT" in config.symbols


class TestBacktestRiskReplayResultContract:
    """测试 BacktestRiskReplayResult 契约"""

    def test_result_importable_from_service_layer(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayResult

        assert BacktestRiskReplayResult is not None

    def test_result_has_timeline_fields(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayResult

        result = BacktestRiskReplayResult()
        assert hasattr(result, "signals")
        assert hasattr(result, "decisions")
        assert hasattr(result, "fills")
        assert hasattr(result, "risk_timeline")
        assert hasattr(result, "account_timeline")
        assert hasattr(result, "position_timeline")
        assert hasattr(result, "equity_curve")
        assert hasattr(result, "max_drawdown")
        assert hasattr(result, "final_positions")
        assert hasattr(result, "errors")
        assert hasattr(result, "rejection_counts")
        assert hasattr(result, "risk_mode_transitions")
        assert hasattr(result, "risk_adjusted_metrics")

    def test_result_default_values(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayResult

        result = BacktestRiskReplayResult()
        assert result.signals == []
        assert result.decisions == []
        assert result.fills == []
        assert result.risk_timeline == []
        assert result.account_timeline == []
        assert result.position_timeline == []
        assert result.equity_curve == []
        assert result.max_drawdown == Decimal("0")
        assert result.final_positions == {}
        assert result.errors == []
        assert result.rejection_counts == {}
        assert result.risk_mode_transitions == []
        assert result.risk_adjusted_metrics is not None


class TestReplayDecisionContract:
    """测试 ReplayDecision 契约"""

    def test_decision_importable(self):
        from trader.services.backtesting.backtest_risk_replay import ReplayDecision

        assert ReplayDecision is not None

    def test_decision_has_required_fields(self):
        from trader.core.domain.models.market_rules import OrderSide
        from trader.services.backtesting.backtest_risk_replay import ReplayDecision

        decision = ReplayDecision(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.1"),
            price=Decimal("50000"),
            timestamp_ms=1000000,
            decision="APPROVED",
            effective_quantity=Decimal("0.1"),
            effective_price=Decimal("50000"),
        )
        assert decision.symbol == "BTCUSDT"
        assert decision.side == OrderSide.BUY
        assert decision.decision == "APPROVED"
        assert decision.effective_quantity == Decimal("0.1")

    def test_decision_sizing_decision_field(self):
        from trader.core.domain.models.market_rules import OrderSide
        from trader.services.backtesting.backtest_risk_replay import ReplayDecision

        decision = ReplayDecision(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1.0"),
            price=Decimal("50000"),
            timestamp_ms=1000000,
            decision="CLIPPED",
            effective_quantity=Decimal("0.5"),
            effective_price=Decimal("50000"),
            sizing_decision={"max_allowed_qty": "0.5", "reason": "risk_budget"},
        )
        assert decision.sizing_decision is not None
        assert decision.sizing_decision["max_allowed_qty"] == "0.5"

    def test_decision_rejection_reason_optional(self):
        from trader.core.domain.models.market_rules import OrderSide
        from trader.services.backtesting.backtest_risk_replay import ReplayDecision

        decision = ReplayDecision(
            symbol="ETHUSDT",
            side=OrderSide.SELL,
            quantity=Decimal("1.0"),
            price=Decimal("3000"),
            timestamp_ms=2000000,
            decision="REJECTED",
            effective_quantity=Decimal("0"),
            effective_price=Decimal("0"),
            rejection_reason="INSUFFICIENT_BALANCE",
        )
        assert decision.decision == "REJECTED"
        assert decision.rejection_reason == "INSUFFICIENT_BALANCE"


class TestSnapshotContracts:
    """测试时间线快照契约"""

    def test_risk_snapshot_importable(self):
        from trader.services.backtesting.backtest_risk_replay import RiskSnapshot

        assert RiskSnapshot is not None

    def test_risk_snapshot_extended_fields(self):
        from trader.services.backtesting.backtest_risk_replay import RiskSnapshot

        snapshot = RiskSnapshot(
            timestamp_ms=1000000,
            risk_mode="NORMAL",
            equity=Decimal("100000"),
            daily_pnl=Decimal("0"),
            daily_pnl_percent=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            drawdown=Decimal("0"),
            decision="APPROVED",
            rejection_reason=None,
            sizing_decision={"max_allowed_qty": "0.1"},
            account_summary={"available_cash": Decimal("80000")},
            position_summary={"BTCUSDT": {"qty": Decimal("0.1")}},
        )
        assert snapshot.risk_mode == "NORMAL"
        assert snapshot.decision == "APPROVED"
        assert snapshot.sizing_decision is not None

    def test_account_snapshot_importable(self):
        from trader.services.backtesting.backtest_risk_replay import AccountSnapshot

        assert AccountSnapshot is not None

    def test_account_snapshot_extended_fields(self):
        from trader.services.backtesting.backtest_risk_replay import AccountSnapshot

        snapshot = AccountSnapshot(
            timestamp_ms=1000000,
            total_equity=Decimal("100000"),
            available_cash=Decimal("80000"),
            total_position_value=Decimal("20000"),
            margin_used=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )
        assert snapshot.margin_used == Decimal("0")
        assert snapshot.unrealized_pnl == Decimal("0")

    def test_position_snapshot_importable(self):
        from trader.services.backtesting.backtest_risk_replay import PositionSnapshot

        assert PositionSnapshot is not None

    def test_position_snapshot_extended_fields(self):
        from trader.services.backtesting.backtest_risk_replay import PositionSnapshot

        snapshot = PositionSnapshot(
            timestamp_ms=1000000,
            symbol="BTCUSDT",
            quantity=Decimal("0.1"),
            avg_price=Decimal("50000"),
            market_value=Decimal("5000"),
            unrealized_pnl=Decimal("100"),
            side="LONG",
        )
        assert snapshot.unrealized_pnl == Decimal("100")
        assert snapshot.side == "LONG"


class TestProtocolsAndEngine:
    """测试 Protocol 接口和 Engine 契约"""

    def test_backtest_snapshot_provider_port_importable(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestSnapshotProviderPort

        assert BacktestSnapshotProviderPort is not None

    def test_backtest_risk_replay_engine_importable(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplayEngine

        assert BacktestRiskReplayEngine is not None

    def test_historical_crypto_risk_snapshot_provider_importable(self):
        from trader.services.backtesting.backtest_risk_replay import (
            HistoricalCryptoRiskSnapshotProvider,
        )

        assert HistoricalCryptoRiskSnapshotProvider is not None


class TestBacktestRiskReplayMainInterface:
    """测试 BacktestRiskReplay 主接口"""

    def test_class_importable(self):
        from trader.services.backtesting.backtest_risk_replay import BacktestRiskReplay

        assert BacktestRiskReplay is not None

    def test_init_requires_config(self):
        from trader.services.backtesting.backtest_risk_replay import (
            BacktestRiskReplay,
            BacktestRiskReplayConfig,
        )

        config = BacktestRiskReplayConfig(
            initial_capital=Decimal("100000"),
            symbols=["BTCUSDT"],
            interval="1h",
        )
        replay = BacktestRiskReplay(config=config)
        assert replay is not None


class TestRiskAdjustedMetricsContract:
    """测试 RiskAdjustedMetrics 契约"""

    def test_metrics_importable(self):
        from trader.services.backtesting.backtest_risk_replay import RiskAdjustedMetrics

        assert RiskAdjustedMetrics is not None

    def test_metrics_has_required_fields(self):
        from trader.services.backtesting.backtest_risk_replay import RiskAdjustedMetrics

        metrics = RiskAdjustedMetrics()
        assert metrics.risk_adjusted_equity_curve == []
        assert metrics.max_drawdown_before_risk == Decimal("0")
        assert metrics.max_drawdown_after_risk == Decimal("0")
        assert metrics.rejection_counts == {}
        assert metrics.clip_counts == 0
        assert metrics.risk_mode_durations == {}
        assert metrics.risk_avoided_notional == Decimal("0")
        assert metrics.max_exposure_before_risk == Decimal("0")
        assert metrics.max_exposure_after_risk == Decimal("0")
        assert metrics.max_margin_ratio_after_risk == Decimal("0")

    def test_metrics_populated_values(self):
        from trader.services.backtesting.backtest_risk_replay import RiskAdjustedMetrics

        metrics = RiskAdjustedMetrics(
            risk_adjusted_equity_curve=[Decimal("100000"), Decimal("99000")],
            max_drawdown_before_risk=Decimal("0.01"),
            max_drawdown_after_risk=Decimal("0.05"),
            rejection_counts={"DAILY_LOSS_LIMIT": 2},
            clip_counts=3,
            risk_mode_durations={"CLOSE_ONLY": 5000},
            risk_avoided_notional=Decimal("50000"),
            max_exposure_before_risk=Decimal("20000"),
            max_exposure_after_risk=Decimal("10000"),
            max_margin_ratio_after_risk=Decimal("0.15"),
        )
        assert len(metrics.risk_adjusted_equity_curve) == 2
        assert metrics.clip_counts == 3
        assert metrics.risk_mode_durations["CLOSE_ONLY"] == 5000
        assert metrics.risk_avoided_notional == Decimal("50000")
