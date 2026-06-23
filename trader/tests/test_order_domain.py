"""Order domain model state-machine tests."""

from decimal import Decimal

import pytest

from trader.core.domain.models.order import Order, OrderSide, OrderStatus, OrderType


def make_order(qty: str = "1", status: OrderStatus = OrderStatus.SUBMITTED) -> Order:
    return Order(
        order_id="1",
        cl_ord_id="cl-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal(qty),
        price=Decimal("100"),
        status=status,
    )


class TestOrderFillAveragePrice:
    def test_single_fill_sets_average_price(self):
        order = make_order("2")
        order.fill(Decimal("2"), Decimal("100"))
        assert order.average_price == Decimal("100")
        assert order.filled_qty == Decimal("2")

    def test_two_partial_fills_correct_average(self):
        order = make_order("2")
        order.fill(Decimal("1"), Decimal("100"))
        order.fill(Decimal("1"), Decimal("200"))
        assert order.filled_qty == Decimal("2")
        assert order.average_price == Decimal("150")

    def test_three_partial_fills_correct_average(self):
        order = make_order("3")
        order.fill(Decimal("1"), Decimal("100"))
        order.fill(Decimal("1"), Decimal("200"))
        order.fill(Decimal("1"), Decimal("300"))
        assert order.filled_qty == Decimal("3")
        assert order.average_price == Decimal("200")

    def test_unequal_quantities_average(self):
        order = make_order("3")
        order.fill(Decimal("1"), Decimal("100"))
        order.fill(Decimal("2"), Decimal("250"))
        assert order.filled_qty == Decimal("3")
        assert order.average_price == Decimal("200")

    def test_fill_from_zero_average_price(self):
        order = make_order()
        order.price = Decimal("0")
        order.fill(Decimal("1"), Decimal("50000"))
        assert order.average_price == Decimal("50000")


class TestOrderStateMachine:
    def test_submit_from_pending(self):
        order = make_order(status=OrderStatus.PENDING)
        order.submit()
        assert order.status == OrderStatus.SUBMITTED

    def test_submit_from_submitted_raises(self):
        with pytest.raises(ValueError):
            make_order().submit()

    @pytest.mark.parametrize("terminal", [OrderStatus.FILLED, OrderStatus.CANCELLED])
    def test_fill_from_terminal_raises(self, terminal: OrderStatus):
        with pytest.raises(ValueError):
            make_order(status=terminal).fill(Decimal("1"), Decimal("100"))

    def test_fill_transitions_from_submitted_to_filled(self):
        order = make_order()
        order.fill(Decimal("1"), Decimal("100"))
        assert order.status == OrderStatus.FILLED

    def test_partial_then_filled_transition(self):
        order = make_order("2")
        order.fill(Decimal("1"), Decimal("100"))
        assert order.status == OrderStatus.PARTIALLY_FILLED
        order.fill(Decimal("1"), Decimal("110"))
        assert order.status == OrderStatus.FILLED

    def test_cancel_and_reject(self):
        order = make_order()
        order.cancel()
        assert order.status == OrderStatus.CANCELLED
        order = make_order()
        order.reject("insufficient balance")
        assert order.status == OrderStatus.REJECTED
