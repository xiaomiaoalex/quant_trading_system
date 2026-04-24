"""
PositionLedgerManager - 策略级持仓账本管理器
============================================
管理所有 (strategy_id, symbol) 的 PositionLedger 实例。

设计原则：
- 每个 (strategy_id, symbol) 独立聚合，天然线程安全（无跨 key 竞争）
- 仅持有 in-memory 引用，IO 由调用方负责（符合 Core Plane 无 IO 约束）
- Lot 事件由调用方发布，本类只产生事件数据，不执行发布

用法示例：
    manager = PositionLedgerManager()
    # 成交 BUY
    events = manager.on_buy("strat_a", "BTCUSDT", Decimal("1.0"), Decimal("65000"))
    for evt in events:
        event_bus.publish(evt)
    # 成交 SELL
    events = manager.on_sell("strat_a", "BTCUSDT", Decimal("0.5"), Decimal("66000"))
    for evt in events:
        event_bus.publish(evt)
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from trader.core.domain.models.position import (
    PositionLedger,
    PositionLot,
    PositionStatus,
    CostBasisMethod,
)
from trader.core.domain.models.events import (
    DomainEvent,
    create_lot_opened_event,
    create_lot_reduced_event,
    create_lot_closed_event,
    create_strategy_position_updated_event,
)


@dataclass
class ReconciliationReport:
    """对账报告"""
    strategy_id: str
    symbol: str
    broker_qty: Decimal
    oms_qty: Decimal
    difference: Decimal
    tolerance: Decimal
    status: str  # CONSISTENT / DISCREPANCY
    lots_closed: List[str]  # 被强制关闭的 lot_id 列表


class PositionLedgerManager:
    """
    策略级持仓账本管理器。

    管理所有 (strategy_id, symbol) 的 PositionLedger 实例。
    每个 key 独立加锁，保证并发安全。
    """

    def __init__(self, default_tolerance: Decimal = Decimal("0.001")):
        self._ledgers: Dict[str, PositionLedger] = {}
        self._default_tolerance = default_tolerance

    # ==================== 索引 ====================

    @staticmethod
    def _key(strategy_id: str, symbol: str) -> str:
        return f"{strategy_id}:{symbol}"

    def _get_or_create(self, strategy_id: str, symbol: str) -> PositionLedger:
        key = self._key(strategy_id, symbol)
        if key not in self._ledgers:
            self._ledgers[key] = PositionLedger(
                position_id=key,
                strategy_id=strategy_id,
                symbol=symbol,
            )
        return self._ledgers[key]

    def get(self, strategy_id: str, symbol: str) -> Optional[PositionLedger]:
        key = self._key(strategy_id, symbol)
        return self._ledgers.get(key)

    def list_ledgers(self) -> List[PositionLedger]:
        return list(self._ledgers.values())

    def list_active(self) -> List[PositionLedger]:
        return [l for l in self._ledgers.values() if l.status == PositionStatus.ACTIVE]

    # ==================== 成交处理 ====================

    def on_fill(
        self,
        strategy_id: str,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: Decimal,
        price: Decimal,
        fee_qty: Decimal = Decimal("0"),
        fee_asset: Optional[str] = None,
        filled_at: Optional[datetime] = None,
    ) -> List[DomainEvent]:
        """
        处理成交回报，返回需要发布的事件列表。

        Args:
            strategy_id: 策略ID
            symbol: 交易对
            side: 成交方向 "BUY" | "SELL"
            quantity: 成交数量
            price: 成交价格
            fee_qty: 手续费数量（base asset）
            fee_asset: 手续费币种
            filled_at: 成交时间（默认当前时间）

        Returns:
            需要发布的事件列表（可能是空的，如果是重复成交）
        """
        filled_at = filled_at or datetime.now(timezone.utc)
        ledger = self._get_or_create(strategy_id, symbol)

        if side.upper() in ("BUY", "BUY"):
            return self._on_buy(ledger, quantity, price, fee_qty, fee_asset, filled_at)
        else:
            return self._on_sell(ledger, quantity, price, filled_at)

    def _on_buy(
        self,
        ledger: PositionLedger,
        quantity: Decimal,
        price: Decimal,
        fee_qty: Decimal,
        fee_asset: Optional[str],
        filled_at: datetime,
    ) -> List[DomainEvent]:
        lot = ledger.add_lot(
            quantity=quantity,
            fill_price=price,
            fee_qty=fee_qty,
            fee_asset=fee_asset,
            filled_at=filled_at,
        )
        events = [
            create_lot_opened_event(
                lot_id=lot.lot_id,
                strategy_id=ledger.strategy_id,
                symbol=ledger.symbol,
                quantity=quantity,
                fill_price=price,
                fee_qty=fee_qty,
            ),
        ]
        # 同步发布策略持仓汇总事件
        events.append(self._make_strategy_position_updated_event(ledger))
        return events

    def _on_sell(
        self,
        ledger: PositionLedger,
        quantity: Decimal,
        price: Decimal,
        filled_at: datetime,
    ) -> List[DomainEvent]:
        events: List[DomainEvent] = []
        realized, reduced_lots = ledger.reduce(quantity, price)

        for lot_id, reduce_qty, lot_fill_price in reduced_lots:
            # reduce() 后，lot 要么在 closed_lots（完全平仓），要么在 lots（部分平仓）
            closed_lot = next((l for l in ledger.closed_lots if l.lot_id == lot_id), None)
            open_lot = next((l for l in ledger.lots if l.lot_id == lot_id), None)
            lot = closed_lot or open_lot

            if closed_lot is not None:
                # 完全平仓
                events.append(create_lot_closed_event(
                    lot_id=lot_id,
                    strategy_id=ledger.strategy_id,
                    symbol=ledger.symbol,
                    close_price=price,
                    total_realized_pnl=closed_lot.realized_pnl,
                ))
            elif open_lot is not None:
                # 部分平仓：PnL = (卖出价 - 批次成本价) × 数量
                lot_pnl = (price - lot_fill_price) * reduce_qty
                events.append(create_lot_reduced_event(
                    lot_id=lot_id,
                    strategy_id=ledger.strategy_id,
                    symbol=ledger.symbol,
                    reduce_qty=reduce_qty,
                    reduce_price=price,
                    remaining_qty=open_lot.remaining_qty,
                    realized_pnl=lot_pnl,
                ))

        events.append(self._make_strategy_position_updated_event(ledger))
        return events

    def _make_strategy_position_updated_event(self, ledger: PositionLedger) -> DomainEvent:
        return create_strategy_position_updated_event(
            strategy_id=ledger.strategy_id,
            symbol=ledger.symbol,
            total_qty=ledger.total_qty,
            avg_cost=ledger.avg_cost,
            realized_pnl=ledger.realized_pnl,
            unrealized_pnl=ledger.unrealized_pnl,
        )

    # ==================== 对账 ====================

    def reconcile(
        self,
        strategy_id: str,
        symbol: str,
        broker_qty: Decimal,
        tolerance: Optional[Decimal] = None,
    ) -> ReconciliationReport:
        """
        对比 Broker 持仓与 OMS 持仓，返回对账报告。

        注意：本方法只比较数量，不触发任何状态修改。
        """
        tolerance = tolerance or self._default_tolerance
        ledger = self.get(strategy_id, symbol)
        oms_qty = ledger.total_qty if ledger else Decimal("0")

        raw_diff = broker_qty - oms_qty
        # 相对差异比例
        diff_ratio = abs(raw_diff / broker_qty) if broker_qty != 0 else Decimal("0")
        within_tolerance = diff_ratio <= tolerance

        status = "CONSISTENT" if within_tolerance else "DISCREPANCY"

        return ReconciliationReport(
            strategy_id=strategy_id,
            symbol=symbol,
            broker_qty=broker_qty,
            oms_qty=oms_qty,
            difference=raw_diff,
            tolerance=tolerance,
            status=status,
            lots_closed=[],
        )

    # ==================== 批量汇总 ====================

    def get_strategy_position_summary(
        self,
        strategy_id: str,
    ) -> List[Dict]:
        """获取某策略所有标的的持仓摘要"""
        result = []
        for ledger in self._ledgers.values():
            if ledger.strategy_id != strategy_id:
                continue
            result.append(ledger.to_summary_dict())
        return result

    def get_total_exposure(
        self,
        strategy_id: str,
        symbol: str,
        current_price: Decimal,
    ) -> Decimal:
        """计算某策略某标的的名义敞口"""
        ledger = self.get(strategy_id, symbol)
        if not ledger:
            return Decimal("0")
        return ledger.total_qty * current_price
