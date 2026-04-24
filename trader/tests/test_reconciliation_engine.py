"""
test_reconciliation_engine - ReconciliationEngine 单元测试（Batch 2.4）
=====================================================================
覆盖场景：
- 三级响应：CONSISTENT / ALERT / KILLSWITCH_L1
- 历史持仓发现（启动时）
- tolerance 边界
- 批量对账
"""
from decimal import Decimal
import pytest

from trader.core.domain.models.position import (
    PositionLedger,
    PositionStatus,
)
from trader.core.domain.services.reconciliation_engine import (
    ReconciliationEngine,
    ReconciliationConfig,
    ReconciliationOutcome,
)


class _FakeLedgerManager:
    """模拟 PositionLedgerManager"""
    def __init__(self, ledgers: dict = None):
        self._ledgers: dict = ledgers or {}

    def list_ledgers(self):
        return list(self._ledgers.values())

    def add_ledger(self, strategy_id: str, symbol: str, qty: Decimal):
        key = f"{strategy_id}:{symbol}"
        ledger = PositionLedger(
            position_id=key,
            strategy_id=strategy_id,
            symbol=symbol,
        )
        ledger.add_lot(qty, Decimal("65000"))
        self._ledgers[key] = ledger
        return ledger


class TestReconciliationOutcome:
    def test_is_within_tolerance_true(self):
        cfg = ReconciliationConfig(
            tolerance=Decimal("0.01"),
            alert_threshold=Decimal("0.05"),
        )
        outcome = ReconciliationOutcome(
            symbol="BTCUSDT",
            broker_qty=Decimal("100"),
            oms_qty=Decimal("100"),
            historical_qty=Decimal("0"),
            difference=Decimal("0"),
            diff_ratio=Decimal("0"),
            tolerance=cfg.tolerance,
            alert_threshold=cfg.alert_threshold,
            status="CONSISTENT",
            action="NONE",
        )
        assert outcome.is_within_tolerance is True
        assert outcome.is_above_alert_threshold is False

    def test_is_above_alert_threshold_true(self):
        outcome = ReconciliationOutcome(
            symbol="BTCUSDT",
            broker_qty=Decimal("100"),
            oms_qty=Decimal("90"),
            historical_qty=Decimal("0"),
            difference=Decimal("10"),
            diff_ratio=Decimal("0.1"),  # 10%
            tolerance=Decimal("0.01"),
            alert_threshold=Decimal("0.05"),
            status="DISCREPANCY",
            action="KILLSWITCH_L1",
        )
        assert outcome.is_above_alert_threshold is True
        assert outcome.is_within_tolerance is False


class TestReconciliationEngine:
    def test_consistent_zero_positions(self):
        """broker=0, oms=0 → CONSISTENT"""
        engine = ReconciliationEngine()
        outcome = engine.reconcile_single("BTCUSDT")
        assert outcome.status == "CONSISTENT"
        assert outcome.action == "NONE"

    def test_consistent_within_tolerance(self):
        """|diff|/broker ≤ tolerance → CONSISTENT"""
        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("100"))

        def fake_broker():
            return {"BTCUSDT": Decimal("100.05")}

        engine = ReconciliationEngine(
            config=ReconciliationConfig(
                tolerance=Decimal("0.001"),
                alert_threshold=Decimal("0.01"),
            ),
            get_broker_positions=fake_broker,
            get_ledger_manager=lambda: ledger_mgr,
        )
        outcome = engine.reconcile_single("BTCUSDT")
        # diff = 0.05, ratio = 0.05/100.05 ≈ 0.0005 < 0.001
        assert outcome.status == "CONSISTENT"
        assert outcome.action == "NONE"

    def test_alert_above_tolerance_below_alert(self):
        """tolerance < |diff|/broker ≤ alert_threshold → ALERT"""
        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("100"))

        def fake_broker():
            return {"BTCUSDT": Decimal("105")}

        engine = ReconciliationEngine(
            config=ReconciliationConfig(
                tolerance=Decimal("0.001"),
                alert_threshold=Decimal("0.05"),
            ),
            get_broker_positions=fake_broker,
            get_ledger_manager=lambda: ledger_mgr,
        )
        outcome = engine.reconcile_single("BTCUSDT")
        # diff = 5, ratio = 5/105 ≈ 0.0476 < 0.05
        assert outcome.status == "DISCREPANCY"
        assert outcome.action == "ALERT"

    def test_killswitch_l1_triggered(self):
        """|diff|/broker > alert_threshold → KILLSWITCH_L1"""
        kill_called = []

        def fake_kill(reason, scope):
            kill_called.append((reason, scope))

        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("100"))

        def fake_broker():
            return {"BTCUSDT": Decimal("50")}  # OMS 比 broker 多 50

        engine = ReconciliationEngine(
            config=ReconciliationConfig(
                tolerance=Decimal("0.001"),
                alert_threshold=Decimal("0.05"),
            ),
            get_broker_positions=fake_broker,
            get_ledger_manager=lambda: ledger_mgr,
            kill_switch_upgrader=fake_kill,
        )
        outcome = engine.reconcile_single("BTCUSDT")
        assert outcome.status == "DISCREPANCY"
        assert outcome.action == "KILLSWITCH_L1"
        assert len(kill_called) == 1
        assert "BTCUSDT" in kill_called[0][0]

    def test_historical_gap_no_action(self):
        """broker 有但 oms 没有 → HISTORICAL_GAP（不触发告警）"""
        engine = ReconciliationEngine(
            config=ReconciliationConfig(),
            get_broker_positions=lambda: {"BTCUSDT": Decimal("10")},
            get_ledger_manager=lambda: _FakeLedgerManager(),
        )
        outcome = engine.reconcile_single("BTCUSDT")
        assert outcome.status == "HISTORICAL_GAP"
        assert outcome.action == "NONE"

    def test_discrepancy_broker_zero_oms_nonzero(self):
        """broker=0 但 oms>0 → DISCREPANCY + KILLSWITCH_L1"""
        kill_called = []

        def fake_kill(reason, scope):
            kill_called.append((reason, scope))

        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("5"))

        engine = ReconciliationEngine(
            config=ReconciliationConfig(),
            get_broker_positions=lambda: {"BTCUSDT": Decimal("0")},
            get_ledger_manager=lambda: ledger_mgr,
            kill_switch_upgrader=fake_kill,
        )
        outcome = engine.reconcile_single("BTCUSDT")
        assert outcome.status == "DISCREPANCY"
        assert outcome.action == "KILLSWITCH_L1"
        assert len(kill_called) == 1

    def test_discover_historical_position(self):
        """手动注册历史持仓"""
        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("1.0"))

        def fake_broker():
            return {"BTCUSDT": Decimal("1.5")}

        engine = ReconciliationEngine(
            get_broker_positions=fake_broker,
            get_ledger_manager=lambda: ledger_mgr,
        )
        outcome = engine.discover_historical_position(
            "BTCUSDT", Decimal("0.5"), source="manual"
        )
        assert engine._historical_positions["BTCUSDT"] == Decimal("0.5")
        assert outcome.status in ("CONSISTENT", "HISTORICAL_GAP")

    def test_discover_all_historical_auto(self):
        """批量发现历史持仓：broker>oms 时自动注册"""
        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("1.0"))

        broker_positions = {
            "BTCUSDT": Decimal("2.0"),  # broker 有 2.0, OMS 有 1.0 → 历史 1.0
            "ETHUSDT": Decimal("0"),    # broker 空仓
        }
        engine = ReconciliationEngine(
            get_broker_positions=lambda: broker_positions,
            get_ledger_manager=lambda: ledger_mgr,
        )
        outcomes = engine.discover_all_historical(broker_positions)

        assert engine._historical_positions["BTCUSDT"] == Decimal("1.0")
        assert "ETHUSDT" not in engine._historical_positions

    def test_reconcile_all(self):
        """批量对账多个标的"""
        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("1.0"))
        ledger_mgr.add_ledger("strat_a", "ETHUSDT", Decimal("5.0"))

        broker_positions = {
            "BTCUSDT": Decimal("1.0"),
            "ETHUSDT": Decimal("5.0"),
            "DOGEUSDT": Decimal("1000"),  # broker 有但 OMS 没有
        }
        engine = ReconciliationEngine(
            get_broker_positions=lambda: broker_positions,
            get_ledger_manager=lambda: ledger_mgr,
        )
        outcomes = engine.reconcile_all()
        symbols = {o.symbol for o in outcomes}
        assert symbols == {"BTCUSDT", "ETHUSDT", "DOGEUSDT"}
        consistent = [o for o in outcomes if o.status == "CONSISTENT"]
        assert len(consistent) == 2

    def test_tolerance_boundary_at_exact_tolerance(self):
        """差异正好等于 tolerance → 视为 CONSISTENT"""
        ledger_mgr = _FakeLedgerManager()
        ledger_mgr.add_ledger("strat_a", "BTCUSDT", Decimal("100"))

        # broker=101, ratio=1/101≈0.0099 ≈ tolerance(0.01)
        def fake_broker():
            return {"BTCUSDT": Decimal("101")}

        engine = ReconciliationEngine(
            config=ReconciliationConfig(
                tolerance=Decimal("0.01"),
                alert_threshold=Decimal("0.05"),
            ),
            get_broker_positions=fake_broker,
            get_ledger_manager=lambda: ledger_mgr,
        )
        outcome = engine.reconcile_single("BTCUSDT")
        assert outcome.status == "CONSISTENT"
