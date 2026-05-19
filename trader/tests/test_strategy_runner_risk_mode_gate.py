"""
阶段2：StrategyRunner RiskMode Early Gate 测试

目标：验证 StrategyRunner.tick() 路径中的 RiskMode early gate 行为
RiskMode 动作矩阵：
- NO_NEW_POSITIONS: 阻止开仓信号(LONG/SHORT)，允许减仓信号(CLOSE_LONG/CLOSE_SHORT)
- CLOSE_ONLY: 阻止开仓信号，允许减仓信号
- CANCEL_ALL_AND_HALT: 阻止所有策略信号
- LIQUIDATE_AND_DISCONNECT: 阻止所有策略信号
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.risk_mode import RiskMode
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.strategy_runner import StrategyRunner

STRATEGY_CODE = """
from decimal import Decimal
from trader.core.application.strategy_protocol import MarketData, StrategyPlugin
from trader.core.domain.models.signal import Signal, SignalType

class TestStrategy(StrategyPlugin):
    def __init__(self, config: dict):
        self.strategy_name = config.get("strategy_id", "test")
        self.symbols = config.get("symbols", [])

    async def initialize(self, config: dict) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def on_market_data(self, market_data: MarketData) -> Signal:
        return Signal(
            signal_type=SignalType.LONG,
            symbol=market_data.symbol,
            quantity=Decimal("0.01"),
            price=market_data.close,
        )

def get_plugin(config: dict) -> StrategyPlugin:
    return TestStrategy(config)
"""


def _make_market_data(symbol: str = "BTCUSDT") -> MarketData:
    return MarketData(
        symbol=symbol,
        data_type=MarketDataType.KLINE,
        price=Decimal("50000"),
        volume=Decimal("100"),
        timestamp=datetime.now(timezone.utc),
        kline_open=Decimal("50000"),
        kline_high=Decimal("51000"),
        kline_low=Decimal("49000"),
        kline_close=Decimal("50000"),
        kline_interval="1m",
    )


class TestStrategyRunnerRiskModeEarlyGate:
    """StrategyRunner tick 路径 RiskMode Early Gate 测试"""

    @pytest.mark.asyncio
    async def test_no_new_positions_blocks_open_signal(self) -> None:
        """NO_NEW_POSITIONS + 开仓信号 -> StrategyRunner 返回 None"""
        runner = StrategyRunner(
            signal_callback=None,
            oms_callback=None,
        )

        runner.set_risk_mode_callback(lambda sid: RiskMode.NO_NEW_POSITIONS)

        await runner.load_strategy_from_code(
            strategy_id="test_strategy",
            version="v1",
            code=STRATEGY_CODE,
            config={},
            symbols=["BTCUSDT"],
        )
        await runner.start("test_strategy")

        mock_plugin = runner._plugins["test_strategy"]
        original_on_market_data = mock_plugin.on_market_data
        open_signal = Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        mock_plugin.on_market_data = AsyncMock(return_value=open_signal)

        result = await runner.tick("test_strategy", _make_market_data())

        assert result is None

        info = runner._infos["test_strategy"]
        assert info.blocked_reason == "RiskMode NO_NEW_POSITIONS active"

        await runner.stop("test_strategy")

    @pytest.mark.asyncio
    async def test_no_new_positions_allows_close_signal(self) -> None:
        """NO_NEW_POSITIONS + 减仓信号 -> StrategyRunner 调用 plugin 并返回信号"""
        oms_called = []

        async def oms_callback(strategy_id: str, signal: Signal) -> MagicMock:
            oms_called.append(signal)
            return MagicMock(order_id="order-1", status="submitted")

        runner = StrategyRunner(
            signal_callback=None,
            oms_callback=oms_callback,
        )

        runner.set_risk_mode_callback(lambda sid: RiskMode.NO_NEW_POSITIONS)

        await runner.load_strategy_from_code(
            strategy_id="test_strategy",
            version="v1",
            code=STRATEGY_CODE,
            config={},
            symbols=["BTCUSDT"],
        )
        await runner.start("test_strategy")

        mock_plugin = runner._plugins["test_strategy"]
        close_signal = Signal(
            signal_type=SignalType.CLOSE_LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        mock_plugin.on_market_data = AsyncMock(return_value=close_signal)

        result = await runner.tick("test_strategy", _make_market_data())

        assert result is not None
        assert result.signal_type == SignalType.CLOSE_LONG

        info = runner._infos["test_strategy"]
        assert info.blocked_reason is None

        await runner.stop("test_strategy")

    @pytest.mark.asyncio
    async def test_close_only_blocks_open_signal(self) -> None:
        """CLOSE_ONLY + 开仓信号 -> StrategyRunner 返回 None"""
        runner = StrategyRunner(
            signal_callback=None,
            oms_callback=None,
        )

        runner.set_risk_mode_callback(lambda sid: RiskMode.CLOSE_ONLY)

        await runner.load_strategy_from_code(
            strategy_id="test_strategy",
            version="v1",
            code=STRATEGY_CODE,
            config={},
            symbols=["BTCUSDT"],
        )
        await runner.start("test_strategy")

        mock_plugin = runner._plugins["test_strategy"]
        open_signal = Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        mock_plugin.on_market_data = AsyncMock(return_value=open_signal)

        result = await runner.tick("test_strategy", _make_market_data())

        assert result is None

        info = runner._infos["test_strategy"]
        assert info.blocked_reason == "RiskMode CLOSE_ONLY active"

        await runner.stop("test_strategy")

    @pytest.mark.asyncio
    async def test_close_only_allows_close_signal(self) -> None:
        """CLOSE_ONLY + 减仓信号 -> StrategyRunner 允许减仓，返回信号"""
        oms_called = []

        async def oms_callback(strategy_id: str, signal: Signal) -> MagicMock:
            oms_called.append(signal)
            return MagicMock(order_id="order-1", status="submitted")

        runner = StrategyRunner(
            signal_callback=None,
            oms_callback=oms_callback,
        )

        runner.set_risk_mode_callback(lambda sid: RiskMode.CLOSE_ONLY)

        await runner.load_strategy_from_code(
            strategy_id="test_strategy",
            version="v1",
            code=STRATEGY_CODE,
            config={},
            symbols=["BTCUSDT"],
        )
        await runner.start("test_strategy")

        mock_plugin = runner._plugins["test_strategy"]
        close_signal = Signal(
            signal_type=SignalType.CLOSE_LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        mock_plugin.on_market_data = AsyncMock(return_value=close_signal)

        result = await runner.tick("test_strategy", _make_market_data())

        assert result is not None
        assert result.signal_type == SignalType.CLOSE_LONG

        info = runner._infos["test_strategy"]
        assert info.blocked_reason is None

        await runner.stop("test_strategy")

    @pytest.mark.asyncio
    async def test_cancel_all_and_halt_blocks_all_signals(self) -> None:
        """CANCEL_ALL_AND_HALT -> StrategyRunner 返回 None"""
        runner = StrategyRunner(
            signal_callback=None,
            oms_callback=None,
        )

        runner.set_risk_mode_callback(lambda sid: RiskMode.CANCEL_ALL_AND_HALT)

        await runner.load_strategy_from_code(
            strategy_id="test_strategy",
            version="v1",
            code=STRATEGY_CODE,
            config={},
            symbols=["BTCUSDT"],
        )
        await runner.start("test_strategy")

        mock_plugin = runner._plugins["test_strategy"]
        close_signal = Signal(
            signal_type=SignalType.CLOSE_LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        mock_plugin.on_market_data = AsyncMock(return_value=close_signal)

        result = await runner.tick("test_strategy", _make_market_data())

        assert result is None

        info = runner._infos["test_strategy"]
        assert info.blocked_reason == "RiskMode CANCEL_ALL_AND_HALT active"

        await runner.stop("test_strategy")

    @pytest.mark.asyncio
    async def test_normal_mode_allows_all_signals(self) -> None:
        """NORMAL 模式 -> StrategyRunner 允许所有信号"""
        oms_called = []

        async def oms_callback(strategy_id: str, signal: Signal) -> MagicMock:
            oms_called.append(signal)
            return MagicMock(order_id="order-1", status="submitted")

        runner = StrategyRunner(
            signal_callback=None,
            oms_callback=oms_callback,
        )

        runner.set_risk_mode_callback(lambda sid: RiskMode.NORMAL)

        await runner.load_strategy_from_code(
            strategy_id="test_strategy",
            version="v1",
            code=STRATEGY_CODE,
            config={},
            symbols=["BTCUSDT"],
        )
        await runner.start("test_strategy")

        mock_plugin = runner._plugins["test_strategy"]
        open_signal = Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        mock_plugin.on_market_data = AsyncMock(return_value=open_signal)

        result = await runner.tick("test_strategy", _make_market_data())

        assert result is not None
        assert result.signal_type == SignalType.LONG

        info = runner._infos["test_strategy"]
        assert info.blocked_reason is None

        await runner.stop("test_strategy")

    @pytest.mark.asyncio
    async def test_no_risk_mode_callback_ignores_check(self) -> None:
        """没有设置 RiskMode callback -> StrategyRunner 正常执行"""
        oms_called = []

        async def oms_callback(strategy_id: str, signal: Signal) -> MagicMock:
            oms_called.append(signal)
            return MagicMock(order_id="order-1", status="submitted")

        runner = StrategyRunner(
            signal_callback=None,
            oms_callback=oms_callback,
        )

        await runner.load_strategy_from_code(
            strategy_id="test_strategy",
            version="v1",
            code=STRATEGY_CODE,
            config={},
            symbols=["BTCUSDT"],
        )
        await runner.start("test_strategy")

        mock_plugin = runner._plugins["test_strategy"]
        open_signal = Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        mock_plugin.on_market_data = AsyncMock(return_value=open_signal)

        result = await runner.tick("test_strategy", _make_market_data())

        assert result is not None
        assert result.signal_type == SignalType.LONG

        info = runner._infos["test_strategy"]
        assert info.blocked_reason is None

        await runner.stop("test_strategy")
