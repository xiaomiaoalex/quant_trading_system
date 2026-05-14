from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from trader.core.application.risk_engine import RiskCheckResult, RiskEngine
    from trader.core.domain.models.signal import Signal


class BacktestRiskEnginePort(Protocol):
    """回测用 RiskEngine 端口

    回测环境使用此 Protocol 注入 RiskEngine 的模拟实现。
    生产环境直接注入真实的 RiskEngine。
    """

    async def check_pre_trade(self, signal: Signal) -> RiskCheckResult: ...


@dataclass
class BacktestRiskReport:
    """回测风控报告"""

    total_signals: int = 0
    approved_signals: int = 0
    clipped_signals: int = 0
    rejected_signals: int = 0
    rejection_counts: dict[str, int] = field(default_factory=dict)
    clipped_orders: list[dict[str, Any]] = field(default_factory=list)
    rejected_orders: list[dict[str, Any]] = field(default_factory=list)
    approved_orders: list[dict[str, Any]] = field(default_factory=list)


class BacktestRiskIntegration:
    """回测风控集成

    职责：
    - 接收 RiskEngine（真实或模拟）
    - 通过 `risk_engine.check_pre_trade(signal)` 调用完整风控
    - 不绕过 RiskEngine 自带的日亏损、回撤、持仓数、订单频率等检查
    - 记录 APPROVED / CLIPPED / REJECTED 结果
    - 生成风控报告用于回测报告

    使用方式：
        engine = MockRiskEngine()  # 回测用模拟引擎
        integration = BacktestRiskIntegration(engine)

        for signal in signals:
            result = await integration.evaluate_signal(signal)
            # result.status: APPROVED / CLIPPED / REJECTED
            # 根据 result 处理订单
    """

    def __init__(
        self,
        risk_engine: BacktestRiskEnginePort,
    ) -> None:
        self._risk_engine = risk_engine
        self._report = BacktestRiskReport()

    @property
    def report(self) -> BacktestRiskReport:
        return self._report

    def reset_report(self) -> None:
        self._report = BacktestRiskReport()

    async def evaluate_signal(
        self,
        signal: Signal,
    ) -> BacktestSignalResult:
        """评估单个信号的完整风控结果

        通过 RiskEngine.check_pre_trade() 获取完整风控结果，
        包括日亏损、回撤、持仓数、订单频率、资金、时间窗口、
        killswitch hint 等所有检查。
        """
        self._report.total_signals += 1

        result = await self._risk_engine.check_pre_trade(signal)

        if result.passed:
            self._report.approved_signals += 1
            self._report.approved_orders.append(self._signal_to_dict(signal))
            return BacktestSignalResult(
                signal=signal,
                status=BacktestSignalStatus.APPROVED,
                risk_check_result=result,
                effective_quantity=signal.quantity,
            )

        details = result.details or {}
        risk_sizing = details.get("risk_sizing_decision")
        max_allowed: Decimal | None = None
        is_clipped = False

        if risk_sizing and isinstance(risk_sizing, dict):
            max_allowed_str = risk_sizing.get("max_allowed_qty")
            if max_allowed_str is not None:
                try:
                    max_allowed = Decimal(str(max_allowed_str))
                    requested = Decimal(str(signal.quantity))
                    if max_allowed > 0 and max_allowed < requested:
                        is_clipped = True
                except Exception as exc:
                    raise ValueError(f"Failed to parse max_allowed_qty: {max_allowed_str}") from exc

        reason_str = self._extract_reason_str(result.rejection_reason)

        if is_clipped:
            self._report.clipped_signals += 1
            order_dict = self._clipped_signal_to_dict(signal, max_allowed, reason_str, details)
            self._report.clipped_orders.append(order_dict)
            return BacktestSignalResult(
                signal=signal,
                status=BacktestSignalStatus.CLIPPED,
                risk_check_result=result,
                rejection_reason=reason_str,
                max_allowed_qty=max_allowed,
                effective_quantity=max_allowed,
            )

        self._report.rejected_signals += 1
        if reason_str is not None:
            self._report.rejection_counts[reason_str] = (
                self._report.rejection_counts.get(reason_str, 0) + 1
            )
        order_dict = self._rejected_signal_to_dict(signal, max_allowed, reason_str, details)
        self._report.rejected_orders.append(order_dict)
        return BacktestSignalResult(
            signal=signal,
            status=BacktestSignalStatus.REJECTED,
            risk_check_result=result,
            rejection_reason=reason_str,
            max_allowed_qty=max_allowed,
            effective_quantity=None,
        )

    async def evaluate_signals(
        self,
        signals: list[Signal],
    ) -> BacktestRiskReport:
        """批量评估信号，返回累计报告"""
        for signal in signals:
            await self.evaluate_signal(signal)
        return self._report

    def _extract_reason_str(self, reason: Any) -> str | None:
        if reason is None:
            return None
        if hasattr(reason, "value"):
            return str(reason.value)
        return str(reason)

    def _signal_to_dict(self, signal: Signal) -> dict[str, Any]:
        return {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "signal_type": (
                signal.signal_type.value
                if hasattr(signal.signal_type, "value")
                else str(signal.signal_type)
            ),
            "quantity": str(signal.quantity),
            "price": str(signal.price) if signal.price else None,
            "strategy_name": signal.strategy_name,
            "timestamp": (
                signal.timestamp.isoformat()
                if signal.timestamp
                else datetime.now(timezone.utc).isoformat()
            ),
        }

    def _clipped_signal_to_dict(
        self,
        signal: Signal,
        max_allowed: Decimal | None,
        reason: str | None,
        details: dict[str, Any],
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
            "max_allowed_qty": str(max_allowed) if max_allowed is not None else None,
            "rejection_reason": reason,
            "rejection_details": details,
            "strategy_name": signal.strategy_name,
            "timestamp": (
                signal.timestamp.isoformat()
                if signal.timestamp
                else datetime.now(timezone.utc).isoformat()
            ),
            "clipped": True,
        }

    def _rejected_signal_to_dict(
        self,
        signal: Signal,
        max_allowed: Decimal | None,
        reason: str | None,
        details: dict[str, Any],
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
            "max_allowed_qty": str(max_allowed) if max_allowed is not None else None,
            "rejection_reason": reason,
            "rejection_details": details,
            "strategy_name": signal.strategy_name,
            "timestamp": (
                signal.timestamp.isoformat()
                if signal.timestamp
                else datetime.now(timezone.utc).isoformat()
            ),
            "rejected": True,
        }


class BacktestSignalStatus:
    APPROVED = "approved"
    CLIPPED = "clipped"
    REJECTED = "rejected"


@dataclass
class BacktestSignalResult:
    signal: Signal
    status: str
    risk_check_result: Any = None
    rejection_reason: str | None = None
    max_allowed_qty: Decimal | None = None
    effective_quantity: Decimal | None = None
    details: dict[str, Any] | None = None
