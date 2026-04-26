"""
Position Domain Model Tests - 持仓领域模型单元测试
==================================================
覆盖 Position.open/add/reduce/close、PositionLot、PositionLedger 核心逻辑。
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone

from trader.core.domain.models.position import (
    Position,
    PositionLot,
    PositionLedger,
    PositionStatus,
    PositionSource,
    CostBasisMethod,
    BrokerPosition,
    PositionReconciliation,
)


class TestPositionOpen:
    def test_open_new_position(self):
        pos = Position(symbol="BTCUSDT", strategy_id="strat_a")
        pos.open(Decimal("1"), Decimal("50000"))
        assert pos.quantity == Decimal("1")
        assert pos.avg_price == Decimal("50000")
        assert pos.status == PositionStatus.ACTIVE
        assert pos.opened_at is not None

    def test_open_zero_quantity_raises(self):
        pos = Position(symbol="BTCUSDT")
        with pytest.raises(ValueError):
            pos.open(Decimal("0"), Decimal("50000"))

    def test_open_negative_quantity_raises(self):
        pos = Position(symbol="BTCUSDT")
        with pytest.raises(ValueError):
            pos.open(Decimal("-1"), Decimal("50000"))

    def test_open_then_add(self):
        pos = Position(symbol="BTCUSDT", strategy_id="strat_a")
        pos.open(Decimal("1"), Decimal("50000"))
        pos.add(Decimal("1"), Decimal("60000"))
        assert pos.quantity == Decimal("2")
        assert pos.avg_price == Decimal("55000")


class TestPositionAdd:
    def test_add_updates_weighted_average(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"), avg_price=Decimal("100"))
        pos.add(Decimal("1"), Decimal("200"))
        assert pos.quantity == Decimal("2")
        assert pos.avg_price == Decimal("150")

    def test_add_zero_raises(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"), avg_price=Decimal("100"))
        with pytest.raises(ValueError):
            pos.add(Decimal("0"), Decimal("200"))

    def test_add_negative_raises(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"), avg_price=Decimal("100"))
        with pytest.raises(ValueError):
            pos.add(Decimal("-1"), Decimal("200"))


class TestPositionReduce:
    def test_reduce_calculates_realized_pnl(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("2"), avg_price=Decimal("100"))
        realized = pos.reduce(Decimal("1"), Decimal("150"))
        assert realized == Decimal("50")
        assert pos.realized_pnl == Decimal("50")
        assert pos.quantity == Decimal("1")

    def test_reduce_more_than_held_clamps(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"), avg_price=Decimal("100"))
        realized = pos.reduce(Decimal("2"), Decimal("150"))
        assert pos.quantity == Decimal("0")
        assert pos.status == PositionStatus.CLOSED

    def test_reduce_zero_raises(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"), avg_price=Decimal("100"))
        with pytest.raises(ValueError):
            pos.reduce(Decimal("0"), Decimal("150"))

    def test_close_position(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"), avg_price=Decimal("100"))
        realized = pos.close(Decimal("200"))
        assert realized == Decimal("100")
        assert pos.quantity == Decimal("0")
        assert pos.status == PositionStatus.CLOSED
        assert pos.avg_price == Decimal("0")


class TestPositionUpdatePrice:
    def test_update_price_calculates_unrealized_pnl(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"), avg_price=Decimal("100"))
        pos.update_price(Decimal("150"))
        assert pos.unrealized_pnl == Decimal("50")

    def test_update_price_zero_quantity(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("0"))
        pos.update_price(Decimal("150"))
        assert pos.unrealized_pnl == Decimal("0")


class TestPositionProperties:
    def test_market_value(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("2"), avg_price=Decimal("100"), current_price=Decimal("150"))
        assert pos.market_value == Decimal("300")

    def test_cost_basis(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("2"), avg_price=Decimal("100"))
        assert pos.cost_basis == Decimal("200")

    def test_is_long(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("1"))
        assert pos.is_long is True

    def test_is_empty(self):
        pos = Position(symbol="BTCUSDT", quantity=Decimal("0"))
        assert pos.is_empty is True

    def test_equality_by_position_id(self):
        a = Position(position_id="id-1", symbol="BTCUSDT")
        b = Position(position_id="id-1", symbol="ETHUSDT")
        assert a == b

    def test_inequality_by_position_id(self):
        a = Position(position_id="id-1", symbol="BTCUSDT")
        b = Position(position_id="id-2", symbol="BTCUSDT")
        assert a != b


class TestPositionLot:
    def test_apply_fee_deducts_from_remaining(self):
        lot = PositionLot(
            lot_id="lot-1",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            original_qty=Decimal("1"),
            remaining_qty=Decimal("1"),
            fill_price=Decimal("50000"),
            fee_qty=Decimal("0.001"),
        )
        lot.apply_fee()
        assert lot.remaining_qty == Decimal("0.999")

    def test_apply_fee_insufficient_logs_warning(self, caplog):
        import logging
        lot = PositionLot(
            lot_id="lot-1",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            original_qty=Decimal("0.001"),
            remaining_qty=Decimal("0.001"),
            fill_price=Decimal("50000"),
            fee_qty=Decimal("0.01"),
        )
        with caplog.at_level(logging.WARNING):
            lot.apply_fee()
        assert lot.remaining_qty == Decimal("0.001")
        assert "exceeds remaining_qty" in caplog.text

    def test_apply_fee_zero_does_nothing(self):
        lot = PositionLot(
            lot_id="lot-1",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            original_qty=Decimal("1"),
            remaining_qty=Decimal("1"),
            fill_price=Decimal("50000"),
            fee_qty=Decimal("0"),
        )
        lot.apply_fee()
        assert lot.remaining_qty == Decimal("1")


class TestPositionLedger:
    def test_add_lot_creates_lot(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        lot = ledger.add_lot(Decimal("1"), Decimal("50000"))
        assert lot.remaining_qty == Decimal("1")
        assert lot.fill_price == Decimal("50000")
        assert ledger.total_qty == Decimal("1")

    def test_add_lot_with_fee(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        lot = ledger.add_lot(Decimal("1"), Decimal("50000"), fee_qty=Decimal("0.001"))
        assert lot.remaining_qty == Decimal("0.999")

    def test_reduce_fifo_order(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1"), Decimal("100"))
        ledger.add_lot(Decimal("1"), Decimal("200"))

        realized, reduced = ledger.reduce(Decimal("1.5"), Decimal("300"))
        assert realized == (Decimal("300") - Decimal("100")) * 1 + (Decimal("300") - Decimal("200")) * Decimal("0.5")
        assert len(reduced) == 2
        assert ledger.total_qty == Decimal("0.5")

    def test_reduce_empty_ledger_logs_warning(self, caplog):
        import logging
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        with caplog.at_level(logging.WARNING):
            realized, reduced = ledger.reduce(Decimal("1"), Decimal("100"))
        assert realized == Decimal("0")
        assert len(reduced) == 0
        assert "empty ledger" in caplog.text

    def test_reduce_zero_quantity_raises(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        with pytest.raises(ValueError):
            ledger.reduce(Decimal("0"), Decimal("100"))

    def test_reduce_closes_lot_fully(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1"), Decimal("100"))
        realized, reduced = ledger.reduce(Decimal("1"), Decimal("200"))
        assert ledger.total_qty == Decimal("0")
        assert ledger.status == PositionStatus.CLOSED
        assert len(ledger.closed_lots) == 1

    def test_avg_cost_calculation(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            cost_basis_method=CostBasisMethod.AVERAGE_COST,
        )
        ledger.add_lot(Decimal("1"), Decimal("100"))
        ledger.add_lot(Decimal("1"), Decimal("200"))
        assert ledger.avg_cost == Decimal("150")

    def test_update_unrealized(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1"), Decimal("100"))
        ledger.update_unrealized(Decimal("150"))
        assert ledger.unrealized_pnl == Decimal("50")

    def test_to_summary_dict(self):
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1"), Decimal("100"))
        d = ledger.to_summary_dict()
        assert d["strategy_id"] == "strat_a"
        assert d["symbol"] == "BTCUSDT"
        assert d["total_qty"] == "1"


class TestBrokerPosition:
    def test_available_quantity(self):
        bp = BrokerPosition(
            symbol="BTCUSDT",
            quantity=Decimal("10"),
            frozen_quantity=Decimal("3"),
        )
        assert bp.available_quantity == Decimal("7")

    def test_auto_decimal_conversion(self):
        bp = BrokerPosition(symbol="BTCUSDT", quantity="10", avg_price="50000")
        assert isinstance(bp.quantity, Decimal)
        assert bp.quantity == Decimal("10")


class TestPositionReconciliation:
    def test_consistent_status(self):
        r = PositionReconciliation(
            symbol="BTCUSDT",
            broker_quantity=Decimal("10"),
            ledger_quantity=Decimal("10"),
            difference=Decimal("0"),
            status="CONSISTENT",
        )
        assert r.status == "CONSISTENT"

    def test_discrepancy_status(self):
        r = PositionReconciliation(
            symbol="BTCUSDT",
            broker_quantity=Decimal("10"),
            ledger_quantity=Decimal("9"),
            difference=Decimal("1"),
            status="DISCREPANCY",
        )
        assert r.status == "DISCREPANCY"
