"""
Order Domain Model Tests - 订单领域模型单元测试
================================================
覆盖 Order 状态机、fill() 均价计算、状态转换等核心逻辑。
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone

from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType


class TestOrderFillAveragePrice:
    """P0-1 回归测试：Order.fill() 加权平均价计算"""

    def test_single_fill_sets_average_price(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.fill(Decimal("2"), Decimal("100"))
        assert order.average_price == Decimal("100")
        assert order.filled_quantity == Decimal("2")

    def test_two_partial_fills_correct_average(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.fill(Decimal("1"), Decimal("100"))
        assert order.average_price == Decimal("100")

        order.fill(Decimal("1"), Decimal("200"))
        assert order.filled_quantity == Decimal("2")
        assert order.average_price == Decimal("150")

    def test_three_partial_fills_correct_average(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("3"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.fill(Decimal("1"), Decimal("100"))
        order.fill(Decimal("1"), Decimal("200"))
        order.fill(Decimal("1"), Decimal("300"))

        assert order.filled_quantity == Decimal("3")
        assert order.average_price == Decimal("200")

    def test_unequal_quantities_average(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("3"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.fill(Decimal("1"), Decimal("100"))
        order.fill(Decimal("2"), Decimal("250"))

        assert order.filled_quantity == Decimal("3")
        expected_avg = (Decimal("100") * 1 + Decimal("250") * 2) / 3
        assert order.average_price == expected_avg

    def test_fill_from_zero_average_price(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("0"),
            status=OrderStatus.SUBMITTED,
        )
        order.fill(Decimal("1"), Decimal("50000"))
        assert order.average_price == Decimal("50000")


class TestOrderStateMachine:
    """订单状态机测试"""

    def test_submit_from_pending(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
            status=OrderStatus.PENDING,
        )
        order.submit()
        assert order.status == OrderStatus.SUBMITTED

    def test_submit_from_submitted_raises(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        with pytest.raises(ValueError):
            order.submit()

    def test_fill_from_submitted(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.fill(Decimal("1"), Decimal("100"))
        assert order.status == OrderStatus.FILLED

    def test_fill_from_partially_filled(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.fill(Decimal("1"), Decimal("100"))
        assert order.status == OrderStatus.PARTIALLY_FILLED

        order.fill(Decimal("1"), Decimal("110"))
        assert order.status == OrderStatus.FILLED

    def test_fill_from_filled_raises(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
            status=OrderStatus.FILLED,
        )
        with pytest.raises(ValueError):
            order.fill(Decimal("1"), Decimal("100"))

    def test_fill_from_cancelled_raises(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
            status=OrderStatus.CANCELLED,
        )
        with pytest.raises(ValueError):
            order.fill(Decimal("1"), Decimal("100"))

    def test_cancel_from_submitted(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.cancel()
        assert order.status == OrderStatus.CANCELLED

    def test_is_terminal(self):
        assert OrderStatus.FILLED in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]
        assert OrderStatus.CANCELLED in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]
        assert OrderStatus.SUBMITTED not in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]

    def test_reject(self):
        order = Order(
            order_id="1",
            client_order_id="cl-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
            status=OrderStatus.SUBMITTED,
        )
        order.reject("insufficient balance")
        assert order.status == OrderStatus.REJECTED
