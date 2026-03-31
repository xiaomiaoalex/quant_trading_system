"""
StrategyEvaluator 单元测试
==========================

测试覆盖：
1. StrategyMetrics - 指标计算和属性
2. BacktestEngine - 回测引擎和数据质量验证
3. LiveEvaluator - 实时评估和异常检测
4. 辅助函数 - 夏普率、最大回撤计算
"""
import asyncio
import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from trader.services.strategy_evaluator import (
    AnomalyAlert,
    BacktestConfig,
    BacktestEngine,
    BacktestReport,
    BacktestTrade,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    DataQualityIssue,
    DataQualityResult,
    DataQualityStatus,
    EvaluationResult,
    EvaluationStatus,
    FeatureStorePort,
    LiveEvaluator,
    LiveEvaluatorConfig,
    MarketDataProvider,
    StrategyMetrics,
)


# ============================================================================
# 测试数据构造辅助
# ============================================================================


def create_metrics(
    total_pnl: float = 1000.0,
    sharpe_ratio: float = 1.5,
    max_drawdown: float = 500.0,
    win_rate: float = 0.55,
    trade_count: int = 20,
    equity_curve: Optional[List[Decimal]] = None,
) -> StrategyMetrics:
    """创建测试用 StrategyMetrics"""
    return StrategyMetrics(
        total_pnl=Decimal(str(total_pnl)),
        sharpe_ratio=sharpe_ratio,
        max_drawdown=Decimal(str(max_drawdown)),
        win_rate=win_rate,
        trade_count=trade_count,
        equity_curve=equity_curve or [Decimal("10000"), Decimal("10100"), Decimal("10200")],
    )


def create_mock_strategy():
    """创建模拟策略"""
    @dataclass
    class MockStrategy:
        name: str = "TestStrategy"
        strategy_id: str = "test_strategy"
        
        def on_market_data(self, market_data: Any) -> Optional[Any]:
            # 简单策略：价格 > 50000 买入，否则卖出
            if market_data.price > Decimal("50000"):
                @dataclass
                class BuySignal:
                    signal_type: str = "BUY"
                    quantity: Decimal = Decimal("0.1")
                    price: Decimal = Decimal("0")
                    symbol: str = ""
                    confidence: Decimal = Decimal("1.0")
                    stop_loss: Optional[Decimal] = None
                    take_profit: Optional[Decimal] = None
                return BuySignal()
            return None
        
        def validate(self) -> Any:
            from trader.core.application.strategy_protocol import ValidationResult, ValidationStatus
            return ValidationResult(status=ValidationStatus.VALID)
    
    return MockStrategy()


# ============================================================================
# StrategyMetrics Tests
# ============================================================================


class TestStrategyMetrics:
    """StrategyMetrics 单元测试"""
    
    def test_metrics_creation(self):
        """测试指标创建"""
        metrics = create_metrics(
            total_pnl=1500.0,
            sharpe_ratio=2.0,
            max_drawdown=300.0,
            win_rate=0.6,
            trade_count=50,
        )
        
        assert metrics.total_pnl == Decimal("1500")
        assert metrics.sharpe_ratio == 2.0
        assert metrics.max_drawdown == Decimal("300")
        assert metrics.win_rate == 0.6
        assert metrics.trade_count == 50
    
    def test_metrics_type_conversion(self):
        """测试类型安全转换"""
        metrics = StrategyMetrics(
            total_pnl=1000,  # int
            sharpe_ratio=1.5,
            max_drawdown=500,  # int
            win_rate=0.5,
            trade_count=10,
        )
        
        assert isinstance(metrics.total_pnl, Decimal)
        assert isinstance(metrics.max_drawdown, Decimal)
        assert metrics.total_pnl == Decimal("1000")
        assert metrics.max_drawdown == Decimal("500")
    
    def test_is_profitable(self):
        """测试盈利判断"""
        profitable = create_metrics(total_pnl=100.0)
        assert profitable.is_profitable is True
        
        losing = create_metrics(total_pnl=-100.0)
        assert losing.is_profitable is False
        
        zero = create_metrics(total_pnl=0.0)
        assert zero.is_profitable is False
    
    def test_summary_property(self):
        """测试摘要属性"""
        metrics = create_metrics(
            total_pnl=1000.0,
            sharpe_ratio=1.5,
            max_drawdown=500.0,
            win_rate=0.55,
            trade_count=20,
        )
        
        summary = metrics.summary
        
        assert summary["total_pnl"] == 1000.0
        assert summary["sharpe_ratio"] == 1.5
        assert summary["max_drawdown"] == 500.0
        assert summary["win_rate"] == 0.55
        assert summary["trade_count"] == 20
        assert summary["is_profitable"] is True


# ============================================================================
# BacktestReport Tests
# ============================================================================


class TestBacktestReport:
    """BacktestReport 单元测试"""
    
    def test_report_creation(self):
        """测试报告创建"""
        now = datetime.now(timezone.utc)
        metrics = create_metrics()
        
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_time=now - timedelta(days=30),
            end_time=now,
            metrics=metrics,
        )
        
        assert report.strategy_name == "TestStrategy"
        assert report.metrics == metrics
        assert len(report.trades) == 0
    
    def test_return_percent_calculation(self):
        """测试收益率计算"""
        now = datetime.now(timezone.utc)
        metrics = create_metrics()
        
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_time=now - timedelta(days=30),
            end_time=now,
            metrics=metrics,
            initial_capital=Decimal("10000"),
            final_capital=Decimal("12000"),
        )
        
        assert report.return_percent == 20.0
    
    def test_return_percent_zero_capital(self):
        """测试初始资金为零时的收益率"""
        now = datetime.now(timezone.utc)
        metrics = create_metrics()
        
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_time=now - timedelta(days=30),
            end_time=now,
            metrics=metrics,
            initial_capital=Decimal("0"),
            final_capital=Decimal("10000"),
        )
        
        assert report.return_percent == 0.0
    
    def test_to_dict(self):
        """测试字典转换"""
        now = datetime.now(timezone.utc)
        metrics = create_metrics()
        
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_time=now - timedelta(days=30),
            end_time=now,
            metrics=metrics,
            initial_capital=Decimal("10000"),
            final_capital=Decimal("11000"),
            execution_time_seconds=5.5,
        )
        
        result = report.to_dict()
        
        assert result["strategy_name"] == "TestStrategy"
        assert result["return_percent"] == 10.0
        assert result["execution_time_seconds"] == 5.5
        assert result["metrics"]["total_pnl"] == 1000.0


# ============================================================================
# DataQualityResult Tests
# ============================================================================


class TestDataQualityResult:
    """DataQualityResult 单元测试"""
    
    def test_initial_status(self):
        """测试初始状态"""
        result = DataQualityResult()
        assert result.status == DataQualityStatus.PASS
        assert len(result.issues) == 0
    
    def test_add_warning_issue(self):
        """测试添加警告级别问题"""
        result = DataQualityResult()
        result.add_issue(DataQualityIssue(
            issue_type="LOW_COVERAGE",
            message="Coverage below 90%",
            severity="WARNING",
        ))
        
        assert result.status == DataQualityStatus.WARNING
        assert len(result.issues) == 1
    
    def test_add_error_issue(self):
        """测试添加错误级别问题"""
        result = DataQualityResult()
        result.add_issue(DataQualityIssue(
            issue_type="INVALID_OHLC",
            message="High < Low",
            severity="ERROR",
        ))
        
        assert result.status == DataQualityStatus.FAIL
        assert len(result.issues) == 1
    
    def test_error_overrides_warning(self):
        """测试错误级别覆盖警告"""
        result = DataQualityResult()
        result.add_issue(DataQualityIssue(
            issue_type="LOW_COVERAGE",
            message="Coverage below 90%",
            severity="WARNING",
        ))
        assert result.status == DataQualityStatus.WARNING
        
        result.add_issue(DataQualityIssue(
            issue_type="INVALID_OHLC",
            message="High < Low",
            severity="ERROR",
        ))
        
        assert result.status == DataQualityStatus.FAIL
    
    def test_to_dict(self):
        """测试字典转换"""
        result = DataQualityResult()
        result.coverage_percent = 95.0
        result.gap_count = 2
        result.total_bars = 1000
        
        result_dict = result.to_dict()
        
        assert result_dict["status"] == "PASS"
        assert result_dict["coverage_percent"] == 95.0
        assert result_dict["gap_count"] == 2
        assert result_dict["total_bars"] == 1000


# ============================================================================
# BacktestEngine Tests
# ============================================================================


class TestBacktestEngine:
    """BacktestEngine 单元测试"""
    
    @pytest.fixture
    def mock_data_provider(self):
        """创建模拟数据提供者"""
        provider = MagicMock(spec=MarketDataProvider)
        return provider
    
    def test_engine_creation(self):
        """测试引擎创建"""
        engine = BacktestEngine()
        assert engine._config is not None
        assert engine._data_provider is None
    
    def test_engine_with_custom_config(self):
        """测试自定义配置"""
        config = BacktestConfig(
            initial_capital=Decimal("50000"),
            commission_rate=Decimal("0.002"),
        )
        
        engine = BacktestEngine(config=config)
        
        assert engine._config.initial_capital == Decimal("50000")
        assert engine._config.commission_rate == Decimal("0.002")
    
    @pytest.mark.asyncio
    async def test_run_backtest_basic(self):
        """测试基本回测运行"""
        engine = BacktestEngine()
        strategy = create_mock_strategy()
        
        start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 2, tzinfo=timezone.utc)
        
        report = await engine.run_backtest(
            strategy=strategy,
            start_time=start_time,
            end_time=end_time,
        )
        
        assert report.strategy_name == "TestStrategy"
        assert report.start_time == start_time
        assert report.end_time == end_time
        assert report.metrics is not None
        assert report.execution_time_seconds >= 0
    
    @pytest.mark.asyncio
    async def test_backtest_with_mock_data(self, mock_data_provider):
        """测试使用模拟数据的回测"""
        # 构造模拟K线数据
        mock_data_provider.get_klines = AsyncMock(return_value=[
            {
                "symbol": "BTCUSDT",
                "open_time": 1704067200000,  # 2024-01-01 00:00:00
                "close_time": 1704070800000,  # 2024-01-01 01:00:00
                "open": 50000.0,
                "high": 50500.0,
                "low": 49800.0,
                "close": 50200.0,
                "volume": 100.0,
                "interval": "1h",
            },
            {
                "symbol": "BTCUSDT",
                "open_time": 1704070800000,
                "close_time": 1704074400000,
                "open": 50200.0,
                "high": 51000.0,
                "low": 50100.0,
                "close": 50800.0,
                "volume": 120.0,
                "interval": "1h",
            },
        ])
        
        engine = BacktestEngine(
            data_provider=mock_data_provider,
            config=BacktestConfig(symbols=["BTCUSDT"]),
        )
        
        strategy = create_mock_strategy()
        
        start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 1, 2, tzinfo=timezone.utc)
        
        report = await engine.run_backtest(
            strategy=strategy,
            start_time=start_time,
            end_time=end_time,
        )
        
        assert report.metrics.trade_count >= 0
        assert report.data_quality.status == DataQualityStatus.PASS
    
    def test_data_quality_validation(self):
        """测试数据质量验证"""
        engine = BacktestEngine()
        
        # 正常数据
        klines = {
            "BTCUSDT": [
                {
                    "symbol": "BTCUSDT",
                    "open_time": 1704067200000,
                    "close_time": 1704070800000,
                    "open": 50000.0,
                    "high": 50500.0,
                    "low": 49800.0,
                    "close": 50200.0,
                    "volume": 100.0,
                },
                {
                    "symbol": "BTCUSDT",
                    "open_time": 1704070800000,
                    "close_time": 1704074400000,
                    "open": 50200.0,
                    "high": 51000.0,
                    "low": 50100.0,
                    "close": 50800.0,
                    "volume": 120.0,
                },
            ]
        }
        
        start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 1, 2, tzinfo=timezone.utc)
        
        result = engine._validate_data_quality(klines, start_time, end_time)
        
        assert result.status in [DataQualityStatus.PASS, DataQualityStatus.WARNING]
        assert result.total_bars == 2
    
    def test_data_quality_with_invalid_ohlc(self):
        """测试无效OHLC数据检测"""
        engine = BacktestEngine()
        
        klines = {
            "BTCUSDT": [
                {
                    "symbol": "BTCUSDT",
                    "open_time": 1704067200000,
                    "close_time": 1704070800000,
                    "open": 50000.0,
                    "high": 49800.0,  # High < Low，无效
                    "low": 50500.0,
                    "close": 50200.0,
                    "volume": 100.0,
                },
            ]
        }
        
        start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        
        result = engine._validate_data_quality(klines, start_time, end_time)
        
        assert result.status == DataQualityStatus.FAIL
        assert any(i.issue_type == "INVALID_OHLC" for i in result.issues)
    
    def test_metrics_calculation(self):
        """测试指标计算"""
        engine = BacktestEngine()
        engine._equity_curve = [
            Decimal("10000"),
            Decimal("10200"),
            Decimal("10100"),
            Decimal("10300"),
            Decimal("10500"),
            Decimal("10400"),
            Decimal("10600"),
        ]
        
        engine._trades = [
            BacktestTrade(
                trade_id="1",
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                entry_price=Decimal("100"),
                exit_price=Decimal("105"),
                quantity=Decimal("10"),
                pnl=Decimal("50"),
                side="LONG",
                exit_reason="TAKE_PROFIT",
            ),
            BacktestTrade(
                trade_id="2",
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                entry_price=Decimal("105"),
                exit_price=Decimal("102"),
                quantity=Decimal("10"),
                pnl=Decimal("-30"),
                side="LONG",
                exit_reason="STOP_LOSS",
            ),
        ]
        
        start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2024, 1, 2, tzinfo=timezone.utc)
        
        metrics = engine._calculate_metrics(start_time, end_time)
        
        assert metrics.trade_count == 2
        assert metrics.win_rate == 0.5  # 1胜1负
        assert len(metrics.equity_curve) == 7
    
    @pytest.mark.asyncio
    async def test_backtest_performance(self):
        """测试回测性能 - 1年数据应在1分钟内完成"""
        import time
        
        engine = BacktestEngine()
        strategy = create_mock_strategy()
        
        # 生成1年数据
        start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        
        start = time.monotonic()
        report = await engine.run_backtest(
            strategy=strategy,
            start_time=start_time,
            end_time=end_time,
        )
        elapsed = time.monotonic() - start
        
        # 1年数据（每小时K线）应该很快完成
        assert elapsed < 60, f"Backtest took {elapsed:.2f}s, expected < 60s"
        assert report.metrics.trade_count >= 0


# ============================================================================
# LiveEvaluator Tests
# ============================================================================


class TestLiveEvaluator:
    """LiveEvaluator 单元测试"""
    
    @pytest.fixture
    def backtest_report(self):
        """创建基线回测报告"""
        now = datetime.now(timezone.utc)
        metrics = create_metrics(
            total_pnl=2000.0,
            sharpe_ratio=1.8,
            max_drawdown=400.0,
            win_rate=0.6,
            trade_count=50,
        )
        
        return BacktestReport(
            strategy_name="TestStrategy",
            start_time=now - timedelta(days=365),
            end_time=now,
            metrics=metrics,
            initial_capital=Decimal("10000"),
            final_capital=Decimal("12000"),
        )
    
    def test_evaluator_creation(self):
        """测试评估器创建"""
        evaluator = LiveEvaluator()
        assert evaluator._backtest_report is None
        assert evaluator._config is not None
    
    def test_evaluator_with_baseline(self, backtest_report):
        """测试带基线的评估器"""
        evaluator = LiveEvaluator(backtest_report=backtest_report)
        assert evaluator._backtest_report is not None
    
    @pytest.mark.asyncio
    async def test_evaluate_healthy(self, backtest_report):
        """测试正常评估"""
        evaluator = LiveEvaluator(backtest_report=backtest_report)
        strategy = create_mock_strategy()
        
        # 正常指标（与回测接近）
        metrics = create_metrics(
            total_pnl=2100.0,  # 略好于回测
            sharpe_ratio=1.7,  # 略低于回测但正常
            max_drawdown=380.0,  # 略好于回测
            win_rate=0.58,  # 接近回测
            trade_count=55,
        )
        
        result = await evaluator.evaluate(strategy, metrics)
        
        assert result.status == EvaluationStatus.HEALTHY
        assert result.strategy_name == "TestStrategy"
        assert len(result.alerts) == 0
    
    @pytest.mark.asyncio
    async def test_evaluate_with_alerts(self, backtest_report):
        """测试带告警的评估"""
        evaluator = LiveEvaluator(backtest_report=backtest_report)
        strategy = create_mock_strategy()
        
        # 差于回测的指标
        metrics = create_metrics(
            total_pnl=-500.0,  # 亏损
            sharpe_ratio=0.5,  # 显著下降
            max_drawdown=800.0,  # 显著超出回测
            win_rate=0.4,  # 显著下降
            trade_count=20,
        )
        
        result = await evaluator.evaluate(strategy, metrics)
        
        assert result.status in [EvaluationStatus.WARNING, EvaluationStatus.CRITICAL]
        assert len(result.alerts) > 0
        assert result.comparison_with_backtest is not None
    
    @pytest.mark.asyncio
    async def test_evaluate_low_trade_count(self):
        """测试交易次数过少的评估"""
        evaluator = LiveEvaluator()
        strategy = create_mock_strategy()
        
        metrics = create_metrics(
            total_pnl=100.0,
            sharpe_ratio=2.0,
            trade_count=3,  # 少于最小要求
        )
        
        result = await evaluator.evaluate(strategy, metrics)
        
        assert "交易次数过少" in result.recommendations[0]
    
    def test_compare_with_backtest(self, backtest_report):
        """测试与回测对比"""
        evaluator = LiveEvaluator(backtest_report=backtest_report)
        
        live_metrics = create_metrics(
            total_pnl=1500.0,
            sharpe_ratio=1.5,
            max_drawdown=600.0,
            win_rate=0.5,
            trade_count=40,
        )
        
        comparison = evaluator._compare_with_backtest(
            live_metrics, backtest_report.metrics
        )
        
        assert "pnl_diff" in comparison
        assert "sharpe_ratio_diff" in comparison
        assert "drawdown_diff" in comparison
        assert comparison["pnl_diff"] == -500.0  # 1500 - 2000
    
    def test_detect_consecutive_losses(self):
        """测试连续亏损检测"""
        evaluator = LiveEvaluator()
        
        # 需要先添加一些历史数据，这样才能触发连续亏损检测
        # _detect_anomalies 中检查 len(self._metrics_history) >= 3
        history_metrics = [
            StrategyMetrics(
                trade_count=10,
                equity_curve=[
                    Decimal("10000"),
                    Decimal("9900"),
                    Decimal("9800"),
                    Decimal("9700"),
                    Decimal("9600"),
                    Decimal("9500"),
                    Decimal("9400"),
                    Decimal("9300"),
                    Decimal("9200"),
                    Decimal("9100"),
                ],
            )
            for _ in range(3)
        ]
        evaluator._metrics_history = history_metrics
        
        # 使用持续下跌的权益曲线，不回升才能保持连续亏损
        # 10个数据点，前9个连续下跌应该能检测到
        metrics = StrategyMetrics(
            trade_count=20,
            equity_curve=[
                Decimal("10000"),
                Decimal("9900"),  # loss 1
                Decimal("9800"),  # loss 2
                Decimal("9700"),  # loss 3
                Decimal("9600"),  # loss 4
                Decimal("9500"),  # loss 5
                Decimal("9400"),  # loss 6
                Decimal("9300"),  # loss 7
                Decimal("9200"),  # loss 8 - 应该触发告警（>=7）
                Decimal("9100"),  # loss 9
            ],
        )
        
        result = EvaluationResult(
            strategy_name="TestStrategy",
            metrics=metrics,
        )
        
        evaluator._detect_anomalies(result, metrics)
        
        # 应该有连续亏损告警
        loss_alerts = [a for a in result.alerts if a.alert_type == "CONSECUTIVE_LOSSES"]
        assert len(loss_alerts) > 0
    
    def test_generate_recommendations(self):
        """测试建议生成"""
        evaluator = LiveEvaluator()
        
        metrics = StrategyMetrics(
            total_pnl=Decimal("-500"),
            win_rate=0.35,  # 偏低
            profit_factor=1.0,  # 偏低
            sharpe_ratio=0.3,  # 偏低
            trade_count=20,
        )
        
        result = EvaluationResult(
            strategy_name="TestStrategy",
            metrics=metrics,
        )
        
        evaluator._generate_recommendations(result, metrics)
        
        assert len(result.recommendations) > 0


# ============================================================================
# 辅助函数 Tests
# ============================================================================


class TestCalculateSharpeRatio:
    """calculate_sharpe_ratio 单元测试"""
    
    def test_empty_returns(self):
        """测试空收益率列表"""
        sharpe, vol = calculate_sharpe_ratio([])
        assert sharpe == 0.0
        assert vol == 0.0
    
    def test_zero_volatility(self):
        """测试零波动率"""
        sharpe, vol = calculate_sharpe_ratio([0.01, 0.01, 0.01])
        assert vol == 0.0
        # 零波动率时夏普率为无穷大，但实现返回0.0
        assert sharpe == 0.0
    
    def test_positive_sharpe(self):
        """测试正夏普率"""
        returns = [0.001, 0.002, 0.0015, 0.001, 0.0025]
        sharpe, vol = calculate_sharpe_ratio(returns, risk_free_rate=0.02)
        
        assert sharpe > 0
        assert vol >= 0
    
    def test_negative_returns(self):
        """测试负收益"""
        returns = [-0.001, -0.002, -0.001, -0.0015, -0.001]
        sharpe, vol = calculate_sharpe_ratio(returns, risk_free_rate=0.02)
        
        assert sharpe < 0


class TestCalculateMaxDrawdown:
    """calculate_max_drawdown 单元测试"""
    
    def test_empty_curve(self):
        """测试空权益曲线"""
        max_dd, peak, trough = calculate_max_drawdown([])
        assert max_dd == Decimal("0")
        assert peak == -1
        assert trough == -1
    
    def test_no_drawdown(self):
        """测试无回撤（单边上涨）"""
        curve = [
            Decimal("10000"),
            Decimal("10100"),
            Decimal("10200"),
            Decimal("10300"),
        ]
        
        max_dd, peak, trough = calculate_max_drawdown(curve)
        
        assert max_dd == Decimal("0")
        # peak是最后一个新高位置
        assert peak == 3  # 最后一个
        # trough应该是peak的位置（因为没有回撤）
        assert trough == 3
    
    def test_with_drawdown(self):
        """测试有回撤的情况"""
        curve = [
            Decimal("10000"),
            Decimal("10200"),
            Decimal("10100"),  # 回撤100
            Decimal("9800"),   # 再回撤200，总回撤400
            Decimal("9900"),
            Decimal("10300"),
        ]
        
        max_dd, peak, trough = calculate_max_drawdown(curve)
        
        # 从峰值 10200 回撤到 9800，回撤 400
        # 回撤 = peak(10200) - trough(9800) = 400
        assert max_dd == Decimal("400")
        assert peak == 1  # 10200 的位置
        assert trough == 3  # 9800 的位置


# ============================================================================
# Integration Tests
# ============================================================================


class TestBacktestLiveEvaluatorIntegration:
    """回测与实时评估集成测试"""
    
    @pytest.mark.asyncio
    async def test_full_backtest_then_evaluate(self):
        """测试完整流程：回测 -> 实时评估"""
        # 1. 运行回测
        engine = BacktestEngine()
        strategy = create_mock_strategy()
        
        start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2024, 6, 1, tzinfo=timezone.utc)  # 半年数据
        
        backtest_report = await engine.run_backtest(
            strategy=strategy,
            start_time=start_time,
            end_time=end_time,
        )
        
        # 2. 使用回测结果创建评估器
        evaluator = LiveEvaluator(backtest_report=backtest_report)
        
        # 3. 模拟实时指标（假设与回测类似）
        live_metrics = StrategyMetrics(
            total_pnl=backtest_report.metrics.total_pnl * Decimal("0.5"),  # 50%的收益
            sharpe_ratio=backtest_report.metrics.sharpe_ratio * 0.8,
            max_drawdown=backtest_report.metrics.max_drawdown * Decimal("0.8"),
            win_rate=backtest_report.metrics.win_rate * 0.95,
            trade_count=int(backtest_report.metrics.trade_count * 0.5),
            equity_curve=backtest_report.metrics.equity_curve.copy(),
        )
        
        # 4. 评估
        result = await evaluator.evaluate(strategy, live_metrics)
        
        # 应该生成对比报告
        assert result.comparison_with_backtest is not None
        assert "pnl_diff_percent" in result.comparison_with_backtest


# ============================================================================
# 运行测试
# ============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
