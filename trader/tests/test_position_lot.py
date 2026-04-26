"""
test_position_lot - PositionLedger 单元测试（Batch 1）
=========================================================
覆盖场景：
- add_lot：开仓、加仓、费用扣除
- reduce：FIFO 顺序、部分成交、全部平仓
- avg_cost：加权平均成本计算
- 空仓 / 边界条件
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from trader.core.domain.models.position import (
    PositionLot,
    PositionLedger,
    PositionStatus,
    CostBasisMethod,
)


class TestPositionLot:
    def test_apply_fee_deducts_remaining_qty(self):
        """手续费从 remaining_qty 中扣除"""
        lot = PositionLot(
            lot_id="lot1",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            original_qty=Decimal("1.0"),
            remaining_qty=Decimal("1.0"),
            fill_price=Decimal("65000"),
            fee_qty=Decimal("0.0005"),
            fee_asset="BTC",
        )
        lot.apply_fee()
        assert lot.remaining_qty == Decimal("0.9995")

    def test_apply_fee_zero_fee(self):
        """零手续费不改变 remaining_qty"""
        lot = PositionLot(
            lot_id="lot1",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            original_qty=Decimal("1.0"),
            remaining_qty=Decimal("1.0"),
            fill_price=Decimal("65000"),
            fee_qty=Decimal("0"),
        )
        lot.apply_fee()
        assert lot.remaining_qty == Decimal("1.0")

    def test_apply_fee_insufficient_qty(self):
        """手续费 > remaining_qty 时不扣除（保持原值）"""
        lot = PositionLot(
            lot_id="lot1",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            original_qty=Decimal("0.0001"),
            remaining_qty=Decimal("0.0001"),
            fill_price=Decimal("65000"),
            fee_qty=Decimal("0.0005"),
        )
        lot.apply_fee()
        assert lot.remaining_qty == Decimal("0.0001")


class TestPositionLedger:
    def test_add_lot_creates_open_lot(self):
        """开仓创建新批次"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        lot = ledger.add_lot(Decimal("1.0"), Decimal("65000"))
        assert len(ledger.lots) == 1
        assert ledger.total_qty == Decimal("1.0")
        assert lot.fill_price == Decimal("65000")
        assert lot.is_closed is False
        assert ledger.status == PositionStatus.ACTIVE

    def test_add_lot_with_fee(self):
        """带手续费的批次创建"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        lot = ledger.add_lot(
            quantity=Decimal("1.0"),
            fill_price=Decimal("65000"),
            fee_qty=Decimal("0.0005"),
            fee_asset="BTC",
        )
        assert lot.original_qty == Decimal("1.0")
        assert lot.remaining_qty == Decimal("0.9995")
        assert ledger.total_qty == Decimal("0.9995")

    def test_add_multiple_lots(self):
        """多次买入产生多个批次"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        t = datetime.now(timezone.utc)
        ledger.add_lot(Decimal("1.0"), Decimal("65000"), filled_at=t)
        ledger.add_lot(Decimal("0.5"), Decimal("66000"), filled_at=t + timedelta(minutes=1))
        assert len(ledger.lots) == 2
        assert ledger.total_qty == Decimal("1.5")

    def test_reduce_fifo_order(self):
        """FIFO 顺序扣减：先买的批次先扣"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        t = datetime.now(timezone.utc)
        ledger.add_lot(Decimal("1.0"), Decimal("65000"), filled_at=t)
        ledger.add_lot(Decimal("1.0"), Decimal("66000"), filled_at=t + timedelta(minutes=1))

        # 卖出 0.5 BTC，扣减第一个批次
        realized, reduced = ledger.reduce(Decimal("0.5"), Decimal("65500"))

        # (65500 - 65000) * 0.5 = 250
        assert realized == Decimal("250")
        assert len(reduced) == 1
        assert reduced[0][1] == Decimal("0.5")
        assert ledger.total_qty == Decimal("1.5")

    def test_reduce_across_multiple_lots(self):
        """卖出量跨越多个批次"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        t = datetime.now(timezone.utc)
        ledger.add_lot(Decimal("1.0"), Decimal("65000"), filled_at=t)
        ledger.add_lot(Decimal("1.0"), Decimal("66000"), filled_at=t + timedelta(minutes=1))

        # 卖出 1.5 BTC：0.5 来自第一个 lot，1.0 来自第二个 lot
        realized, reduced = ledger.reduce(Decimal("1.5"), Decimal("67000"))

        # lot0: (67000-65000)*0.5 = 1000
        # lot1: (67000-66000)*1.0 = 1000
        assert realized == Decimal("2500")
        assert len(reduced) == 2

    def test_reduce_full_close(self):
        """全部平仓"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1.0"), Decimal("65000"))

        realized, reduced = ledger.reduce(Decimal("1.0"), Decimal("67000"))
        assert realized == Decimal("2000")
        assert ledger.total_qty == Decimal("0")
        assert ledger.status == PositionStatus.CLOSED
        assert len(ledger.lots) == 0
        assert len(ledger.closed_lots) == 1

    def test_reduce_exceeds_position(self):
        """卖出量超过持仓时，实际卖出量 = 持仓量"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1.0"), Decimal("65000"))

        realized, reduced = ledger.reduce(Decimal("2.0"), Decimal("67000"))
        assert realized == Decimal("2000")
        assert len(reduced) == 1
        assert reduced[0][1] == Decimal("1.0")

    def test_avg_cost_averages_all_open_lots(self):
        """AVERAGE_COST 模式下，avg_cost 是所有 open lot 的加权平均"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            cost_basis_method=CostBasisMethod.AVERAGE_COST,
        )
        t = datetime.now(timezone.utc)
        ledger.add_lot(Decimal("2.0"), Decimal("65000"), filled_at=t)
        ledger.add_lot(Decimal("1.0"), Decimal("67000"), filled_at=t + timedelta(minutes=1))

        # (2*65000 + 1*67000) / 3 = 65666.67
        expected = (Decimal("2") * Decimal("65000") + Decimal("1") * Decimal("67000")) / Decimal("3")
        assert ledger.avg_cost == expected

    def test_avg_cost_zero_when_empty(self):
        """空仓时 avg_cost = 0"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        assert ledger.avg_cost == Decimal("0")

    def test_cost_basis_method_fifo_returns_zero_avg_cost(self):
        """FIFO 方法下 avg_cost 返回 0"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            cost_basis_method=CostBasisMethod.FIFO,
        )
        ledger.add_lot(Decimal("1.0"), Decimal("65000"))
        assert ledger.avg_cost == Decimal("0")

    def test_update_unrealized_pnl(self):
        """按当前价格更新未实现盈亏"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
            cost_basis_method=CostBasisMethod.AVERAGE_COST,
        )
        ledger.add_lot(Decimal("1.0"), Decimal("65000"))
        ledger.update_unrealized(Decimal("66000"))
        assert ledger.unrealized_pnl == Decimal("1000")

    def test_update_unrealized_zero_when_empty(self):
        """空仓时未实现盈亏 = 0"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.update_unrealized(Decimal("66000"))
        assert ledger.unrealized_pnl == Decimal("0")

    def test_to_summary_dict(self):
        """序列化为摘要字典"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1.0"), Decimal("65000"))
        summary = ledger.to_summary_dict()
        assert summary["strategy_id"] == "strat_a"
        assert summary["symbol"] == "BTCUSDT"
        assert summary["total_qty"] == "1.0"
        assert summary["lot_count"] == 1
        assert summary["status"] == "ACTIVE"

    def test_realized_pnl_accumulates_across_reduces(self):
        """多次卖出时 realized_pnl 累加"""
        ledger = PositionLedger(
            position_id="strat_a:BTCUSDT",
            strategy_id="strat_a",
            symbol="BTCUSDT",
        )
        ledger.add_lot(Decimal("1.0"), Decimal("65000"))

        realized1, _ = ledger.reduce(Decimal("0.4"), Decimal("66000"))
        realized2, _ = ledger.reduce(Decimal("0.4"), Decimal("67000"))

        assert ledger.realized_pnl == realized1 + realized2
        assert realized1 == Decimal("400")
        assert realized2 == Decimal("800")


# ==================== PositionLedgerManager Tests ====================

from trader.core.domain.models.position_lot_manager import (
    PositionLedgerManager,
    ReconciliationReport,
)


class TestPositionLedgerManager:
    def test_on_fill_buy_creates_lot_event(self):
        """成交 BUY 返回 POSITION_LOT_OPENED 事件"""
        manager = PositionLedgerManager()
        events = manager.on_fill(
            "strat_a", "BTCUSDT", "BUY",
            Decimal("1.0"), Decimal("65000"),
        )
        assert len(events) == 2  # lot_opened + strategy_position_updated
        assert events[0].event_type.value == "POSITION_LOT_OPENED"
        assert events[0].data["strategy_id"] == "strat_a"
        assert events[0].data["symbol"] == "BTCUSDT"

    def test_on_fill_sell_reduces_lot_event(self):
        """成交 SELL 返回 POSITION_LOT_REDUCED 事件"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        events = manager.on_fill(
            "strat_a", "BTCUSDT", "SELL",
            Decimal("0.5"), Decimal("66000"),
        )
        # 1 × REDUCED + 1 × STRATEGY_POSITION_UPDATED
        assert len(events) == 2
        assert events[0].event_type.value == "POSITION_LOT_REDUCED"
        assert events[0].data["reduce_qty"] == Decimal("0.5")

    def test_on_fill_sell_full_close_event(self):
        """全部平仓返回 POSITION_LOT_CLOSED 事件"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        events = manager.on_fill(
            "strat_a", "BTCUSDT", "SELL",
            Decimal("1.0"), Decimal("67000"),
        )
        assert events[0].event_type.value == "POSITION_LOT_CLOSED"
        assert events[0].data["total_realized_pnl"] == Decimal("2000")

    def test_on_fill_buy_updates_strategy_position_event(self):
        """每次成交都更新策略持仓汇总事件"""
        manager = PositionLedgerManager()
        events = manager.on_fill(
            "strat_a", "BTCUSDT", "BUY",
            Decimal("1.0"), Decimal("65000"),
        )
        strat_evt = events[1]
        assert strat_evt.event_type.value == "STRATEGY_POSITION_UPDATED"
        assert strat_evt.data["total_qty"] == Decimal("1.0")
        assert strat_evt.data["avg_cost"] == Decimal("65000")

    def test_multiple_strategies_isolated(self):
        """不同策略的 Ledger 相互隔离"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        manager.on_fill("strat_b", "BTCUSDT", "BUY", Decimal("2.0"), Decimal("64000"))

        a_ledger = manager.get("strat_a", "BTCUSDT")
        b_ledger = manager.get("strat_b", "BTCUSDT")
        assert a_ledger.total_qty == Decimal("1.0")
        assert b_ledger.total_qty == Decimal("2.0")

    def test_reconcile_consistent(self):
        """Broker qty == OMS qty 时状态为 CONSISTENT"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        report = manager.reconcile("strat_a", "BTCUSDT", Decimal("1.0"))
        assert report.status == "CONSISTENT"
        assert report.difference == Decimal("0")

    def test_reconcile_discrepancy(self):
        """Broker qty != OMS qty 时状态为 DISCREPANCY"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        report = manager.reconcile("strat_a", "BTCUSDT", Decimal("0.5"))
        assert report.status == "DISCREPANCY"
        assert report.difference == Decimal("-0.5")

    def test_reconcile_within_tolerance(self):
        """Broker 与 OMS 差异在容忍度内视为 CONSISTENT"""
        manager = PositionLedgerManager(default_tolerance=Decimal("0.01"))
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("100.0"), Decimal("100"))
        # broker = 101, oms = 100, diff = 1, ratio = 0.01 (边界)
        report = manager.reconcile("strat_a", "BTCUSDT", Decimal("101"))
        assert report.status == "CONSISTENT"

    def test_get_strategy_position_summary(self):
        """返回策略下所有标的的持仓摘要"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        manager.on_fill("strat_a", "ETHUSDT", "BUY", Decimal("5.0"), Decimal("3000"))
        summary = manager.get_strategy_position_summary("strat_a")
        assert len(summary) == 2
        symbols = {s["symbol"] for s in summary}
        assert symbols == {"BTCUSDT", "ETHUSDT"}

    def test_get_total_exposure(self):
        """计算名义敞口"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("2.0"), Decimal("65000"))
        exposure = manager.get_total_exposure(
            "strat_a", "BTCUSDT", Decimal("66000")
        )
        assert exposure == Decimal("132000")

    def test_list_active_only(self):
        """只列出 ACTIVE 状态的 Ledger"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        manager.on_fill("strat_a", "ETHUSDT", "BUY", Decimal("5.0"), Decimal("3000"))
        # 关闭 BTCUSDT
        manager.on_fill("strat_a", "BTCUSDT", "SELL", Decimal("1.0"), Decimal("67000"))
        active = manager.list_active()
        symbols = {l.symbol for l in active}
        assert "ETHUSDT" in symbols
        assert "BTCUSDT" not in symbols

