"""
StrategyRunner 单元测试
=======================

测试范围：
1. 策略加载/卸载
2. 生命周期控制（启动/停止/暂停/恢复）
3. Tick驱动和信号生成
4. 异常隔离
5. 回调通知
6. StrategyResourceLimits 集成（Phase 4 Task 4.1）
7. KillSwitch 对接（Phase 4 Task 4.1）
8. OMS 回调（Phase 4 Task 4.1）

注意：项目配置了 asyncio_mode = "auto"，不需要 @pytest.mark.asyncio 装饰器
"""
import asyncio
import pytest
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, Mock, patch

from trader.core.application.risk_engine import KillSwitchLevel
from trader.core.application.strategy_protocol import StrategyResourceLimits
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.strategy_runner import (
    StrategyRunner,
    StrategyRuntimeInfo,
    StrategyStatus,
)
from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationResult,
    ValidationError,
)
from trader.core.application.risk_engine import RiskLevel

# 全局超时设置（秒）
TEST_TIMEOUT = 10


# ============================================================================
# Mock 策略插件
# ============================================================================


class MockStrategyPlugin:
    """Mock 策略插件"""

    def __init__(self):
        self.strategy_id = ""
        self.name = "MockStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.initialized = False
        self.shutdown_called = False
        self.tick_count = 0
        self.fill_count = 0
        self.cancel_count = 0
        self.return_signal = True
        self.raise_on_tick = False

    async def initialize(self, config: Dict[str, Any]) -> None:
        self.initialized = True
        self.config = config

    async def on_market_data(self, market_data: MarketData) -> Optional[Signal]:
        self.tick_count += 1
        if self.raise_on_tick:
            raise RuntimeError("Mock tick error")
        if self.return_signal:
            return Signal(
                strategy_name=self.strategy_id or self.name,
                signal_type=SignalType.BUY,
                symbol=market_data.symbol,
                price=market_data.price,
                quantity=0.1,
                confidence=0.8,
                reason="Mock signal",
            )
        return None

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    async def on_fill(
        self, order_id: str, symbol: str, side: str, quantity: float, price: float
    ) -> None:
        self.fill_count += 1

    async def on_cancel(self, order_id: str, reason: str) -> None:
        self.cancel_count += 1

    async def shutdown(self) -> None:
        self.shutdown_called = True

    async def update_config(self, config: Dict[str, Any]) -> ValidationResult:
        """实现 update_config 协议方法"""
        self.config = {**self.config, **config} if hasattr(self, 'config') else config
        return ValidationResult.valid()


def _create_mock_module(plugin: MockStrategyPlugin):
    """创建 mock 模块"""
    mock_module = Mock()
    mock_module.get_plugin = Mock(return_value=plugin)
    mock_module.create_plugin = Mock(return_value=plugin)
    mock_module.build_plugin = Mock(return_value=plugin)
    return mock_module


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_plugin():
    """创建 Mock 策略插件"""
    return MockStrategyPlugin()


@pytest.fixture
def runner():
    """创建 StrategyRunner 实例"""
    return StrategyRunner()


@pytest.fixture
def mock_signal_callback():
    """创建 Mock 信号回调"""
    return AsyncMock()


@pytest.fixture
def runner_with_callback(mock_signal_callback):
    """创建带信号回调的 StrategyRunner 实例"""
    return StrategyRunner(signal_callback=mock_signal_callback)


@pytest.fixture
def sample_market_data():
    """创建示例市场数据"""
    return MarketData(
        symbol="BTCUSDT",
        data_type=MarketDataType.TICKER,
        timestamp=datetime.now(timezone.utc),
        price=Decimal("50000.0"),
        bid=Decimal("49999.0"),
        ask=Decimal("50001.0"),
        volume=Decimal("1000.0"),
    )


# ============================================================================
# 测试：策略加载/卸载
# ============================================================================


class TestStrategyLoadUnload:
    """测试策略加载和卸载"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_load_strategy_success(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试成功加载策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            info = await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                config={"param1": "value1"},
            )

        assert info.strategy_id == "test_strategy"
        assert info.version == "v1"
        assert info.status == StrategyStatus.LOADED
        assert info.loaded_at is not None
        assert mock_plugin.initialized is True

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_load_strategy_already_loaded(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试重复加载策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            with pytest.raises(ValueError, match="已加载"):
                await runner.load_strategy(
                    strategy_id="test_strategy",
                    version="v2",
                    module_path="strategies.test",
                )

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_unload_strategy_success(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试成功卸载策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            await runner.unload_strategy("test_strategy")

        assert runner.get_status("test_strategy") is None
        assert mock_plugin.shutdown_called is True

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_unload_strategy_not_loaded(self, runner: StrategyRunner):
        """测试卸载未加载的策略"""
        with pytest.raises(ValueError, match="策略未加载"):
            await runner.unload_strategy("nonexistent")

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_unload_running_strategy_fails(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试卸载运行中的策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            with pytest.raises(ValueError, match="策略正在运行"):
                await runner.unload_strategy("test_strategy")


# ============================================================================
# 测试：生命周期控制
# ============================================================================


class TestLifecycleControl:
    """测试策略生命周期控制"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_start_strategy(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试启动策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            info = await runner.start("test_strategy")

        assert info.status == StrategyStatus.RUNNING
        assert info.started_at is not None

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_start_already_running(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试重复启动策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            with pytest.raises(ValueError, match="策略已在运行"):
                await runner.start("test_strategy")

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_stop_strategy(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试停止策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            info = await runner.stop("test_strategy")

        assert info.status == StrategyStatus.STOPPED

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_pause_strategy(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试暂停策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            info = await runner.pause("test_strategy")

        assert info.status == StrategyStatus.PAUSED

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_resume_strategy(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试恢复策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")
            await runner.pause("test_strategy")

            info = await runner.resume("test_strategy")

        assert info.status == StrategyStatus.RUNNING

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_pause_not_running_fails(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试暂停未运行的策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            with pytest.raises(ValueError, match="策略未在运行"):
                await runner.pause("test_strategy")

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_resume_not_paused_fails(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试恢复未暂停的策略"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            with pytest.raises(ValueError, match="策略未暂停"):
                await runner.resume("test_strategy")


# ============================================================================
# 测试：Tick驱动
# ============================================================================


class TestTickDriving:
    """测试 Tick 驱动和信号生成"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_tick_generates_signal(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin, sample_market_data: MarketData
    ):
        """测试 Tick 生成信号"""
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            signal = await runner.tick("test_strategy", sample_market_data)

        assert signal is not None
        assert signal.signal_type == SignalType.BUY
        assert signal.symbol == "BTCUSDT"
        assert mock_plugin.tick_count == 1

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_tick_no_signal(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin, sample_market_data: MarketData
    ):
        """测试 Tick 不生成信号"""
        mock_plugin.return_signal = False
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            signal = await runner.tick("test_strategy", sample_market_data)

        assert signal is None
        assert mock_plugin.tick_count == 1

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_tick_paused_returns_none(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin, sample_market_data: MarketData
    ):
        """测试暂停状态 Tick 返回 None"""
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")
            await runner.pause("test_strategy")

            signal = await runner.tick("test_strategy", sample_market_data)

        assert signal is None
        assert mock_plugin.tick_count == 0  # 暂停状态不调用策略

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_tick_unloaded_strategy_fails(
        self, runner: StrategyRunner, sample_market_data: MarketData
    ):
        """测试未加载策略的 Tick"""
        with pytest.raises(ValueError, match="策略未加载"):
            await runner.tick("nonexistent", sample_market_data)

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_tick_not_running_fails(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin, sample_market_data: MarketData
    ):
        """测试未运行策略的 Tick"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            with pytest.raises(ValueError, match="策略未运行"):
                await runner.tick("test_strategy", sample_market_data)


# ============================================================================
# 测试：异常隔离
# ============================================================================


class TestErrorIsolation:
    """测试异常隔离"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_tick_error_isolated(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin, sample_market_data: MarketData
    ):
        """测试 Tick 异常被隔离"""
        mock_plugin.raise_on_tick = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            # Tick 异常不应导致崩溃
            signal = await runner.tick("test_strategy", sample_market_data)

        assert signal is None
        info = runner.get_status("test_strategy")
        assert info.error_count == 1
        assert info.last_error is not None

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_multiple_errors_mark_as_error(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin, sample_market_data: MarketData
    ):
        """测试多次错误后标记为 ERROR 状态"""
        mock_plugin.raise_on_tick = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner.start("test_strategy")

            # 触发 10 次错误
            for _ in range(10):
                await runner.tick("test_strategy", sample_market_data)

        info = runner.get_status("test_strategy")
        assert info.status == StrategyStatus.ERROR
        assert info.error_count >= 10


# ============================================================================
# 测试：回调通知
# ============================================================================


class TestCallbacks:
    """测试回调通知"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_signal_callback(
        self,
        runner_with_callback: StrategyRunner,
        mock_signal_callback,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
    ):
        """测试信号回调"""
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_callback.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner_with_callback.start("test_strategy")

            signal = await runner_with_callback.tick("test_strategy", sample_market_data)

        mock_signal_callback.assert_called_once()
        call_args = mock_signal_callback.call_args[0]
        assert call_args[0] == "test_strategy"
        assert call_args[1].signal_type == SignalType.BUY

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_notify_fill(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试成交通知"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            await runner.notify_fill(
                strategy_id="test_strategy",
                order_id="order-001",
                symbol="BTCUSDT",
                side="BUY",
                quantity=0.1,
                price=50000.0,
            )

        assert mock_plugin.fill_count == 1

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_notify_cancel(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试取消通知"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            await runner.notify_cancel(
                strategy_id="test_strategy",
                order_id="order-001",
                reason="User cancelled",
            )

        assert mock_plugin.cancel_count == 1


# ============================================================================
# 测试：查询功能
# ============================================================================


class TestQueryFunctions:
    """测试查询功能"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_get_status(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试获取状态"""
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )

            info = runner.get_status("test_strategy")

        assert info is not None
        assert info.strategy_id == "test_strategy"
        assert info.status == StrategyStatus.LOADED

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_get_status_not_loaded(self, runner: StrategyRunner):
        """测试获取未加载策略的状态"""
        info = runner.get_status("nonexistent")
        assert info is None

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_list_strategies(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试列出所有策略"""
        mock_module1 = _create_mock_module(mock_plugin)

        mock_plugin2 = MockStrategyPlugin()
        mock_module2 = _create_mock_module(mock_plugin2)

        with patch("importlib.import_module", side_effect=[mock_module1, mock_module2]):
            await runner.load_strategy(
                strategy_id="strategy1",
                version="v1",
                module_path="strategies.test1",
            )

            await runner.load_strategy(
                strategy_id="strategy2",
                version="v1",
                module_path="strategies.test2",
            )

        strategies = runner.list_strategies()

        assert len(strategies) == 2
        strategy_ids = [s.strategy_id for s in strategies]
        assert "strategy1" in strategy_ids
        assert "strategy2" in strategy_ids


# ============================================================================
# 测试：关闭
# ============================================================================


class TestShutdown:
    """测试关闭功能"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_shutdown(self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin):
        """测试关闭所有策略"""
        mock_module1 = _create_mock_module(mock_plugin)

        mock_plugin2 = MockStrategyPlugin()
        mock_module2 = _create_mock_module(mock_plugin2)

        with patch("importlib.import_module", side_effect=[mock_module1, mock_module2]):
            await runner.load_strategy(
                strategy_id="strategy1",
                version="v1",
                module_path="strategies.test1",
            )
            await runner.start("strategy1")

            await runner.load_strategy(
                strategy_id="strategy2",
                version="v1",
                module_path="strategies.test2",
            )

            await runner.shutdown()

        assert runner.get_status("strategy1") is None
        assert runner.get_status("strategy2") is None


# ============================================================================
# 测试：StrategyResourceLimits 集成（Phase 4 Task 4.1）
# ============================================================================


class TestStrategyResourceLimits:
    """测试 StrategyResourceLimits 资源限制"""

    @pytest.fixture
    def mock_oms_callback(self):
        """创建 Mock OMS 回调"""
        return AsyncMock()

    @pytest.fixture
    def runner_with_oms(self, mock_oms_callback):
        """创建带 OMS 回调的 StrategyRunner"""
        return StrategyRunner(oms_callback=mock_oms_callback)

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_load_strategy_with_resource_limits(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin
    ):
        """测试加载策略时设置资源限制"""
        mock_module = _create_mock_module(mock_plugin)
        limits = StrategyResourceLimits(
            max_position_size=Decimal("2.0"),
            max_daily_loss=Decimal("200.0"),
            max_orders_per_minute=5,
            timeout_seconds=3.0,
        )

        with patch("importlib.import_module", return_value=mock_module):
            info = await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                resource_limits=limits,
            )

        assert info.resource_limits is not None
        assert info.resource_limits.max_orders_per_minute == 5
        assert info.resource_limits.timeout_seconds == 3.0

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_order_rate_limit_exceeded(
        self,
        runner_with_oms: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_oms_callback,
    ):
        """测试订单频率限制超出时被阻止"""
        limits = StrategyResourceLimits(
            max_orders_per_minute=2,
            timeout_seconds=5.0,
        )
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_oms.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                resource_limits=limits,
            )
            await runner_with_oms.start("test_strategy")

            # 前2次调用应该成功
            for i in range(2):
                signal = await runner_with_oms.tick("test_strategy", sample_market_data)
                assert signal is not None, f"第{i+1}次调用应该成功"

            # 第3次应该被阻止
            signal = await runner_with_oms.tick("test_strategy", sample_market_data)
            assert signal is None  # 被阻止

            info = runner_with_oms.get_status("test_strategy")
            assert info.blocked_reason == "Order rate limit exceeded"

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_oms_callback_executed(
        self,
        runner_with_oms: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_oms_callback,
    ):
        """测试 OMS 回调被执行"""
        limits = StrategyResourceLimits(
            max_orders_per_minute=10,
            timeout_seconds=5.0,
        )
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_oms.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                resource_limits=limits,
            )
            await runner_with_oms.start("test_strategy")

            signal = await runner_with_oms.tick("test_strategy", sample_market_data)

        assert signal is not None
        mock_oms_callback.assert_called_once()

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_oms_callback_exception_isolated(
        self,
        runner_with_oms: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_oms_callback,
    ):
        """测试 OMS 回调异常被隔离"""
        limits = StrategyResourceLimits(
            max_orders_per_minute=10,
            timeout_seconds=5.0,
        )
        mock_plugin.return_signal = True
        mock_oms_callback.side_effect = RuntimeError("OMS error")
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_oms.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                resource_limits=limits,
            )
            await runner_with_oms.start("test_strategy")

            # OMS 回调异常不应导致崩溃
            signal = await runner_with_oms.tick("test_strategy", sample_market_data)

        assert signal is None  # OMS 失败后信号被丢弃
        info = runner_with_oms.get_status("test_strategy")
        assert info.error_count == 0  # 策略本身没有错误


# ============================================================================
# 测试：KillSwitch 对接（Phase 4 Task 4.1）
# ============================================================================


class TestKillSwitchIntegration:
    """测试 KillSwitch 对接"""

    @pytest.fixture
    def mock_killswitch_callback(self):
        """创建 Mock KillSwitch 回调"""
        callback = Mock()
        callback.return_value = KillSwitchLevel.L0_NORMAL
        return callback

    @pytest.fixture
    def runner_with_killswitch(self, mock_killswitch_callback):
        """创建带 KillSwitch 回调的 StrategyRunner"""
        return StrategyRunner(killswitch_callback=mock_killswitch_callback)

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_killswitch_l1_blocks_new_orders(
        self,
        runner_with_killswitch: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_killswitch_callback,
    ):
        """测试 KillSwitch L1 阻止新订单"""
        mock_killswitch_callback.return_value = KillSwitchLevel.L1_NO_NEW_POSITIONS
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_killswitch.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner_with_killswitch.start("test_strategy")

            signal = await runner_with_killswitch.tick("test_strategy", sample_market_data)

        assert signal is None
        info = runner_with_killswitch.get_status("test_strategy")
        assert "KillSwitch L1" in info.blocked_reason

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_killswitch_l2_stops_strategy(
        self,
        runner_with_killswitch: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_killswitch_callback,
    ):
        """测试 KillSwitch L2 停止策略"""
        mock_killswitch_callback.return_value = KillSwitchLevel.L2_CANCEL_ALL_AND_HALT
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_killswitch.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner_with_killswitch.start("test_strategy")

            signal = await runner_with_killswitch.tick("test_strategy", sample_market_data)

        assert signal is None
        info = runner_with_killswitch.get_status("test_strategy")
        assert info.status == StrategyStatus.STOPPED
        assert "KillSwitch L2" in info.blocked_reason

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_killswitch_l3_stops_strategy(
        self,
        runner_with_killswitch: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_killswitch_callback,
    ):
        """测试 KillSwitch L3 停止策略"""
        mock_killswitch_callback.return_value = KillSwitchLevel.L3_LIQUIDATE_AND_DISCONNECT
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_killswitch.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner_with_killswitch.start("test_strategy")

            signal = await runner_with_killswitch.tick("test_strategy", sample_market_data)

        assert signal is None
        info = runner_with_killswitch.get_status("test_strategy")
        assert info.status == StrategyStatus.STOPPED

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_killswitch_normal_allows_trading(
        self,
        runner_with_killswitch: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_killswitch_callback,
    ):
        """测试 KillSwitch L0 允许交易"""
        mock_killswitch_callback.return_value = KillSwitchLevel.L0_NORMAL
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_killswitch.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner_with_killswitch.start("test_strategy")

            signal = await runner_with_killswitch.tick("test_strategy", sample_market_data)

        assert signal is not None
        info = runner_with_killswitch.get_status("test_strategy")
        assert info.blocked_reason is None

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_killswitch_callback_exception_is_handled(
        self,
        runner_with_killswitch: StrategyRunner,
        mock_plugin: MockStrategyPlugin,
        sample_market_data: MarketData,
        mock_killswitch_callback,
    ):
        """测试 KillSwitch 回调异常被处理"""
        mock_killswitch_callback.side_effect = RuntimeError("KillSwitch error")
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner_with_killswitch.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
            )
            await runner_with_killswitch.start("test_strategy")

            # KillSwitch 检查失败不应导致崩溃
            signal = await runner_with_killswitch.tick("test_strategy", sample_market_data)

        # 应该继续处理信号（因为 KillSwitch 检查失败）
        assert signal is not None


# ============================================================================
# 测试：超时控制（Phase 4 Task 4.1）
# ============================================================================


class TestTimeoutControl:
    """测试超时控制"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_tick_timeout_returns_none(
        self, runner: StrategyRunner, mock_plugin: MockStrategyPlugin, sample_market_data: MarketData
    ):
        """测试策略执行超时返回 None"""
        limits = StrategyResourceLimits(timeout_seconds=0.1)
        mock_plugin.return_signal = True
        mock_module = _create_mock_module(mock_plugin)

        # 模拟慢速策略
        async def slow_on_market_data(md):
            await asyncio.sleep(1)  # 睡眠1秒，超过0.1秒超时
            return Signal(
                strategy_name="test",
                signal_type=SignalType.BUY,
                symbol=md.symbol,
                price=md.price,
                quantity=0.1,
                confidence=0.8,
                reason="slow signal",
            )

        mock_plugin.on_market_data = slow_on_market_data
        mock_module = _create_mock_module(mock_plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                resource_limits=limits,
            )
            await runner.start("test_strategy")

            signal = await runner.tick("test_strategy", sample_market_data)

        assert signal is None
        info = runner.get_status("test_strategy")
        assert info.error_count == 1
        assert "超时" in info.last_error


# ============================================================================
# 测试：参数动态调整（Phase 4 Task 4.7）
# ============================================================================


class MockStrategyPluginWithUpdateConfig(MockStrategyPlugin):
    """支持 update_config 的 Mock 策略插件"""

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self.update_config_calls = []

    async def update_config(self, config: Dict[str, Any]) -> ValidationResult:
        """支持 update_config 方法"""
        self.update_config_calls.append(config)
        self._config = {**self._config, **config}
        
        # 验证参数
        if "invalid_param" in config:
            return ValidationResult.invalid([
                ValidationError(
                    field="invalid_param",
                    message="Invalid parameter value",
                    code="INVALID_PARAM"
                )
            ])
        return ValidationResult.valid()

    async def initialize(self, config: Dict[str, Any]) -> None:
        self.initialized = True
        self._config = config
        self.config = config


class TestStrategyConfigUpdate:
    """测试策略参数动态调整"""

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_update_config_success(self, runner: StrategyRunner):
        """测试成功更新配置"""
        plugin = MockStrategyPluginWithUpdateConfig()
        mock_module = _create_mock_module(plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                config={"fast_period": 12, "slow_period": 26},
            )

            # 更新配置
            new_config = {"fast_period": 15, "max_position_size": 0.5}
            info = await runner.update_strategy_config("test_strategy", new_config)

            # 验证配置已更新
            assert info.config["fast_period"] == 15
            assert info.config["slow_period"] == 26  # 旧配置保留
            assert info.config["max_position_size"] == 0.5

            # 验证插件的 update_config 被调用
            assert len(plugin.update_config_calls) == 1
            assert plugin.update_config_calls[0]["fast_period"] == 15

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_update_config_validation_failure(self, runner: StrategyRunner):
        """测试配置验证失败"""
        plugin = MockStrategyPluginWithUpdateConfig()
        mock_module = _create_mock_module(plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                config={"fast_period": 12},
            )

            # 尝试更新为无效配置
            new_config = {"invalid_param": True}
            
            with pytest.raises(ValueError) as exc_info:
                await runner.update_strategy_config("test_strategy", new_config)
            
            assert "参数验证失败" in str(exc_info.value)

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_update_config_not_loaded(self, runner: StrategyRunner):
        """测试更新未加载的策略"""
        with pytest.raises(ValueError) as exc_info:
            await runner.update_strategy_config("nonexistent", {"fast_period": 15})
        
        assert "策略未加载" in str(exc_info.value)

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_update_config_without_update_method_uses_initialize(self, runner: StrategyRunner):
        """测试插件不支持 update_config 时使用 initialize"""
        plugin = MockStrategyPlugin()  # 使用不支持 update_config 的插件
        mock_module = _create_mock_module(plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                config={"fast_period": 12},
            )

            # 更新配置
            new_config = {"fast_period": 15}
            info = await runner.update_strategy_config("test_strategy", new_config)

            # 验证配置已更新（通过 initialize 后备）
            assert info.config["fast_period"] == 15

    @pytest.mark.timeout(TEST_TIMEOUT)
    async def test_update_config_partial_update(self, runner: StrategyRunner):
        """测试部分更新配置"""
        plugin = MockStrategyPluginWithUpdateConfig()
        mock_module = _create_mock_module(plugin)

        with patch("importlib.import_module", return_value=mock_module):
            await runner.load_strategy(
                strategy_id="test_strategy",
                version="v1",
                module_path="strategies.test",
                config={"fast_period": 12, "slow_period": 26, "rsi_period": 14},
            )

            # 只更新 fast_period
            new_config = {"fast_period": 10}
            info = await runner.update_strategy_config("test_strategy", new_config)

            # 验证部分更新：其他配置保留
            assert info.config["fast_period"] == 10
            assert info.config["slow_period"] == 26
            assert info.config["rsi_period"] == 14
