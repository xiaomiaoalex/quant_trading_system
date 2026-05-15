"""
event_driven_risk_replay.py - P9.4 EventDrivenRiskReplay v1
=========================================================
Service 层 signal/bar 回放编排，使用 BacktestRiskIntegration 进行风控检查。

核心功能：
1. 按时间顺序回放 signal/bar events（使用 BacktestRiskIntegration）
2. APPROVED 订单进入模拟执行
3. CLIPPED 使用 effective_quantity 执行
4. REJECTED 记录但不执行
5. 风控异常 fail-closed 生成 REJECTED 结果

参考: docs/INTERFACE_CONTRACTS.md 8.11.6 EventDrivenRiskReplay v1 契约
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from trader.core.domain.models.market_rules import OrderSide
from trader.services.backtesting.backtest_risk_integration import (
    BacktestRiskIntegration,
    BacktestSignalResult,
    BacktestSignalStatus,
)

if TYPE_CHECKING:
    pass


class OrderDecision(str, Enum):
    APPROVED = "APPROVED"
    CLIPPED = "CLIPPED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ReplayOrder:
    symbol: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    timestamp_ms: int
    decision: OrderDecision
    normalized_qty: Decimal
    normalized_price: Decimal
    rejection_reason: str | None = None
    fills: list[ReplayFill] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReplayFill:
    symbol: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    timestamp_ms: int
    commission: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ReplayRiskDecision:
    symbol: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    timestamp_ms: int
    decision: OrderDecision
    normalized_qty: Decimal
    normalized_price: Decimal
    rejection_reason: str | None = None


@dataclass
class EventDrivenRiskReplayResult:
    """EventDrivenRiskReplay 执行结果"""

    raw_signals: list[dict] = field(default_factory=list)
    approved_orders: list[ReplayOrder] = field(default_factory=list)
    clipped_orders: list[ReplayOrder] = field(default_factory=list)
    rejected_orders: list[ReplayOrder] = field(default_factory=list)
    rejection_reason_counts: dict[str, int] = field(default_factory=dict)
    fills: list[ReplayFill] = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)
    max_drawdown: Decimal = Decimal("0")
    risk_decisions: list[ReplayRiskDecision] = field(default_factory=list)
    final_positions: dict[str, Decimal] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class EventDrivenRiskReplay:
    """
    EventDrivenRiskReplay v1

    Service 层编排，使用 BacktestRiskIntegration 进行风控检查。

    参考: docs/INTERFACE_CONTRACTS.md 8.11.6 EventDrivenRiskReplay v1 契约
    """

    def __init__(self, risk_integration: BacktestRiskIntegration) -> None:
        self._risk_integration = risk_integration

    async def replay(self, signals: list[Any]) -> EventDrivenRiskReplayResult:
        result = EventDrivenRiskReplayResult()
        rejection_counter: Counter[str] = Counter()
        current_equity = Decimal("100000")
        positions: dict[str, Decimal] = {}
        peak_equity = current_equity
        max_drawdown = Decimal("0")

        for signal in signals:
            raw_dict = {
                "symbol": signal.symbol,
                "quantity": str(signal.quantity),
                "price": str(signal.price) if signal.price else None,
                "timestamp": getattr(signal, "timestamp", None),
                "strategy_name": getattr(signal, "strategy_name", ""),
            }
            result.raw_signals.append(raw_dict)

            try:
                side = self._get_order_side(signal)
                timestamp_ms = self._get_timestamp_ms(signal)
            except Exception as exc:
                rejection_counter["INVALID_SIDE"] += 1
                result.errors.append(f"Signal {signal.signal_id}: {exc}")
                result.rejected_orders.append(
                    ReplayOrder(
                        symbol=signal.symbol,
                        side=OrderSide.BUY,
                        qty=signal.quantity,
                        price=signal.price or Decimal("0"),
                        timestamp_ms=0,
                        decision=OrderDecision.REJECTED,
                        normalized_qty=Decimal("0"),
                        normalized_price=Decimal("0"),
                        rejection_reason="INVALID_SIDE",
                    )
                )
                result.risk_decisions.append(
                    ReplayRiskDecision(
                        symbol=signal.symbol,
                        side=OrderSide.BUY,
                        qty=signal.quantity,
                        price=signal.price or Decimal("0"),
                        timestamp_ms=0,
                        decision=OrderDecision.REJECTED,
                        normalized_qty=Decimal("0"),
                        normalized_price=Decimal("0"),
                        rejection_reason="INVALID_SIDE",
                    )
                )
                continue

            try:
                signal_result = await self._risk_integration.evaluate_signal(signal)
            except Exception as exc:
                rejection_counter["RISK_ENGINE_EXCEPTION"] += 1
                result.errors.append(f"Signal {signal.signal_id} at {timestamp_ms}: {exc}")
                result.rejected_orders.append(
                    ReplayOrder(
                        symbol=signal.symbol,
                        side=side,
                        qty=signal.quantity,
                        price=signal.price or Decimal("0"),
                        timestamp_ms=timestamp_ms,
                        decision=OrderDecision.REJECTED,
                        normalized_qty=Decimal("0"),
                        normalized_price=Decimal("0"),
                        rejection_reason="RISK_ENGINE_EXCEPTION",
                    )
                )
                result.risk_decisions.append(
                    ReplayRiskDecision(
                        symbol=signal.symbol,
                        side=side,
                        qty=signal.quantity,
                        price=signal.price or Decimal("0"),
                        timestamp_ms=timestamp_ms,
                        decision=OrderDecision.REJECTED,
                        normalized_qty=Decimal("0"),
                        normalized_price=Decimal("0"),
                        rejection_reason="RISK_ENGINE_EXCEPTION",
                    )
                )
                continue

            decision = self._convert_status(signal_result.status)
            effective_qty = signal_result.effective_quantity
            effective_price = signal.price or Decimal("0")

            risk_decision = ReplayRiskDecision(
                symbol=signal.symbol,
                side=side,
                qty=signal.quantity,
                price=effective_price,
                timestamp_ms=timestamp_ms,
                decision=decision,
                normalized_qty=effective_qty or Decimal("0"),
                normalized_price=effective_price,
                rejection_reason=signal_result.rejection_reason,
            )
            result.risk_decisions.append(risk_decision)

            if decision == OrderDecision.REJECTED:
                reason = signal_result.rejection_reason or "UNKNOWN"
                rejection_counter[reason] += 1
                result.rejected_orders.append(
                    ReplayOrder(
                        symbol=signal.symbol,
                        side=side,
                        qty=signal.quantity,
                        price=effective_price,
                        timestamp_ms=timestamp_ms,
                        decision=OrderDecision.REJECTED,
                        normalized_qty=effective_qty or Decimal("0"),
                        normalized_price=effective_price,
                        rejection_reason=reason,
                    )
                )
            else:
                if effective_qty is None or effective_qty <= 0:
                    rejection_counter["MISSING_EFFECTIVE_QTY"] += 1
                    result.rejected_orders.append(
                        ReplayOrder(
                            symbol=signal.symbol,
                            side=side,
                            qty=signal.quantity,
                            price=effective_price,
                            timestamp_ms=timestamp_ms,
                            decision=OrderDecision.REJECTED,
                            normalized_qty=Decimal("0"),
                            normalized_price=effective_price,
                            rejection_reason="MISSING_EFFECTIVE_QTY",
                        )
                    )
                    result.risk_decisions.append(
                        ReplayRiskDecision(
                            symbol=signal.symbol,
                            side=side,
                            qty=signal.quantity,
                            price=effective_price,
                            timestamp_ms=timestamp_ms,
                            decision=OrderDecision.REJECTED,
                            normalized_qty=Decimal("0"),
                            normalized_price=effective_price,
                            rejection_reason="MISSING_EFFECTIVE_QTY",
                        )
                    )
                    continue

                fill = self._simulate_fill(
                    signal=signal,
                    side=side,
                    effective_qty=effective_qty,
                    effective_price=effective_price,
                    timestamp_ms=timestamp_ms,
                )
                if fill:
                    result.fills.append(fill)
                    self._update_positions(positions, fill)
                    current_equity += self._calculate_pnl(fill, side, current_equity, positions)
                    result.equity_curve.append(current_equity)
                    if current_equity > peak_equity:
                        peak_equity = current_equity
                    dd = peak_equity - current_equity
                    if dd > max_drawdown:
                        max_drawdown = dd

                order = ReplayOrder(
                    symbol=signal.symbol,
                    side=side,
                    qty=signal.quantity,
                    price=effective_price,
                    timestamp_ms=timestamp_ms,
                    decision=decision,
                    normalized_qty=effective_qty,
                    normalized_price=effective_price,
                    fills=[fill] if fill else [],
                )

                if decision == OrderDecision.CLIPPED:
                    result.clipped_orders.append(order)
                else:
                    result.approved_orders.append(order)

        result.rejection_reason_counts = dict(rejection_counter)
        result.max_drawdown = max_drawdown
        result.final_positions = dict(positions)
        return result

    def _get_order_side(self, signal: Any) -> OrderSide:
        """从 signal 获取订单方向"""
        if hasattr(signal, "get_order_side"):
            return signal.get_order_side()
        if hasattr(signal, "is_sell_signal") and signal.is_sell_signal():
            return OrderSide.SELL
        if hasattr(signal, "is_close_signal") and signal.is_close_signal():
            return OrderSide.SELL
        return OrderSide.BUY

    def _convert_status(self, status: str) -> OrderDecision:
        """转换 BackTestSignalStatus 为 OrderDecision"""
        if status == BacktestSignalStatus.APPROVED:
            return OrderDecision.APPROVED
        if status == BacktestSignalStatus.CLIPPED:
            return OrderDecision.CLIPPED
        return OrderDecision.REJECTED

    def _get_timestamp_ms(self, signal: Any) -> int:
        """从 signal 获取 timestamp_ms"""
        timestamp = getattr(signal, "timestamp", None)
        if timestamp:
            if hasattr(timestamp, "timestamp"):
                return int(timestamp.timestamp() * 1000)
            if isinstance(timestamp, (int, float)):
                return int(timestamp * 1000)
        return 0

    def _simulate_fill(
        self,
        signal: Any,
        side: OrderSide,
        effective_qty: Decimal,
        effective_price: Decimal,
        timestamp_ms: int,
    ) -> ReplayFill | None:
        """模拟成交"""
        if effective_qty <= 0 or effective_price <= 0:
            return None
        commission = effective_qty * effective_price * Decimal("0.0004")
        return ReplayFill(
            symbol=signal.symbol,
            side=side,
            qty=effective_qty,
            price=effective_price,
            timestamp_ms=timestamp_ms,
            commission=commission,
        )

    def _update_positions(self, positions: dict[str, Decimal], fill: ReplayFill) -> None:
        """更新持仓"""
        current = positions.get(fill.symbol, Decimal("0"))
        if fill.side == OrderSide.BUY:
            positions[fill.symbol] = current + fill.qty
        else:
            positions[fill.symbol] = current - fill.qty

    def _calculate_pnl(
        self,
        fill: ReplayFill,
        side: OrderSide,
        current_equity: Decimal,
        positions: dict[str, Decimal],
    ) -> Decimal:
        """计算 PnL"""
        cost = fill.qty * fill.price + fill.commission
        if fill.side == OrderSide.BUY:
            return -cost
        position = positions.get(fill.symbol, Decimal("0"))
        if position > 0:
            return cost - fill.commission
        return cost - fill.commission
