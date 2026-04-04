"""
ShadowModeVerifier 单元测试
=========================
测试影子模式验证框架的功能。
"""
import pytest
from datetime import datetime, timedelta, timezone

from trader.services.shadow_mode_verifier import (
    ShadowModeVerifier,
    ShadowDeviationReport,
    SignalRecord,
    create_signal_record,
    compare_backtest_to_shadow,
)


# ==================== Fixtures ====================

@pytest.fixture
def verifier() -> ShadowModeVerifier:
    """创建影子模式验证器"""
    return ShadowModeVerifier(
        signal_diff_threshold=0.2,
        sizing_diff_threshold=0.3,
    )


@pytest.fixture
def sample_signals() -> list[SignalRecord]:
    """创建示例信号记录"""
    now = datetime.now(timezone.utc)
    signals = []
    
    # 回测信号
    for i in range(10):
        signals.append(create_signal_record(
            signal_id=f"bt-{i}",
            timestamp=now - timedelta(hours=i),
            signal_type="BUY",
            symbol="BTCUSDT",
            sizing=1.0,
            source="backtest",
            price=50000.0 + i * 10,
        ))
    
    # 影子信号（80% 触发率）
    for i in range(8):
        signals.append(create_signal_record(
            signal_id=f"sh-{i}",
            timestamp=now - timedelta(hours=i),
            signal_type="BUY",
            symbol="BTCUSDT",
            sizing=0.95,  # sizing 略小
            source="shadow",
            price=50000.0 + i * 10 + 5,  # 价格略高（滑点）
        ))
    
    # 实际成交（影子信号的 60%）
    for i in range(5):
        signals.append(create_signal_record(
            signal_id=f"sh-{i}",
            timestamp=now - timedelta(hours=i),
            signal_type="BUY",
            symbol="BTCUSDT",
            sizing=0.9,
            source="execution",
            price=50000.0 + i * 10 + 8,  # 成交价更高
        ))
    
    return signals


# ==================== 信号记录测试 ====================

class TestSignalRecord:
    """SignalRecord 测试"""
    
    def test_create_backtest_signal(self):
        """创建回测信号"""
        record = create_signal_record(
            signal_id="sig-001",
            timestamp=datetime.now(timezone.utc),
            signal_type="BUY",
            symbol="BTCUSDT",
            sizing=1.0,
            source="backtest",
            price=50000.0,
        )
        
        assert record.backtest_signal is True
        assert record.shadow_signal is False
        assert record.executed_signal is False
        assert record.backtest_price == 50000.0
    
    def test_create_shadow_signal(self):
        """创建影子信号"""
        record = create_signal_record(
            signal_id="sig-002",
            timestamp=datetime.now(timezone.utc),
            signal_type="SELL",
            symbol="ETHUSDT",
            sizing=2.0,
            source="shadow",
            price=3000.0,
        )
        
        assert record.backtest_signal is False
        assert record.shadow_signal is True
        assert record.executed_signal is False
        assert record.shadow_price == 3000.0
    
    def test_create_execution_signal(self):
        """创建实际成交信号"""
        record = create_signal_record(
            signal_id="sig-003",
            timestamp=datetime.now(timezone.utc),
            signal_type="BUY",
            symbol="BTCUSDT",
            sizing=1.0,
            source="execution",
            price=50050.0,
        )
        
        assert record.executed_signal is True
        assert record.execution_price == 50050.0


# ==================== 验证器测试 ====================

class TestVerifier:
    """ShadowModeVerifier 测试"""
    
    @pytest.mark.asyncio
    async def test_verify_with_signals(
        self,
        verifier: ShadowModeVerifier,
        sample_signals: list[SignalRecord],
    ):
        """使用提供的信号验证"""
        report = await verifier.verify(
            strategy_id="strategy_A",
            lookback_period=timedelta(days=1),
            signals=sample_signals,
        )
        
        assert report.strategy_id == "strategy_A"
        assert report.total_signals == len(sample_signals)
        assert report.backtest_signals == 10
        assert report.shadow_signals == 8
        assert report.execution_signals == 5
    
    @pytest.mark.asyncio
    async def test_verify_empty_signals(self, verifier: ShadowModeVerifier):
        """空信号列表"""
        report = await verifier.verify(
            strategy_id="strategy_A",
            lookback_period=timedelta(days=1),
            signals=[],
        )
        
        assert report.total_signals == 0
        assert report.overall_healthy is True  # 无数据视为健康
    
    @pytest.mark.asyncio
    async def test_signal_trigger_rate_diff(
        self,
        verifier: ShadowModeVerifier,
    ):
        """信号触发率偏差计算"""
        now = datetime.now(timezone.utc)
        signals = []
        
        # 10 个回测信号
        for i in range(10):
            signals.append(create_signal_record(
                signal_id=f"bt-{i}",
                timestamp=now,
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=1.0,
                source="backtest",
            ))
        
        # 8 个影子信号（偏差 20%）
        for i in range(8):
            signals.append(create_signal_record(
                signal_id=f"sh-{i}",
                timestamp=now,
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=1.0,
                source="shadow",
            ))
        
        report = await verifier.verify(
            strategy_id="strategy_A",
            lookback_period=timedelta(days=1),
            signals=signals,
        )
        
        # backtest_rate = 10/18, shadow_rate = 8/18
        # diff ≈ 2/18 ≈ 0.11
        assert report.signal_trigger_rate_diff < 0.2  # 低于阈值
    
    @pytest.mark.asyncio
    async def test_sizing_diff_within_threshold(
        self,
        verifier: ShadowModeVerifier,
    ):
        """Sizing 偏差在阈值内"""
        now = datetime.now(timezone.utc)
        signals = []
        
        # 回测和影子 sizing 基本一致
        for i in range(5):
            signals.append(create_signal_record(
                signal_id=f"bt-{i}",
                timestamp=now - timedelta(hours=i),
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=1.0,
                source="backtest",
            ))
            signals.append(create_signal_record(
                signal_id=f"sh-{i}",
                timestamp=now - timedelta(hours=i),
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=1.0,
                source="shadow",
            ))
        
        report = await verifier.verify(
            strategy_id="strategy_A",
            lookback_period=timedelta(days=1),
            signals=signals,
        )
        
        assert report.sizing_avg_diff == 0.0
        assert report.overall_healthy is True
    
    @pytest.mark.asyncio
    async def test_sizing_diff_exceeds_threshold(self, verifier: ShadowModeVerifier):
        """Sizing 偏差超过阈值"""
        now = datetime.now(timezone.utc)
        signals = []
        
        for i in range(5):
            signals.append(create_signal_record(
                signal_id=f"bt-{i}",
                timestamp=now - timedelta(hours=i),
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=1.0,
                source="backtest",
            ))
            # 影子 sizing 只有一半
            signals.append(create_signal_record(
                signal_id=f"sh-{i}",
                timestamp=now - timedelta(hours=i),
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=0.5,
                source="shadow",
            ))
        
        report = await verifier.verify(
            strategy_id="strategy_A",
            lookback_period=timedelta(days=1),
            signals=signals,
        )
        
        assert report.sizing_avg_diff == 0.5  # 50% 偏差
        assert report.overall_healthy is False
        assert len(report.alerts) > 0


# ==================== 辅助函数测试 ====================

class TestHelperFunctions:
    """辅助函数测试"""
    
    def test_compare_backtest_to_shadow(self):
        """比较回测和影子信号"""
        now = datetime.now(timezone.utc)
        backtest = [
            create_signal_record(
                signal_id=f"bt-{i}",
                timestamp=now,
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=1.0,
                source="backtest",
            )
            for i in range(5)
        ]
        shadow = [
            create_signal_record(
                signal_id=f"sh-{i}",
                timestamp=now,
                signal_type="BUY",
                symbol="BTCUSDT",
                sizing=0.95,
                source="shadow",
            )
            for i in range(4)
        ]
        
        result = compare_backtest_to_shadow(backtest, shadow)
        
        assert result["backtest_count"] == 5
        assert result["shadow_count"] == 4
        assert result["healthy"] is True
    
    def test_compare_backtest_empty(self):
        """回测信号为空"""
        result = compare_backtest_to_shadow([], [])
        assert "error" in result


# ==================== 报告序列化测试 ====================

class TestSerialization:
    """报告序列化测试"""
    
    def test_report_to_dict(self):
        """报告转字典"""
        report = ShadowDeviationReport(
            strategy_id="strategy_A",
            lookback_period=timedelta(days=7),
            total_signals=100,
            backtest_signals=50,
            shadow_signals=40,
            execution_signals=30,
            signal_trigger_rate_diff=0.1,
            sizing_avg_diff=0.15,
            sizing_max_diff=0.25,
            execution_gap_avg=0.0005,
            execution_gap_max=0.001,
            risk_block_rate_diff=0.1,
            overall_healthy=True,
            alerts=["alert1"],
            recommendations=["rec1"],
        )
        
        d = report.to_dict()
        
        assert d["strategy_id"] == "strategy_A"
        assert d["total_signals"] == 100
        assert d["overall_healthy"] is True
        assert "timestamp" in d


# ==================== 阈值测试 ====================

class TestThresholds:
    """阈值测试"""
    
    def test_default_thresholds(self):
        """默认阈值"""
        verifier = ShadowModeVerifier()
        assert verifier._signal_threshold == 0.2
        assert verifier._sizing_threshold == 0.3
    
    def test_custom_thresholds(self):
        """自定义阈值"""
        verifier = ShadowModeVerifier(
            signal_diff_threshold=0.15,
            sizing_diff_threshold=0.25,
        )
        assert verifier._signal_threshold == 0.15
        assert verifier._sizing_threshold == 0.25


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
