"""
Test EscapeTime Simulator - 平仓时间模拟器测试
===============================================
测试 EscapeTimeSimulator 的各项功能。

覆盖范围：
1. 空持仓处理
2. KillSwitch 检查（L2/L3 阻止平仓）
3. 冷却期检查
4. 深度模拟和滑点估算
5. Regime 折扣
6. 时间估算
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import Mock

from trader.core.domain.models.position import Position
from trader.core.domain.models.orderbook import OrderBook, OrderBookLevel
from trader.core.domain.services.position_risk_constructor import (
    MarketRegime,
    PositionRiskConstructorConfig,
)
from trader.core.domain.services.escape_time_simulator import (
    EscapeTimeSimulator,
    EscapeTimeSimulatorConfig,
    EscapeTimeResult,
    DepthLevel,
    KillSwitchLevel,
)


# ==================== 测试数据工厂 ====================

def create_orderbook(
    bids: list[tuple[str, float, float]] | None = None,
    asks: list[tuple[str, float, float]] | None = None,
) -> OrderBook:
    """
    创建测试用订单簿
    
    Args:
        bids: 买盘 [(symbol, price, quantity), ...]
        asks: 卖盘 [(symbol, price, quantity), ...]
    """
    bids_list = bids or [("BTCUSDT", 50000.0, 10.0), ("BTCUSDT", 49900.0, 20.0)]
    asks_list = asks or [("BTCUSDT", 50100.0, 15.0), ("BTCUSDT", 50200.0, 25.0)]
    
    return OrderBook(
        symbol="BTCUSDT",
        bids=[
            OrderBookLevel(price=Decimal(str(p)), quantity=Decimal(str(q)))
            for _, p, q in bids_list
        ],
        asks=[
            OrderBookLevel(price=Decimal(str(p)), quantity=Decimal(str(q)))
            for _, p, q in asks_list
        ],
        timestamp=datetime.now(timezone.utc),
    )


def create_position(
    symbol: str = "BTCUSDT",
    quantity: float = 1.0,
    avg_price: float = 50000.0,
    current_price: float = 50000.0,
) -> Position:
    """创建测试用持仓"""
    pos = Position(
        symbol=symbol,
        quantity=Decimal(str(quantity)),
        avg_price=Decimal(str(avg_price)),
        current_price=Decimal(str(current_price)),
    )
    return pos


def create_risk_config(
    cooldown_seconds: int = 300,
    cooldown_enabled: bool = True,
) -> PositionRiskConstructorConfig:
    """创建测试用风控配置"""
    return PositionRiskConstructorConfig(
        cooldown_seconds=cooldown_seconds,
        cooldown_enabled=cooldown_enabled,
    )


# ==================== 基础功能测试 ====================

class TestEmptyPosition:
    """空持仓处理测试"""
    
    def test_empty_position_returns_zero_time(self):
        """空持仓应返回零时间和零滑点"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        orderbook = create_orderbook()
        position = create_position(quantity=0)
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True
        assert result.estimated_seconds == 0
        assert result.max_slippage_bps == 0
        assert result.blocking_factors == []
        assert result.escape_path == []


# ==================== KillSwitch 测试 ====================

class TestKillSwitchBlocking:
    """KillSwitch 阻止测试"""
    
    def test_l2_blocks_escape(self):
        """L2 级别应阻止平仓"""
        mock_killswitch = Mock()
        mock_killswitch.get_killswitch_level.return_value = KillSwitchLevel.L2_CANCEL_ALL_AND_HALT
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(),
            killswitch_provider=mock_killswitch,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is False
        assert "L2_CANCEL_ALL_AND_HALT" in result.blocking_factors
        assert result.estimated_seconds == 0
    
    def test_l3_blocks_escape(self):
        """L3 级别应阻止平仓"""
        mock_killswitch = Mock()
        mock_killswitch.get_killswitch_level.return_value = KillSwitchLevel.L3_LIQUIDATE_AND_DISCONNECT
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(),
            killswitch_provider=mock_killswitch,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is False
        assert "L3_LIQUIDATE_AND_DISCONNECT" in result.blocking_factors
    
    def test_l0_allows_escape(self):
        """L0 级别应允许平仓"""
        mock_killswitch = Mock()
        mock_killswitch.get_killswitch_level.return_value = KillSwitchLevel.L0_NORMAL
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(),
            killswitch_provider=mock_killswitch,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True
    
    def test_l1_allows_escape(self):
        """L1 级别应允许平仓（禁止新开仓但允许平仓）"""
        mock_killswitch = Mock()
        mock_killswitch.get_killswitch_level.return_value = KillSwitchLevel.L1_NO_NEW_POSITIONS
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(),
            killswitch_provider=mock_killswitch,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True


# ==================== 冷却期测试 ====================

class TestCooldownBlocking:
    """冷却期阻止测试"""
    
    def test_in_cooldown_blocks_escape(self):
        """在冷却期应阻止平仓"""
        mock_cooldown = Mock()
        # 上次交易在 100 秒前，冷却期为 300 秒
        mock_cooldown.get_last_trade_time.return_value = datetime.now(timezone.utc) - timedelta(seconds=100)
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(cooldown_seconds=300, cooldown_enabled=True),
            cooldown_provider=mock_cooldown,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is False
        assert "IN_COOLDOWN" in result.blocking_factors
        # 剩余冷却时间约 200 秒
        assert 190 <= result.estimated_seconds <= 210
    
    def test_cooldown_expired_allows_escape(self):
        """冷却期已过应允许平仓"""
        mock_cooldown = Mock()
        # 上次交易在 400 秒前，冷却期为 300 秒
        mock_cooldown.get_last_trade_time.return_value = datetime.now(timezone.utc) - timedelta(seconds=400)
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(cooldown_seconds=300, cooldown_enabled=True),
            cooldown_provider=mock_cooldown,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True
    
    def test_cooldown_disabled_allows_escape(self):
        """冷却期禁用时应允许平仓"""
        mock_cooldown = Mock()
        mock_cooldown.get_last_trade_time.return_value = datetime.now(timezone.utc) - timedelta(seconds=1)
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(cooldown_seconds=300, cooldown_enabled=False),
            cooldown_provider=mock_cooldown,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True


# ==================== 深度模拟测试 ====================

class TestDepthSimulation:
    """深度模拟测试"""
    
    def test_sufficient_depth_allows_escape(self):
        """深度充足时应允许平仓"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)  # 1 BTC
        orderbook = create_orderbook(
            bids=[("BTCUSDT", 50000.0, 10.0), ("BTCUSDT", 49900.0, 20.0)]  # 总共 30 BTC
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True
        assert len(result.escape_path) > 0
    
    def test_escape_path_contains_levels(self):
        """平仓路径应包含档位信息"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)
        orderbook = create_orderbook(
            bids=[("BTCUSDT", 50000.0, 10.0), ("BTCUSDT", 49900.0, 20.0)]
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert len(result.escape_path) > 0
        for level in result.escape_path:
            assert isinstance(level, DepthLevel)
            assert level.price > 0
            assert level.cumulative_quantity > 0
    
    def test_empty_bids_uses_asks(self):
        """买盘为空时应使用卖盘（做空平仓）"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=-1.0)  # 做空
        orderbook = create_orderbook(
            bids=[],  # 买盘为空
            asks=[("BTCUSDT", 50100.0, 15.0), ("BTCUSDT", 50200.0, 25.0)]  # 卖盘有深度
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True
        assert len(result.escape_path) > 0
    
    def test_short_position_slippage_with_asks(self):
        """做空平仓使用卖盘时滑点计算验证"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=-1.0)  # 做空持仓
        # 买盘为空，卖盘价格高于中间价
        orderbook = create_orderbook(
            bids=[],  # 买盘为空
            asks=[("BTCUSDT", 50100.0, 15.0), ("BTCUSDT", 50200.0, 25.0)]
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        # 做空平仓（买入平空）应允许
        assert result.can_escape is True
        # 滑点应大于 0（因为卖盘价格高于中间价）
        assert result.max_slippage_bps > 0
    
    def test_insufficient_depth_blocks_escape(self):
        """深度不足时应阻止平仓"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)  # 需要平仓 1 BTC
        orderbook = create_orderbook(
            bids=[("BTCUSDT", 50000.0, 0.1)]  # 只有 0.1 BTC 深度，远不足
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is False
        assert "INSUFFICIENT_DEPTH" in result.blocking_factors
    
    def test_empty_orderbook_blocks_escape(self):
        """空订单簿时应阻止平仓"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)
        # 直接创建真正的空订单簿
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[],  # 买盘为空
            asks=[],  # 卖盘也为空
            timestamp=datetime.now(timezone.utc),
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is False


# ==================== Regime 折扣测试 ====================

class TestRegimeDiscount:
    """市场状态折扣测试"""
    
    def test_crisis_reduces_escapable_quantity(self):
        """危机模式应减少可平仓数量"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)
        # 深度不足以平仓 1 BTC（只有 0.5 BTC）
        orderbook = create_orderbook(
            bids=[("BTCUSDT", 50000.0, 0.5)]
        )
        
        result_normal = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        # 在正常市场 (折扣 1.0)，需要平仓 1.0 BTC，但只有 0.5 BTC 深度
        # 应该因深度不足而被阻止
        assert result_normal.can_escape is False
        assert "INSUFFICIENT_DEPTH" in result_normal.blocking_factors
        
        result_crisis = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.CRISIS,
        )
        
        # 危机模式下 (折扣 0.2)，需要平仓 0.2 BTC (1.0 * 0.2)，深度 0.5 BTC 足够
        # 应该允许平仓
        assert result_crisis.can_escape is True
    
    def test_regime_discount_factor_applied(self):
        """验证 regime 折扣因子被正确应用"""
        config = create_risk_config()
        crisis_discount = config.regime_discounts[MarketRegime.CRISIS]
        
        assert crisis_discount == Decimal("0.2")
        
        bear_discount = config.regime_discounts[MarketRegime.BEAR]
        assert bear_discount == Decimal("0.5")
        
        bull_discount = config.regime_discounts[MarketRegime.BULL]
        assert bull_discount == Decimal("1.0")
        
        sideways_discount = config.regime_discounts[MarketRegime.SIDEWAYS]
        assert sideways_discount == Decimal("0.7")


# ==================== 时间估算测试 ====================

class TestTimeEstimation:
    """时间估算测试"""
    
    def test_time_estimation_positive(self):
        """时间估算应为正数"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)
        orderbook = create_orderbook(
            bids=[("BTCUSDT", 50000.0, 10.0), ("BTCUSDT", 49900.0, 20.0)]
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.estimated_seconds > 0
    
    def test_crisis_increases_time(self):
        """危机模式应增加估算时间"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)
        orderbook = create_orderbook(
            bids=[("BTCUSDT", 50000.0, 10.0), ("BTCUSDT", 49900.0, 20.0)]
        )
        
        result_bull = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        result_crisis = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.CRISIS,
        )
        
        # 危机模式的时间应 >= 正常模式
        assert result_crisis.estimated_seconds >= result_bull.estimated_seconds


# ==================== 滑点测试 ====================

class TestSlippageEstimation:
    """滑点估算测试"""
    
    def test_slippage_calculated(self):
        """滑点应被正确计算"""
        simulator = EscapeTimeSimulator(risk_config=create_risk_config())
        position = create_position(quantity=1.0)
        # 买盘价格：50000, 49900
        # 卖盘价格：50100, 50200
        # 中间价约 50050
        orderbook = create_orderbook(
            bids=[("BTCUSDT", 50000.0, 10.0), ("BTCUSDT", 49900.0, 20.0)],
            asks=[("BTCUSDT", 50100.0, 15.0), ("BTCUSDT", 50200.0, 25.0)]
        )
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        # 卖出时滑点 = (mid - bid) / mid * 10000
        # 如果全部以 50000 成交，滑点 = (50050 - 50000) / 50050 * 10000 ≈ 10 bps
        assert result.max_slippage_bps > 0


# ==================== 边界条件测试 ====================

class TestEdgeCases:
    """边界条件测试"""
    
    def test_no_killswitch_provider(self):
        """没有 KillSwitch 提供者时应继续执行"""
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(),
            killswitch_provider=None,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True
    
    def test_no_cooldown_provider(self):
        """没有冷却期提供者时应继续执行"""
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(),
            cooldown_provider=None,
        )
        position = create_position(quantity=1.0)
        orderbook = create_orderbook()
        
        result = simulator.estimate_escape_time(
            position=position,
            orderbook=orderbook,
            regime=MarketRegime.BULL,
        )
        
        assert result.can_escape is True
    
    def test_custom_config(self):
        """自定义配置应生效"""
        config = EscapeTimeSimulatorConfig(
            order_execution_time_seconds=10,
            market_impact_coefficient_bps=Decimal("2.0"),
            insufficient_depth_penalty_seconds=60,
        )
        
        simulator = EscapeTimeSimulator(
            risk_config=create_risk_config(),
            config=config,
        )
        
        assert simulator._config.order_execution_time_seconds == 10
        assert simulator._config.market_impact_coefficient_bps == Decimal("2.0")
        assert simulator._config.insufficient_depth_penalty_seconds == 60


# ==================== EscapeTimeResult 工厂方法测试 ====================

class TestEscapeTimeResultFactory:
    """EscapeTimeResult 工厂方法测试"""
    
    def test_blocked_factory(self):
        """blocked 工厂方法应正确创建结果"""
        result = EscapeTimeResult.blocked(
            blocking_factors=["TEST_BLOCK"],
            estimated_seconds=100,
        )
        
        assert result.can_escape is False
        assert result.blocking_factors == ["TEST_BLOCK"]
        assert result.estimated_seconds == 100
        assert result.escape_path == []
    
    def test_can_escape_factory(self):
        """can_escape_result 工厂方法应正确创建结果"""
        path = [
            DepthLevel(price=Decimal("50000"), cumulative_quantity=Decimal("0.5"), level_index=0),
            DepthLevel(price=Decimal("49900"), cumulative_quantity=Decimal("1.0"), level_index=1),
        ]
        
        result = EscapeTimeResult.can_escape_result(
            estimated_seconds=30,
            max_slippage_bps=15,
            escape_path=path,
        )
        
        assert result.can_escape is True
        assert result.estimated_seconds == 30
        assert result.max_slippage_bps == 15
        assert result.escape_path == path
        assert result.blocking_factors == []


# ==================== DepthLevel 测试 ====================

class TestDepthLevel:
    """DepthLevel 数据类测试"""
    
    def test_depth_level_immutable(self):
        """DepthLevel 应该是不可变的"""
        level = DepthLevel(price=Decimal("50000"), cumulative_quantity=Decimal("1.0"), level_index=0)
        
        with pytest.raises(AttributeError):
            level.price = Decimal("51000")
    
    def test_depth_level_attributes(self):
        """DepthLevel 属性应正确"""
        level = DepthLevel(price=Decimal("50000"), cumulative_quantity=Decimal("1.0"), level_index=5)
        
        assert level.price == Decimal("50000")
        assert level.cumulative_quantity == Decimal("1.0")
        assert level.level_index == 5


# ==================== KillSwitchLevel 测试 ====================

class TestKillSwitchLevel:
    """KillSwitchLevel 枚举测试"""
    
    def test_levels_order(self):
        """KillSwitch 级别应有正确的顺序"""
        assert KillSwitchLevel.L0_NORMAL == 0
        assert KillSwitchLevel.L1_NO_NEW_POSITIONS == 1
        assert KillSwitchLevel.L2_CANCEL_ALL_AND_HALT == 2
        assert KillSwitchLevel.L3_LIQUIDATE_AND_DISCONNECT == 3
    
    def test_l2_greater_than_l0(self):
        """L2 > L0"""
        assert KillSwitchLevel.L2_CANCEL_ALL_AND_HALT > KillSwitchLevel.L0_NORMAL
    
    def test_l3_greater_than_l2(self):
        """L3 > L2"""
        assert KillSwitchLevel.L3_LIQUIDATE_AND_DISCONNECT > KillSwitchLevel.L2_CANCEL_ALL_AND_HALT
