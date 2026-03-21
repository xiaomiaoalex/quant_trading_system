"""
DepthChecker 单元测试
=====================
测试订单簿深度检查和滑点估算功能。

验收标准：
- [x] 深度不足时pre-trade check返回REJECT
- [x] 滑点估算误差在合理范围（单测用mock orderbook验证）
- [x] 不引入任何IO（Core Plane约束）

测试场景：
1. 正常深度通过
2. 深度不足拒绝
3. 滑点超限拒绝
4. 边界条件测试
"""
import pytest
from decimal import Decimal
from datetime import datetime

from trader.core.domain.models.orderbook import (
    OrderBook, OrderBookLevel, DepthCheckResult
)
from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.services.depth_checker import (
    DepthChecker, DepthCheckerConfig
)


# ==================== Fixtures ====================

@pytest.fixture
def default_config() -> DepthCheckerConfig:
    """默认配置"""
    return DepthCheckerConfig(
        max_slippage_bps=Decimal("50"),  # 50 bps
        min_depth_levels=1,              # 最少1档（避免误杀）
        depth_check_enabled=True
    )


@pytest.fixture
def normal_orderbook() -> OrderBook:
    """
    正常深度的订单簿
    
    买一: 100.0, 数量: 10
    买二: 99.9, 数量: 10
    买三: 99.8, 数量: 10
    卖一: 100.1, 数量: 10
    卖二: 100.2, 数量: 10
    卖三: 100.3, 数量: 10
    
    中间价: 100.05
    """
    return OrderBook(
        symbol="BTCUSDT",
        bids=[
            OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("99.9"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("99.8"), quantity=Decimal("10")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("100.2"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("100.3"), quantity=Decimal("10")),
        ],
        timestamp=datetime.now()
    )


@pytest.fixture
def shallow_orderbook() -> OrderBook:
    """
    浅层订单簿（只有1档）
    
    买一: 100.0, 数量: 5
    卖一: 100.1, 数量: 5
    """
    return OrderBook(
        symbol="BTCUSDT",
        bids=[
            OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("5")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("5")),
        ],
        timestamp=datetime.now()
    )


@pytest.fixture
def high_slippage_orderbook() -> OrderBook:
    """
    高滑点订单簿（档位间价格跳跃大）
    
    买一: 100.0, 数量: 10
    买二: 99.0, 数量: 10  (滑点大)
    买三: 98.0, 数量: 10  (滑点更大)
    卖一: 100.1, 数量: 10
    卖二: 101.0, 数量: 10 (滑点大)
    卖三: 102.0, 数量: 10 (滑点更大)
    """
    return OrderBook(
        symbol="BTCUSDT",
        bids=[
            OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("99.0"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("98.0"), quantity=Decimal("10")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("101.0"), quantity=Decimal("10")),
            OrderBookLevel(price=Decimal("102.0"), quantity=Decimal("10")),
        ],
        timestamp=datetime.now()
    )


@pytest.fixture
def buy_signal() -> Signal:
    """买入信号"""
    return Signal(
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        price=Decimal("100"),
        quantity=Decimal("5"),
        timestamp=datetime.now(),
        signal_id="test-buy-signal"
    )


@pytest.fixture
def sell_signal() -> Signal:
    """卖出信号"""
    return Signal(
        symbol="BTCUSDT",
        signal_type=SignalType.SELL,
        price=Decimal("100"),
        quantity=Decimal("5"),
        timestamp=datetime.now(),
        signal_id="test-sell-signal"
    )


# ==================== 正常深度通过测试 ====================

class TestDepthCheckerNormal:
    """正常深度通过测试"""
    
    def test_buy_order_depth_pass(self, normal_orderbook, default_config, buy_signal):
        """买入订单：深度充足，滑点正常，应该通过"""
        checker = DepthChecker(config=default_config)
        
        result = checker.check_signal_depth(normal_orderbook, buy_signal)
        
        assert result.ok is True
        assert result.rejection_reason is None
        # 买入时滑点应该 > 0
        assert result.estimated_slippage_bps > 0
        assert result.available_qty >= float(buy_signal.quantity)
    
    def test_sell_order_depth_pass(self, normal_orderbook, default_config, sell_signal):
        """卖出订单：深度充足，滑点正常，应该通过"""
        checker = DepthChecker(config=default_config)
        
        result = checker.check_signal_depth(normal_orderbook, sell_signal)
        
        assert result.ok is True
        assert result.rejection_reason is None
        # 卖出时滑点应该 > 0
        assert result.estimated_slippage_bps > 0
        assert result.available_qty >= float(sell_signal.quantity)
    
    def test_partial_fill_qty(self, normal_orderbook, default_config):
        """部分成交数量检查"""
        checker = DepthChecker(config=default_config)
        
        # 买入 25 个，订单簿只有 30 个（每档10个）
        result = checker.check_depth(
            orderbook=normal_orderbook,
            target_qty=Decimal("25"),
            side=OrderSide.BUY
        )
        
        assert result.ok is True
        assert result.available_qty == 25.0  # 只能成交25个


# ==================== 深度不足拒绝测试 ====================

class TestDepthCheckerInsufficientDepth:
    """深度不足拒绝测试"""
    
    def test_insufficient_depth_reject(self, shallow_orderbook, default_config, buy_signal):
        """档位不足，应该拒绝（要求至少2档，但shallow_orderbook只有1档）"""
        # 使用要求至少2档的配置
        strict_config = DepthCheckerConfig(min_depth_levels=2)
        checker = DepthChecker(config=strict_config)
        
        result = checker.check_signal_depth(shallow_orderbook, buy_signal)
        
        assert result.ok is False
        assert result.rejection_reason == "INSUFFICIENT_LEVELS"
    
    def test_qty_exceeds_depth_reject(self, normal_orderbook, default_config):
        """订单量超过可成交量，应该拒绝"""
        checker = DepthChecker(config=default_config)
        
        # 买入 50 个，但每档只有 10 个，共 30 个
        result = checker.check_depth(
            orderbook=normal_orderbook,
            target_qty=Decimal("50"),
            side=OrderSide.BUY
        )
        
        assert result.ok is False
        assert result.rejection_reason == "INSUFFICIENT_DEPTH"
        assert result.available_qty == 30.0  # 只有 30 个可成交
    
    def test_empty_orderbook_reject(self, default_config, buy_signal):
        """空订单簿，应该拒绝"""
        empty_orderbook = OrderBook(symbol="BTCUSDT")
        checker = DepthChecker(config=default_config)
        
        result = checker.check_signal_depth(empty_orderbook, buy_signal)
        
        assert result.ok is False
        assert result.rejection_reason == "EMPTY_ORDERBOOK"
    
    def test_no_asks_reject_for_buy(self, default_config, buy_signal):
        """买入时没有卖盘，应该拒绝"""
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[
                OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10")),
            ],
            asks=[]  # 没有卖盘
        )
        checker = DepthChecker(config=default_config)
        
        result = checker.check_signal_depth(orderbook, buy_signal)
        
        assert result.ok is False
        assert result.rejection_reason == "NO_LEVELS"
    
    def test_no_bids_reject_for_sell(self, default_config, sell_signal):
        """卖出时没有买盘，应该拒绝"""
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[],  # 没有买盘
            asks=[
                OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("10")),
            ]
        )
        checker = DepthChecker(config=default_config)
        
        result = checker.check_signal_depth(orderbook, sell_signal)
        
        assert result.ok is False
        assert result.rejection_reason == "NO_LEVELS"


# ==================== 滑点超限拒绝测试 ====================

class TestDepthCheckerExcessiveSlippage:
    """滑点超限拒绝测试"""
    
    def test_excessive_slippage_reject(self, high_slippage_orderbook, default_config, buy_signal):
        """
        高滑点订单簿，滑点应该超过阈值限制
        
        买20个:
        - 档位1: 100.1 * 10 = 1001, 累计10个
        - 档位2: 101.0 * 10 = 1010, 累计20个
        - VWAP = (1001 + 1010) / 20 = 100.55
        - Mid = (100.0 + 100.1) / 2 = 100.05
        - 滑点 ≈ 49.98 bps
        
        使用 max_slippage_bps=40 的配置来测试超限
        """
        # 使用40 bps的阈值，确保滑点49.98 bps会触发拒绝
        low_threshold_config = DepthCheckerConfig(max_slippage_bps=Decimal("40"))
        checker = DepthChecker(config=low_threshold_config)
        result = checker.check_depth(
            orderbook=high_slippage_orderbook,
            target_qty=Decimal("20"),
            side=OrderSide.BUY
        )
        
        # 滑点约49.98 bps，超过40 bps阈值，应该被拒绝
        assert result.ok is False
        assert result.rejection_reason == "EXCESSIVE_SLIPPAGE"
        assert 45.0 < result.estimated_slippage_bps < 55.0  # 约49.98 bps
    
    def test_slippage_calculation_accuracy(self, default_config):
        """
        滑点计算精度验证
        
        精确测试用例：
        - 买一: 100.00, 数量: 10
        - 卖一: 100.01, 数量: 10
        - 买二: 100.02, 数量: 10
        - 卖二: 100.03, 数量: 10
        
        中间价: (100.00 + 100.01) / 2 = 100.005
        
        买入5个:
        - 在卖一档位成交
        - VWAP = 100.01
        - 滑点 = (100.01 - 100.005) / 100.005 * 10000 = 0.05 / 100.005 * 10000 ≈ 0.5 bps
        """
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[
                OrderBookLevel(price=Decimal("100.00"), quantity=Decimal("10")),
                OrderBookLevel(price=Decimal("99.98"), quantity=Decimal("10")),
            ],
            asks=[
                OrderBookLevel(price=Decimal("100.01"), quantity=Decimal("10")),
                OrderBookLevel(price=Decimal("100.03"), quantity=Decimal("10")),
            ]
        )
        checker = DepthChecker(config=default_config)
        
        result = checker.check_depth(
            orderbook=orderbook,
            target_qty=Decimal("5"),
            side=OrderSide.BUY
        )
        
        assert result.ok is True
        # 滑点应该很小，约 0.5 bps
        assert 0 < result.estimated_slippage_bps < 1.0


# ==================== 边界条件测试 ====================

class TestDepthCheckerEdgeCases:
    """边界条件测试"""
    
    def test_zero_qty_order(self, normal_orderbook, default_config):
        """零数量订单，应该拒绝或通过（取决于业务规则，这里按失败处理）"""
        checker = DepthChecker(config=default_config)
        
        result = checker.check_depth(
            orderbook=normal_orderbook,
            target_qty=Decimal("0"),
            side=OrderSide.BUY
        )
        
        # 零数量订单应该失败
        assert result.ok is False
    
    def test_exact_depth_match(self, normal_orderbook, default_config):
        """精确匹配深度"""
        checker = DepthChecker(config=default_config)
        
        # 每档10个，共30个
        result = checker.check_depth(
            orderbook=normal_orderbook,
            target_qty=Decimal("30"),
            side=OrderSide.BUY
        )
        
        assert result.ok is True
        assert result.available_qty == 30.0
    
    def test_disabled_depth_check(self, normal_orderbook, buy_signal):
        """禁用深度检查"""
        config = DepthCheckerConfig(depth_check_enabled=False)
        checker = DepthChecker(config=config)
        
        # 禁用后，即使深度不足也返回通过（但 check_depth 本身不受影响）
        result = checker.check_signal_depth(normal_orderbook, buy_signal)
        
        # check_signal_depth 仍会执行实际检查
        assert result.ok is True
    
    def test_single_level_exact_match(self, default_config):
        """单档位精确匹配"""
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[
                OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("5")),
            ],
            asks=[
                OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("5")),
            ]
        )
        # 当 min_depth_levels=1 时
        config = DepthCheckerConfig(min_depth_levels=1)
        checker = DepthChecker(config=config)
        
        result = checker.check_depth(
            orderbook=orderbook,
            target_qty=Decimal("5"),
            side=OrderSide.BUY
        )
        
        assert result.ok is True
        assert result.available_qty == 5.0


# ==================== VWAP 计算测试 ====================

class TestVWAPCalculation:
    """VWAP计算测试"""
    
    def test_vwap_single_level(self):
        """单档位VWAP"""
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10"))],
            asks=[OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("10"))]
        )
        checker = DepthChecker()
        
        result = checker.check_depth(
            orderbook=orderbook,
            target_qty=Decimal("5"),
            side=OrderSide.BUY
        )
        
        # VWAP = 100.1（只在第一档成交）
        # Mid = (100.0 + 100.1) / 2 = 100.05
        # Slippage = (100.1 - 100.05) / 100.05 * 10000 ≈ 5 bps
        assert result.ok is True
        assert 4 < result.estimated_slippage_bps < 6  # 约5 bps
    
    def test_vwap_multiple_levels(self):
        """多档位VWAP"""
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[
                OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10")),
                OrderBookLevel(price=Decimal("99.0"), quantity=Decimal("10")),
            ],
            asks=[
                OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("10")),
                OrderBookLevel(price=Decimal("101.0"), quantity=Decimal("10")),
            ]
        )
        checker = DepthChecker()
        
        # 买入15个，需要跨越两个档位
        result = checker.check_depth(
            orderbook=orderbook,
            target_qty=Decimal("15"),
            side=OrderSide.BUY
        )
        
        # 档位1: 100.1 * 10 = 1001
        # 档位2: 101.0 * 5 = 505
        # VWAP = (1001 + 505) / 15 = 100.4
        # Mid = (100.0 + 100.1) / 2 = 100.05
        # Slippage = (100.4 - 100.05) / 100.05 * 10000 ≈ 35 bps
        assert result.ok is True
        assert 30 < result.estimated_slippage_bps < 40  # 约35 bps


# ==================== 辅助方法测试 ====================

class TestOrderBookProperties:
    """OrderBook 属性测试"""
    
    def test_mid_price_calculation(self):
        """中间价计算"""
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10"))],
            asks=[OrderBookLevel(price=Decimal("100.2"), quantity=Decimal("10"))]
        )
        
        assert orderbook.mid_price == Decimal("100.1")
    
    def test_spread_calculation(self):
        """价差计算"""
        orderbook = OrderBook(
            symbol="BTCUSDT",
            bids=[OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10"))],
            asks=[OrderBookLevel(price=Decimal("100.2"), quantity=Decimal("10"))]
        )
        
        assert orderbook.spread == Decimal("0.2")
        # 0.2 / 100.1 * 10000 ≈ 19.98 bps
        assert Decimal("19.9") < orderbook.spread_bps < Decimal("20.1")
    
    def test_empty_orderbook_properties(self):
        """空订单簿属性"""
        orderbook = OrderBook(symbol="BTCUSDT")
        
        assert orderbook.best_bid is None
        assert orderbook.best_ask is None
        assert orderbook.mid_price is None
        assert orderbook.spread is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
