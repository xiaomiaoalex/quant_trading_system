"""
test_oms_position_lot_integration - OMS + PositionLedgerManager 集成测试（Batch 2.4）
==================================================================================
覆盖场景：
- OMS 成交后 PositionLedgerManager 状态正确
- BUY → POSITION_LOT_OPENED 事件
- SELL → POSITION_LOT_REDUCED / LOT_CLOSED 事件
- realized_pnl 正确计算
- 多策略同币种隔离
"""
from decimal import Decimal
import pytest

from trader.core.domain.models.position_lot_manager import PositionLedgerManager
from trader.core.domain.models.events import EventType


class TestPositionLedgerManagerIntegration:
    def test_buy_then_sell_full_close(self):
        """完整闭环：买 → 卖 → 平仓 → realized_pnl"""
        manager = PositionLedgerManager()

        # BUY 1 BTC @ 65000
        buy_events = manager.on_fill(
            "strat_a", "BTCUSDT", "BUY",
            Decimal("1.0"), Decimal("65000"),
        )
        assert len(buy_events) == 2
        assert buy_events[0].event_type == EventType.POSITION_LOT_OPENED
        assert buy_events[1].event_type == EventType.STRATEGY_POSITION_UPDATED

        # SELL 1 BTC @ 67000
        sell_events = manager.on_fill(
            "strat_a", "BTCUSDT", "SELL",
            Decimal("1.0"), Decimal("67000"),
        )
        assert len(sell_events) == 2
        assert sell_events[0].event_type == EventType.POSITION_LOT_CLOSED

        ledger = manager.get("strat_a", "BTCUSDT")
        assert ledger.total_qty == Decimal("0")
        assert ledger.realized_pnl == Decimal("2000")
        assert ledger.status.value == "CLOSED"

    def test_fifo_order_pnl(self):
        """FIFO 顺序：先买的先扣，realized_pnl 按最早成本计算"""
        manager = PositionLedgerManager()
        t_base = None

        # BUY 10 @ 100
        events1 = manager.on_fill(
            "strat_a", "BTCUSDT", "BUY",
            Decimal("10"), Decimal("100"),
        )
        # BUY 10 @ 110
        events2 = manager.on_fill(
            "strat_a", "BTCUSDT", "BUY",
            Decimal("10"), Decimal("110"),
        )

        # SELL 10 @ 120（按 FIFO，应该扣减 10@100 的批次）
        sell_events = manager.on_fill(
            "strat_a", "BTCUSDT", "SELL",
            Decimal("10"), Decimal("120"),
        )

        ledger = manager.get("strat_a", "BTCUSDT")
        # (120-100)*10 = 200
        assert ledger.realized_pnl == Decimal("200")
        assert ledger.total_qty == Decimal("10")  # lot1 完全平仓，lot2 还剩 10

    def test_fifo_multiple_lots_partial(self):
        """多批次部分平仓：先扣最早批次"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("10"), Decimal("100"))
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("10"), Decimal("110"))

        # SELL 15（先扣 10@100，再扣 5@110）
        sell_events = manager.on_fill(
            "strat_a", "BTCUSDT", "SELL",
            Decimal("15"), Decimal("120"),
        )

        ledger = manager.get("strat_a", "BTCUSDT")
        # lot1: (120-100)*10 = 200
        # lot2: (120-110)*5 = 50
        # total = 250
        assert ledger.realized_pnl == Decimal("250")
        assert ledger.total_qty == Decimal("5")  # 还剩 5

        # 还剩的 5 应该来自第二个批次
        assert len(ledger.lots) == 1
        assert ledger.lots[0].remaining_qty == Decimal("5")

    def test_multi_strategy_same_symbol_isolated(self):
        """两策略同币种：持仓相互独立"""
        manager = PositionLedgerManager()

        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        manager.on_fill("strat_b", "BTCUSDT", "BUY", Decimal("2.0"), Decimal("64000"))

        a_ledger = manager.get("strat_a", "BTCUSDT")
        b_ledger = manager.get("strat_b", "BTCUSDT")

        assert a_ledger.total_qty == Decimal("1.0")
        assert b_ledger.total_qty == Decimal("2.0")
        assert a_ledger.avg_cost == Decimal("65000")
        assert b_ledger.avg_cost == Decimal("64000")

        # A 平仓
        manager.on_fill("strat_a", "BTCUSDT", "SELL", Decimal("1.0"), Decimal("66000"))
        assert manager.get("strat_a", "BTCUSDT").total_qty == Decimal("0")
        assert manager.get("strat_b", "BTCUSDT").total_qty == Decimal("2.0")

    def test_avg_cost_calculation(self):
        """avg_cost = 加权平均"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("2.0"), Decimal("65000"))
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("67000"))

        ledger = manager.get("strat_a", "BTCUSDT")
        # (2*65000 + 1*67000) / 3 = 65666.67
        expected = (Decimal("2") * Decimal("65000") + Decimal("1") * Decimal("67000")) / Decimal("3")
        assert ledger.avg_cost == expected

    def test_sell_exceeds_position(self):
        """卖出 > 持仓时，实际卖出 = 持仓量"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))

        events = manager.on_fill(
            "strat_a", "BTCUSDT", "SELL",
            Decimal("5.0"), Decimal("67000"),
        )

        ledger = manager.get("strat_a", "BTCUSDT")
        assert ledger.total_qty == Decimal("0")
        assert ledger.realized_pnl == Decimal("2000")  # (67000-65000)*1.0
        assert events[0].event_type == EventType.POSITION_LOT_CLOSED

    def test_fee_deducted_from_lot(self):
        """手续费从 remaining_qty 扣除"""
        manager = PositionLedgerManager()
        events = manager.on_fill(
            "strat_a", "BTCUSDT", "BUY",
            Decimal("1.0"), Decimal("65000"),
            fee_qty=Decimal("0.0005"),
            fee_asset="BTC",
        )

        ledger = manager.get("strat_a", "BTCUSDT")
        assert ledger.total_qty == Decimal("0.9995")
        assert ledger.lots[0].fee_qty == Decimal("0.0005")

    def test_list_active_only_returns_active(self):
        """list_active() 只返回 ACTIVE 状态的 Ledger"""
        manager = PositionLedgerManager()
        manager.on_fill("strat_a", "BTCUSDT", "BUY", Decimal("1.0"), Decimal("65000"))
        manager.on_fill("strat_b", "ETHUSDT", "BUY", Decimal("5.0"), Decimal("3000"))

        manager.on_fill("strat_a", "BTCUSDT", "SELL", Decimal("1.0"), Decimal("67000"))

        active = manager.list_active()
        symbols = {l.symbol for l in active}
        assert "BTCUSDT" not in symbols
        assert "ETHUSDT" in symbols

    def test_empty_sell_emits_strategy_update_for_observability(self):
        """空仓卖出仍发出 STRATEGY_POSITION_UPDATED 事件（用于可观测性）"""
        manager = PositionLedgerManager()
        events = manager.on_fill(
            "strat_a", "BTCUSDT", "SELL",
            Decimal("1.0"), Decimal("67000"),
        )
        # 无可平仓的 lot，但 STRATEGY_POSITION_UPDATED 仍发出（状态未变）
        assert len(events) == 1
        assert events[0].event_type == EventType.STRATEGY_POSITION_UPDATED
