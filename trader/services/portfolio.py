"""
PortfolioService - 持仓服务
=========================
提供三层持仓查询：
- 账户级：账户总持仓（Broker API 真相源）
- 策略级：每个 strategy_id × symbol 的持仓（Lot 事件累积）
- Lot 级：每笔买入的精确记录

同时支持对账触发。
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    PositionView,
    PnlView,
    StrategyPositionView,
    LotView,
    PositionBreakdown,
    ReconciliationLogEntry,
    ReconciliationResult,
)
from trader.core.domain.services.position_lot_registry import get_lot_manager


class PortfolioService:
    """Service for portfolio positions, PnL, and strategy-level lot tracking"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    # ==================== 账户级（原有） ====================

    def list_positions(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> List[PositionView]:
        """Get positions"""
        positions = self._storage.list_positions(account_id, venue)
        return [PositionView(**p) for p in positions]

    def get_pnl(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> PnlView:
        """Get PnL summary"""
        pnl = self._storage.calculate_pnl(account_id, venue)
        return PnlView(**pnl)

    # ==================== 策略级持仓 ====================

    def get_strategy_positions(
        self,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[StrategyPositionView]:
        """
        获取策略级持仓列表。

        Args:
            strategy_id: 筛选策略（None = 所有策略）
            symbol: 筛选标的（None = 所有标的）

        Returns:
            List[StrategyPositionView]
        """
        manager = get_lot_manager()
        ledgers = manager.list_ledgers()

        results = []
        for ledger in ledgers:
            if strategy_id and ledger.strategy_id != strategy_id:
                continue
            if symbol and ledger.symbol != symbol:
                continue
            if ledger.total_qty <= 0 and not ledger.closed_lots:
                continue  # 跳过空仓且无历史记录的

            total_cost = str(ledger.total_qty * ledger.avg_cost) if ledger.total_qty > 0 else None
            results.append(StrategyPositionView(
                strategy_id=ledger.strategy_id,
                symbol=ledger.symbol,
                qty=str(ledger.total_qty),
                avg_cost=str(ledger.avg_cost),
                realized_pnl=str(ledger.realized_pnl),
                unrealized_pnl=str(ledger.unrealized_pnl),
                total_cost=total_cost,
                status=ledger.status.value,
                lot_count=len(ledger.lots),
                cost_basis_method=ledger.cost_basis_method.value,
                updated_at=ledger.updated_at.isoformat() if ledger.updated_at else None,
            ))

        return results

    def get_position_breakdown(
        self,
        symbol: str,
    ) -> PositionBreakdown:
        """
        获取单个标的的三层持仓分解。

        Args:
            symbol: 交易对

        Returns:
            PositionBreakdown：账户总持仓 + 策略分解 + 历史持仓
        """
        manager = get_lot_manager()

        # 策略级持仓
        strategy_positions = self.get_strategy_positions(symbol=symbol)
        oms_total_qty = sum(
            (Decimal(p.qty) for p in strategy_positions), Decimal("0")
        )

        # 账户总持仓（从 Broker / in-memory storage）
        account_qty = Decimal("0")
        account_avg_cost: Optional[str] = None
        for pos in self._storage.list_positions(venue=None):
            if pos.get("instrument") == symbol or pos.get("symbol") == symbol:
                account_qty = Decimal(str(pos.get("qty", "0")))
                ac = pos.get("avg_cost")
                account_avg_cost = str(ac) if ac is not None else None
                break

        # 对账差异
        diff = account_qty - oms_total_qty
        diff_str = str(diff) if diff != 0 else None

        # 判断是否对账一致（|diff| / account_qty ≤ tolerance）
        is_reconciled = True
        if account_qty > 0 and diff_str:
            diff_val = abs(Decimal(diff_str))
            ratio = diff_val / account_qty
            is_reconciled = ratio <= Decimal("0.001")

        return PositionBreakdown(
            symbol=symbol,
            account_qty=str(account_qty),
            account_avg_cost=account_avg_cost,
            strategy_positions=strategy_positions,
            historical=None,  # 历史持仓由 ReconciliationEngine 管理
            is_reconciled=is_reconciled,
            difference=diff_str,
            tolerance="0.001",
        )

    def get_lots(
        self,
        strategy_id: str,
        symbol: str,
    ) -> List[LotView]:
        """
        获取某策略某标的的所有 Lot 明细。

        Args:
            strategy_id: 策略ID
            symbol: 交易对

        Returns:
            List[LotView]
        """
        manager = get_lot_manager()
        ledger = manager.get(strategy_id, symbol)
        if not ledger:
            return []

        results: List[LotView] = []
        for lot in ledger.lots:
            results.append(LotView(
                lot_id=lot.lot_id,
                strategy_id=lot.strategy_id,
                symbol=lot.symbol,
                original_qty=str(lot.original_qty),
                remaining_qty=str(lot.remaining_qty),
                fill_price=str(lot.fill_price),
                fee_qty=str(lot.fee_qty) if lot.fee_qty else None,
                fee_asset=lot.fee_asset,
                realized_pnl=str(lot.realized_pnl),
                is_closed=lot.is_closed,
                filled_at=lot.filled_at.isoformat() if lot.filled_at else "",
            ))
        for lot in ledger.closed_lots:
            results.append(LotView(
                lot_id=lot.lot_id,
                strategy_id=lot.strategy_id,
                symbol=lot.symbol,
                original_qty=str(lot.original_qty),
                remaining_qty=str(lot.remaining_qty),
                fill_price=str(lot.fill_price),
                fee_qty=str(lot.fee_qty) if lot.fee_qty else None,
                fee_asset=lot.fee_asset,
                realized_pnl=str(lot.realized_pnl),
                is_closed=lot.is_closed,
                filled_at=lot.filled_at.isoformat() if lot.filled_at else "",
            ))

        return results
