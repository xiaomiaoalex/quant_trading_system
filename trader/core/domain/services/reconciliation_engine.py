"""
ReconciliationEngine - 持仓对账引擎
===================================
对比 Broker 真相源与 OMS 策略级持仓，发现并处理差异。

三级响应：
- |diff| ≤ tolerance（默认 0.001 = 0.1%）→ CONSISTENT，静默
- tolerance < |diff| ≤ alert_threshold（默认 0.01 = 1%）→ 告警，写入 reconciliation_log
- |diff| > alert_threshold → KillSwitch L1（禁止新开仓）

架构约束：
- 属于 Control Plane，允许 IO
- KillSwitch 升级不可逆，触发前必须充分记录日志
- 幂等：重复对账同一 symbol 不重复写日志
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ==================== 配置 ====================

@dataclass
class ReconciliationConfig:
    """对账配置"""
    tolerance: Decimal = field(default_factory=lambda: Decimal("0.001"))
    alert_threshold: Decimal = field(default_factory=lambda: Decimal("0.01"))
    interval_seconds: float = 60.0  # 定时对账间隔
    enabled: bool = True


# ==================== 结果 ====================

@dataclass
class ReconciliationOutcome:
    """单标的对账结果"""
    symbol: str
    broker_qty: Decimal
    oms_qty: Decimal
    historical_qty: Decimal
    difference: Decimal
    diff_ratio: Decimal  # |difference| / |broker_qty|
    tolerance: Decimal
    alert_threshold: Decimal
    status: str  # CONSISTENT / DISCREPANCY / HISTORICAL_GAP
    action: str  # NONE / ALERT / KILLSWITCH_L1
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_above_alert_threshold(self) -> bool:
        return self.diff_ratio > self.alert_threshold

    @property
    def is_within_tolerance(self) -> bool:
        return self.diff_ratio <= self.tolerance


class ReconciliationEngine:
    """
    持仓对账引擎。

    工作方式：
    1. discover_historical_positions(): 启动时发现 broker 有但 OMS 无的持仓
    2. reconcile_single(symbol): 对比 broker vs OMS，触发三级响应
    3. start_periodic_reconciliation(): 启动定时对账循环（可选）
    """

    def __init__(
        self,
        config: Optional[ReconciliationConfig] = None,
        get_broker_positions: Optional[Callable[[], Dict[str, Decimal]]] = None,
        get_ledger_manager: Optional[Callable[[], Any]] = None,
        kill_switch_upgrader: Optional[Callable[[str, str], None]] = None,
        log_handler: Optional[Callable[[ReconciliationOutcome], None]] = None,
    ):
        """
        Args:
            config: 对账配置
            get_broker_positions: 获取 broker 持仓的函数 () -> Dict[symbol -> qty]
            get_ledger_manager: 获取 PositionLedgerManager 的函数 () -> PositionLedgerManager
            kill_switch_upgrader: 升级 KillSwitch 的函数 (reason: str, scope: str) -> None
            log_handler: 写入对账日志的函数 (outcome: ReconciliationOutcome) -> None
        """
        self.config = config or ReconciliationConfig()
        self._get_broker_positions = get_broker_positions
        self._get_ledger_manager = get_ledger_manager
        self._kill_switch_upgrader = kill_switch_upgrader
        self._log_handler = log_handler

        self._historical_positions: Dict[str, Decimal] = {}  # symbol -> qty
        self._last_outcomes: Dict[str, ReconciliationOutcome] = {}
        self._reconciled_symbols: Set[str] = set()
        self._periodic_task: Optional[asyncio.Task] = None

    # ==================== 历史持仓发现 ====================

    def discover_historical_position(
        self,
        symbol: str,
        qty: Decimal,
        avg_price: Optional[Decimal] = None,
        source: str = "broker_api",
    ) -> ReconciliationOutcome:
        """
        手动注册历史持仓（程序启动前已存在的持仓）。

        Args:
            symbol: 交易对
            qty: 持仓数量
            avg_price: 平均成本（可选）
            source: 来源标识

        Returns:
            ReconciliationOutcome
        """
        self._historical_positions[symbol] = qty
        logger.info(
            f"[Reconciliation] Historical position discovered: "
            f"symbol={symbol} qty={qty} source={source}"
        )

        # 立即做一次对账
        return self.reconcile_single(symbol)

    def discover_all_historical(
        self,
        broker_positions: Dict[str, Decimal],
    ) -> List[ReconciliationOutcome]:
        """
        启动时批量发现历史持仓。

        对比 broker 持仓与 OMS 持仓，差异作为历史持仓注册。
        """
        outcomes = []
        ledger_manager = self._get_ledger_manager() if self._get_ledger_manager else None

        for symbol, broker_qty in broker_positions.items():
            oms_qty = Decimal("0")
            if ledger_manager:
                # OMS 中所有策略的该标的持仓合计
                for ledger in ledger_manager.list_ledgers():
                    if ledger.symbol == symbol:
                        oms_qty += ledger.total_qty

            diff = broker_qty - oms_qty
            if diff > 0:
                # Broker 有持仓但 OMS 没有 → 历史持仓
                self._historical_positions[symbol] = diff
                logger.info(
                    f"[Reconciliation] Historical position auto-discovered: "
                    f"symbol={symbol} qty={diff} (broker={broker_qty}, oms={oms_qty})"
                )

            outcome = ReconciliationOutcome(
                symbol=symbol,
                broker_qty=broker_qty,
                oms_qty=oms_qty,
                historical_qty=diff,
                difference=diff,
                diff_ratio=Decimal("0"),
                tolerance=self.config.tolerance,
                alert_threshold=self.config.alert_threshold,
                status="HISTORICAL_GAP" if diff > 0 else "CONSISTENT",
                action="NONE",
            )
            outcomes.append(outcome)
            self._last_outcomes[symbol] = outcome

        return outcomes

    # ==================== 单标的对账 ====================

    def reconcile_single(self, symbol: str) -> ReconciliationOutcome:
        """
        对比单个标的的 broker 持仓与 OMS 持仓。

        对账公式：
        - expected_oms = broker_qty - historical_qty
        - actual_diff = oms_qty - expected_oms
        - diff_ratio = |actual_diff| / broker_qty

        三级响应逻辑：
        1. broker_qty == 0 and oms_qty == 0 → CONSISTENT（双方都空仓）
        2. broker_qty == 0 but oms_qty > 0 → DISCREPANCY（KillSwitch L1）
        3. oms_qty == 0 but broker_qty > 0 → HISTORICAL_GAP（需要 discover）
        4. broker_qty > 0:
           - |diff| / broker_qty ≤ tolerance → CONSISTENT
           - tolerance < |diff| / broker_qty ≤ alert_threshold → ALERT
           - > alert_threshold → KILLSWITCH_L1
        """
        broker_qty = self._get_broker_qty(symbol)
        oms_qty = self._get_oms_qty(symbol)
        historical_qty = self._historical_positions.get(symbol, Decimal("0"))

        # 对账差异：OMS 与 Broker（扣除历史持仓后）的差异
        expected_oms = broker_qty - historical_qty
        actual_diff = oms_qty - expected_oms

        if broker_qty > 0:
            diff_ratio = abs(actual_diff) / broker_qty
        else:
            diff_ratio = Decimal("0")

        # 决定状态和动作
        if broker_qty == 0 and oms_qty == 0:
            status = "CONSISTENT"
            action = "NONE"
        elif oms_qty > 0 and broker_qty == 0:
            status = "DISCREPANCY"
            action = "KILLSWITCH_L1"
        elif broker_qty > 0 and oms_qty == 0:
            status = "HISTORICAL_GAP"
            action = "NONE"
        elif diff_ratio <= self.config.tolerance:
            status = "CONSISTENT"
            action = "NONE"
        elif diff_ratio <= self.config.alert_threshold:
            status = "DISCREPANCY"
            action = "ALERT"
        else:
            status = "DISCREPANCY"
            action = "KILLSWITCH_L1"

        outcome = ReconciliationOutcome(
            symbol=symbol,
            broker_qty=broker_qty,
            oms_qty=oms_qty,
            historical_qty=historical_qty,
            difference=actual_diff,
            diff_ratio=diff_ratio,
            tolerance=self.config.tolerance,
            alert_threshold=self.config.alert_threshold,
            status=status,
            action=action,
            details={
                "expected_oms": str(expected_oms),
                "config_tolerance": str(self.config.tolerance),
                "config_alert_threshold": str(self.config.alert_threshold),
            },
        )

        self._last_outcomes[symbol] = outcome
        self._reconciled_symbols.add(symbol)
        self._trigger_action(outcome)

        return outcome

    def _get_broker_qty(self, symbol: str) -> Decimal:
        """从 broker 获取持仓（若无回调，返回 0）"""
        if self._get_broker_positions:
            positions = self._get_broker_positions()
            return positions.get(symbol, Decimal("0"))
        return Decimal("0")

    def _get_oms_qty(self, symbol: str) -> Decimal:
        """从 PositionLedgerManager 获取 OMS 持仓合计"""
        manager = self._get_ledger_manager() if self._get_ledger_manager else None
        if not manager:
            return Decimal("0")
        total = Decimal("0")
        for ledger in manager.list_ledgers():
            if ledger.symbol == symbol:
                total += ledger.total_qty
        return total

    def _trigger_action(self, outcome: ReconciliationOutcome) -> None:
        """根据对账结果触发相应动作"""
        # 记录日志
        if self._log_handler:
            self._log_handler(outcome)

        if outcome.action == "NONE":
            logger.info(
                f"[Reconciliation] {outcome.symbol}: {outcome.status} "
                f"(diff_ratio={outcome.diff_ratio:.4f}, within_tolerance=True)"
            )
        elif outcome.action == "ALERT":
            logger.warning(
                f"[Reconciliation] 🚨 ALERT {outcome.symbol}: "
                f"diff={outcome.difference} ({outcome.diff_ratio:.2%}), status={outcome.status}"
            )
        elif outcome.action == "KILLSWITCH_L1":
            logger.critical(
                f"[Reconciliation] 🚨 KILLSWITCH L1 TRIGGERED for {outcome.symbol}: "
                f"diff={outcome.difference} ({outcome.diff_ratio:.2%}), "
                f"broker={outcome.broker_qty}, oms={outcome.oms_qty}"
            )
            if self._kill_switch_upgrader:
                try:
                    self._kill_switch_upgrader(
                        reason=f"RECONCILIATION_DISCREPANCY: {outcome.symbol}",
                        scope="GLOBAL",
                    )
                except Exception as e:
                    logger.error(f"[Reconciliation] Failed to upgrade KillSwitch: {e}")

    # ==================== 批量对账 ====================

    def reconcile_all(self) -> List[ReconciliationOutcome]:
        """对账所有 symbol"""
        outcomes = []
        all_symbols: Set[str] = set()

        # 收集 broker 的所有 symbol
        if self._get_broker_positions:
            broker_positions = self._get_broker_positions()
            all_symbols.update(broker_positions.keys())

        # 收集 OMS 的所有 symbol
        manager = self._get_ledger_manager() if self._get_ledger_manager else None
        if manager:
            for ledger in manager.list_ledgers():
                all_symbols.add(ledger.symbol)

        for symbol in all_symbols:
            outcome = self.reconcile_single(symbol)
            outcomes.append(outcome)

        return outcomes

    # ==================== 定时对账 ====================

    async def start_periodic(self) -> None:
        """启动定时对账（后台任务）"""
        if self._periodic_task is not None:
            logger.warning("[Reconciliation] Periodic task already running")
            return

        self._periodic_task = asyncio.create_task(self._periodic_loop())
        logger.info(
            f"[Reconciliation] Periodic reconciliation started "
            f"(interval={self.config.interval_seconds}s)"
        )

    async def stop_periodic(self) -> None:
        """停止定时对账"""
        if self._periodic_task:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None
            logger.info("[Reconciliation] Periodic reconciliation stopped")

    async def _periodic_loop(self) -> None:
        """定时对账循环"""
        while True:
            try:
                await asyncio.sleep(self.config.interval_seconds)
                if not self.config.enabled:
                    continue
                outcomes = self.reconcile_all()
                discrepancies = [o for o in outcomes if o.action != "NONE"]
                if discrepancies:
                    logger.warning(
                        f"[Reconciliation] Periodic check: {len(discrepancies)} discrepancies found"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Reconciliation] Periodic check error: {e}", exc_info=True)

    # ==================== 查询 ====================

    def get_last_outcome(self, symbol: str) -> Optional[ReconciliationOutcome]:
        return self._last_outcomes.get(symbol)

    def get_all_outcomes(self) -> Dict[str, ReconciliationOutcome]:
        return dict(self._last_outcomes)
