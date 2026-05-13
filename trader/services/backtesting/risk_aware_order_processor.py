from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trader.core.domain.models.signal import Signal
    from trader.services.backtesting.backtest_risk_integration import (
        BacktestRiskIntegration,
        BacktestSignalResult,
    )
    from trader.services.backtesting.execution_simulator import NextBarOpenExecutor, PendingOrder


@dataclass
class ExecutableOrder:
    original_signal: Signal
    quantity: Decimal
    effective_quantity: Decimal
    is_clipped: bool
    max_allowed_qty: Decimal | None = None
    rejection_reason: str | None = None
    risk_check_result: Any = None


@dataclass
class RiskAwareExecutionReport:
    total_signals: int = 0
    queued_orders: list[dict[str, Any]] = field(default_factory=list)
    approved_queued: int = 0
    clipped_queued: int = 0
    rejected_skipped: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)


class RiskAwareOrderProcessor:
    """风险感知订单处理器

    职责：
    - 接收策略信号列表
    - 通过 BacktestRiskIntegration 评估每个信号
    - APPROVED: 创建 PendingOrder 并加入执行器队列
    - CLIPPED: 创建带 max_allowed_qty 的 PendingOrder 并加入执行器队列
    - REJECTED: 跳过（不进入执行器队列）

    使用方式：
        processor = RiskAwareOrderProcessor(
            risk_integration=integration,
            executor=executor,
        )

        for signal in signals:
            result = await processor.process_signal_async(signal)
            # result: ExecutableOrder or None
    """

    def __init__(
        self,
        risk_integration: BacktestRiskIntegration,
        executor: NextBarOpenExecutor,
    ) -> None:
        self._risk_integration = risk_integration
        self._executor = executor
        self._report = RiskAwareExecutionReport()

    @property
    def report(self) -> RiskAwareExecutionReport:
        return self._report

    def reset_report(self) -> None:
        self._report = RiskAwareExecutionReport()

    async def process_signal_async(self, signal: Signal) -> ExecutableOrder | None:
        """异步处理单个信号

        通过 risk_integration.evaluate_signal() 获取风控结果，
        然后将 APPROVED/CLIPPED 信号转换为 PendingOrder 入队，
        REJECTED 信号跳过。

        Returns:
            ExecutableOrder: 如果信号被 APPROVED 或 CLIPPED
            None: 如果信号被 REJECTED
        """
        self._report.total_signals += 1

        result = await self._risk_integration.evaluate_signal(signal)
        return self._handle_result(result)

    def _handle_result(self, result: BacktestSignalResult) -> ExecutableOrder | None:
        if result.status == "rejected":
            self._report.rejected_skipped += 1
            if result.rejection_reason:
                self._report.rejected_reasons[result.rejection_reason] = (
                    self._report.rejected_reasons.get(result.rejection_reason, 0) + 1
                )
            return None

        effective_qty: Decimal
        is_clipped = False

        if result.status == "clipped":
            if result.max_allowed_qty is None or result.max_allowed_qty <= 0:
                self._report.rejected_skipped += 1
                reason = result.rejection_reason or "MISSING_MAX_ALLOWED_QTY"
                self._report.rejected_reasons[reason] = (
                    self._report.rejected_reasons.get(reason, 0) + 1
                )
                return None
            effective_qty = result.max_allowed_qty
            is_clipped = True
            self._report.clipped_queued += 1
        else:
            effective_qty = result.signal.quantity
            self._report.approved_queued += 1

        pending_order = self._create_pending_order(
            signal=result.signal,
            quantity=effective_qty,
            is_clipped=is_clipped,
            max_allowed=result.max_allowed_qty,
        )
        self._executor.queue_order(pending_order)

        order_dict = self._order_to_dict(
            signal=result.signal,
            quantity=effective_qty,
            is_clipped=is_clipped,
            max_allowed=result.max_allowed_qty,
            rejection_reason=result.rejection_reason,
        )
        self._report.queued_orders.append(order_dict)

        return ExecutableOrder(
            original_signal=result.signal,
            quantity=result.signal.quantity,
            effective_quantity=effective_qty,
            is_clipped=is_clipped,
            max_allowed_qty=result.max_allowed_qty,
            rejection_reason=result.rejection_reason,
            risk_check_result=result.risk_check_result,
        )

    def _create_pending_order(
        self,
        signal: Signal,
        quantity: Decimal,
        is_clipped: bool,
        max_allowed: Decimal | None,
    ) -> PendingOrder:
        from uuid import uuid4

        from trader.services.backtesting.execution_simulator import PendingOrder

        return PendingOrder(
            order_id=str(uuid4()),
            symbol=signal.symbol,
            side=signal.get_order_side(),
            quantity=quantity,
            signal_price=signal.price if signal.price > 0 else None,
        )

    def _order_to_dict(
        self,
        signal: Signal,
        quantity: Decimal,
        is_clipped: bool,
        max_allowed: Decimal | None,
        rejection_reason: str | None,
    ) -> dict[str, Any]:
        return {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "signal_type": (
                signal.signal_type.value
                if hasattr(signal.signal_type, "value")
                else str(signal.signal_type)
            ),
            "requested_quantity": str(signal.quantity),
            "effective_quantity": str(quantity),
            "is_clipped": is_clipped,
            "max_allowed_qty": str(max_allowed) if max_allowed is not None else None,
            "rejection_reason": rejection_reason,
            "strategy_name": signal.strategy_name,
            "timestamp": (
                signal.timestamp.isoformat()
                if signal.timestamp
                else datetime.now(timezone.utc).isoformat()
            ),
        }
