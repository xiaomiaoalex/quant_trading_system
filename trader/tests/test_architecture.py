"""
架构验证测试
=============
验证核心架构组件是否正确工作。

测试覆盖：
1. Domain Models (Money, Order, Position)
2. Ports (BrokerPort)
3. FakeBroker
4. OMS 状态机
5. RiskEngine
"""
import pytest
import asyncio
from decimal import Decimal
from datetime import datetime

from trader.core.domain.models.money import Money
from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.models.position import Position, BrokerPosition

from trader.adapters.broker.testing.fake_broker import FakeBroker, FakeBrokerConfig
from trader.adapters.persistence.memory.event_store import InMemoryStorage
from trader.core.application.oms import OMS
from trader.core.application.risk_engine import RiskEngine, RiskConfig, RejectionReason


# ==================== Domain Models Tests ====================

class TestMoney:
    """测试Money值对象"""

    def test_create_money(self):
        """测试创建Money"""
        money = Money(Decimal("100.50"), "USDT")
        assert money.amount == Decimal("100.50")
        assert money.currency == "USDT"

    def test_add_money(self):
        """测试金额加法"""
        m1 = Money(Decimal("100"), "USDT")
        m2 = Money(Decimal("50"), "USDT")
        result = m1 + m2
        assert result.amount == Decimal("150")

    def test_subtract_money(self):
        """测试金额减法"""
        m1 = Money(Decimal("100"), "USDT")
        m2 = Money(Decimal("30"), "USDT")
        result = m1 - m2
        assert result.amount == Decimal("70")

    def test_multiply_money(self):
        """测试金额乘法"""
        m1 = Money(Decimal("100"), "USDT")
        result = m1 * Decimal("0.1")
        assert result.amount == Decimal("10")

    def test_equality(self):
        """测试相等性"""
        m1 = Money(Decimal("100"), "USDT")
        m2 = Money(Decimal("100"), "USDT")
        m3 = Money(Decimal("100"), "BTC")
        assert m1 == m2
        assert m1 != m3


class TestOrder:
    """测试Order订单模型"""

    def test_create_order(self):
        """测试创建订单"""
        order = Order(
            order_id="",
            client_order_id="test_001",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.5"),
            strategy_name="test_strategy"
        )

        assert order.status == OrderStatus.PENDING
        assert order.client_order_id == "test_001"
        assert order.quantity == Decimal("0.5")

    def test_order_fill(self):
        """测试订单成交"""
        order = Order(
            order_id="test_001",
            client_order_id="cli_001",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1.0")
        )

        # 模拟部分成交
        order.fill(Decimal("0.5"), Decimal("50000"))
        assert order.status == OrderStatus.PARTIALLY_FILLED
        assert order.filled_quantity == Decimal("0.5")
        assert order.average_price == Decimal("50000")

        # 模拟完全成交
        order.fill(Decimal("0.5"), Decimal("50100"))
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == Decimal("1.0")

    def test_order_state_transitions(self):
        """测试订单状态转换"""
        order = Order(
            order_id="test_001",
            client_order_id="cli_001",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1.0")
        )

        # PENDING -> SUBMITTED
        order.submit()
        assert order.status == OrderStatus.SUBMITTED

        # SUBMITTED -> FILLED
        order.fill(Decimal("1.0"), Decimal("50000"))
        assert order.status == OrderStatus.FILLED

        # 终态不可再变
        with pytest.raises(ValueError):
            order.cancel()


class TestPosition:
    """测试Position持仓模型"""

    def test_open_position(self):
        """测试开仓"""
        position = Position(
            symbol="BTCUSDT",
            quantity=Decimal("0"),
            avg_price=Decimal("0")
        )

        position.open(Decimal("1.0"), Decimal("50000"))
        assert position.quantity == Decimal("1.0")
        assert position.avg_price == Decimal("50000")

    def test_add_position(self):
        """测试加仓"""
        position = Position(
            symbol="BTCUSDT",
            quantity=Decimal("1.0"),
            avg_price=Decimal("50000")
        )

        position.add(Decimal("0.5"), Decimal("51000"))
        assert position.quantity == Decimal("1.5")
        # 加权平均价 = (1*50000 + 0.5*51000) / 1.5 = 50333.33...
        assert abs(position.avg_price - Decimal("50333.333333")) < Decimal("0.01")

    def test_reduce_position(self):
        """测试减仓"""
        position = Position(
            symbol="BTCUSDT",
            quantity=Decimal("1.0"),
            avg_price=Decimal("50000")
        )

        realized = position.reduce(Decimal("0.3"), Decimal("52000"))
        # 成本 = 0.3 * 50000 = 15000
        # 收入 = 0.3 * 52000 = 15600
        # 盈利 = 15600 - 15000 = 600
        assert realized == Decimal("600")
        assert position.quantity == Decimal("0.7")


# ==================== Adapter Tests ====================

class TestFakeBroker:
    """测试FakeBroker模拟券商"""

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        """测试连接和断开"""
        broker = FakeBroker()
        await broker.connect()
        assert await broker.is_connected() is True

        await broker.disconnect()
        assert await broker.is_connected() is False

    @pytest.mark.asyncio
    async def test_place_order(self):
        """测试下单"""
        broker = FakeBroker()
        await broker.connect()

        order = await broker.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
            client_order_id="test_001"
        )

        assert order.client_order_id == "test_001"
        assert order.status == OrderStatus.SUBMITTED

    @pytest.mark.asyncio
    async def test_idempotency(self):
        """测试幂等性"""
        broker = FakeBroker()
        await broker.connect()

        # 同一client_order_id下单两次
        order1 = await broker.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
            client_order_id="idempotent_test"
        )

        order2 = await broker.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
            client_order_id="idempotent_test"
        )

        # 应该返回同一个订单
        assert order1.client_order_id == order2.client_order_id

    @pytest.mark.asyncio
    async def test_cancel_order(self):
        """测试撤单"""
        broker = FakeBroker()
        await broker.connect()

        order = await broker.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1")
        )

        success = await broker.cancel_order(order.client_order_id)
        assert success is True


# ==================== Application Tests ====================

class TestOMS:
    """测试OMS订单管理系统"""

    @pytest.mark.asyncio
    async def test_create_and_submit_order(self):
        """测试创建和提交订单"""
        # 准备
        broker = FakeBroker()
        storage = InMemoryStorage()
        oms = OMS(broker, storage)

        await broker.connect()
        await storage.connect()

        # 创建订单
        order = await oms.create_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
            strategy_name="test_strategy"
        )

        assert order.status == OrderStatus.PENDING

        # 提交订单
        await oms.submit_order(order.client_order_id)

        # 等待成交回调
        await asyncio.sleep(0.5)

        # 验证订单状态
        order = oms.get_order(order.client_order_id)
        assert order.status in [OrderStatus.SUBMITTED, OrderStatus.FILLED]

    @pytest.mark.asyncio
    async def test_order_recovery(self):
        """测试订单恢复"""
        broker = FakeBroker()
        storage = InMemoryStorage()
        oms = OMS(broker, storage)

        await broker.connect()
        await storage.connect()

        # 创建订单
        order = await oms.create_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
            strategy_name="test_strategy"
        )

        # 模拟重启：清空内存，重新加载
        oms._orders.clear()
        await oms.recover()

        # 订单应该从存储中恢复
        recovered = oms.get_order(order.client_order_id)
        assert recovered is not None


class TestRiskEngine:
    """测试风控引擎"""

    @pytest.mark.asyncio
    async def test_risk_check_pass(self):
        """测试风控通过"""
        broker = FakeBroker()
        broker.set_balance(Decimal("10000"), Decimal("10000"))
        await broker.connect()

        config = RiskConfig(
            max_daily_loss_percent=Decimal("5.0"),
            max_positions=3
        )
        risk_engine = RiskEngine(broker, config)

        # 创建信号
        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.1")
        )

        # 风控检查
        result = await risk_engine.check_signal(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_insufficient_balance(self):
        """测试资金不足"""
        broker = FakeBroker()
        broker.set_balance(Decimal("100"), Decimal("100"))  # 资金不足
        await broker.connect()

        config = RiskConfig()
        risk_engine = RiskEngine(broker, config)

        # 创建信号（需要5000USDT，但只有100）
        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.1")
        )

        result = await risk_engine.check_signal(signal)
        assert result.passed is False
        assert result.rejection_reason == RejectionReason.INSUFFICIENT_BALANCE


# ==================== 运行测试 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
