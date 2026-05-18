"""
backtest_risk_replay.py - P10 Dynamic Backtest Risk Replay
=========================================================
Service 层动态回测风险重放，支持时间线轨迹记录。

核心功能：
1. 按时间顺序回放 signal events
2. 通过 BacktestRiskEnginePort.check_pre_trade() 进行风控检查
3. 支持 risk_timeline / account_timeline / position_timeline 轨迹记录
4. 动态成交模型：fill_model="next_bar_open"
5. 记录 APPROVED / CLIPPED / REJECTED 决策
6. 计算 equity_curve 和 max_drawdown

参考: docs/INTERFACE_CONTRACTS.md 8.13 P10 Dynamic Backtest Risk Replay 契约
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from trader.core.application.risk_engine import RiskCheckResult
    from trader.core.domain.models.market_rules import OrderSide
    from trader.core.domain.models.risk_mode import RiskMode
    from trader.core.domain.models.signal import Signal


@dataclass(frozen=True)
class BacktestRiskReplayConfig:
    """回测风险重放配置"""

    initial_capital: Decimal
    symbols: list[str]
    interval: str
    commission_rate: Decimal = Decimal("0.0004")
    fill_model: str = "next_bar_open"
    risk_budget: Decimal | None = None
    default_order_quantity: Decimal | None = None
    enable_risk_mode: bool = False
    snapshot_provider: BacktestSnapshotProviderPort | None = None
    risk_engine: BacktestRiskEnginePort | None = None


@dataclass
class RiskAdjustedMetrics:
    """风险调整指标

    所有指标从 replay 状态推导，不拍脑袋。
    """

    risk_adjusted_equity_curve: list[Decimal] = field(default_factory=list)
    max_drawdown_before_risk: Decimal = Decimal("0")
    max_drawdown_after_risk: Decimal = Decimal("0")
    rejection_counts: dict[str, int] = field(default_factory=dict)
    clip_counts: int = 0
    risk_mode_durations: dict[str, int] = field(default_factory=dict)
    risk_avoided_notional: Decimal = Decimal("0")
    max_exposure_before_risk: Decimal = Decimal("0")
    max_exposure_after_risk: Decimal = Decimal("0")
    max_margin_ratio_after_risk: Decimal = Decimal("0")


@dataclass
class BacktestRiskReplayResult:
    """回测风险重放结果"""

    signals: list[dict] = field(default_factory=list)
    decisions: list[ReplayDecision] = field(default_factory=list)
    fills: list[ReplayFill] = field(default_factory=list)
    risk_timeline: list[RiskSnapshot] = field(default_factory=list)
    account_timeline: list[AccountSnapshot] = field(default_factory=list)
    position_timeline: list[PositionSnapshot] = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)
    max_drawdown: Decimal = Decimal("0")
    final_positions: dict[str, Decimal] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    risk_mode_transitions: list[RiskModeTransition] = field(default_factory=list)
    risk_adjusted_metrics: RiskAdjustedMetrics = field(default_factory=RiskAdjustedMetrics)


@dataclass(frozen=True, slots=True)
class ReplayDecision:
    """重放决策"""

    symbol: str
    side: "OrderSide"
    quantity: Decimal
    price: Decimal
    timestamp_ms: int
    decision: str
    effective_quantity: Decimal
    effective_price: Decimal
    sizing_decision: dict[str, Any] | None = None
    rejection_reason: str | None = None
    risk_mode: str = "NORMAL"
    risk_mode_transition: bool = False


@dataclass(frozen=True, slots=True)
class ReplayFill:
    """重放成交"""

    symbol: str
    side: "OrderSide"
    quantity: Decimal
    price: Decimal
    timestamp_ms: int
    commission: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """风险快照"""

    timestamp_ms: int
    risk_mode: str
    equity: Decimal
    daily_pnl: Decimal
    daily_pnl_percent: Decimal
    unrealized_pnl: Decimal
    drawdown: Decimal
    decision: str | None = None
    rejection_reason: str | None = None
    sizing_decision: dict[str, Any] | None = None
    account_summary: dict[str, Decimal] | None = None
    position_summary: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """账户快照"""

    timestamp_ms: int
    total_equity: Decimal
    available_cash: Decimal
    total_position_value: Decimal
    margin_used: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """持仓快照"""

    timestamp_ms: int
    symbol: str
    quantity: Decimal
    avg_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    side: str = "LONG"


@dataclass(frozen=True, slots=True)
class RiskModeTransition:
    """RiskMode 状态变更"""

    timestamp_ms: int
    mode_before: str
    mode_after: str
    reason: str | None = None
    triggered_by: str | None = None


class BacktestSnapshotProviderPort(Protocol):
    """回测快照提供者端口

    注意：历史快照方法已移至 HistoricalCryptoRiskSnapshotProvider。
    此 Port 仅提供通用快照接口。
    """

    async def get_account_snapshot(self, timestamp_ms: int) -> AccountSnapshot: ...
    async def get_position_snapshot(self, symbol: str, timestamp_ms: int) -> PositionSnapshot: ...
    async def get_risk_snapshot(self, timestamp_ms: int) -> RiskSnapshot: ...


class BacktestRiskEnginePort(Protocol):
    """回测风控引擎端口

    注意：此 Protocol 委托给注入的 risk_engine 进行 check_pre_trade()。
    BacktestRiskReplayEngine 本身执行回放编排，不直接调用风控。
    """

    async def check_pre_trade(self, signal: Signal) -> RiskCheckResult: ...


class HistoricalCryptoRiskSnapshotProvider(Protocol):
    """历史快照提供者契约

    用于回测时从历史数据构建账户/持仓快照。
    输入：时间戳 + symbol
    输出：AccountSnapshot / PositionSnapshot / RiskSnapshot
    """

    async def get_account_snapshot(self, symbol: str, timestamp_ms: int) -> AccountSnapshot: ...

    async def get_position_snapshot(self, symbol: str, timestamp_ms: int) -> PositionSnapshot: ...

    async def get_risk_snapshot(self, timestamp_ms: int) -> RiskSnapshot: ...


class BacktestRiskReplayEngine:
    """回测风险重放执行引擎

    职责：
    - 编排信号回放流程
    - 调用注入的 BacktestRiskEnginePort 进行风控检查
    - 收集 timeline 数据
    - 计算 equity_curve 和 max_drawdown

    注意：此类的 replay() 方法是核心入口，不应与 RiskEngine 混淆。
    check_pre_trade() 逻辑委托给注入的 risk_engine port。
    """

    def __init__(self, config: BacktestRiskReplayConfig) -> None:
        self._config = config
        self._risk_mode_controller = None
        if config.enable_risk_mode:
            from trader.core.domain.services.risk_mode_controller import RiskModeController

            self._risk_mode_controller = RiskModeController()
        self._reset_state()

    def _reset_state(self) -> None:
        self._equity: Decimal = self._config.initial_capital
        self._peak_equity: Decimal = self._config.initial_capital
        self._max_drawdown = Decimal("0")
        self._equity_curve: list[Decimal] = []
        self._current_risk_mode: str = "NORMAL"
        self._clip_count: int = 0
        self._risk_avoided_notional: Decimal = Decimal("0")
        self._max_exposure: Decimal = Decimal("0")
        self._max_margin_ratio: Decimal = Decimal("0")
        self._risk_mode_enter_ts: dict[str, int] = {}
        self._risk_mode_durations: dict[str, int] = {}
        self._max_drawdown_before_risk: Decimal = Decimal("0")
        self._max_drawdown_after_risk: Decimal = Decimal("0")
        self._first_risk_mode_ts: int | None = None
        if self._risk_mode_controller is not None:
            from trader.core.domain.models.risk_mode import RiskMode

            self._risk_mode_controller.reset_rejection_count()
            if self._risk_mode_controller.mode != RiskMode.NORMAL:
                self._risk_mode_controller.force_mode(
                    RiskMode.NORMAL,
                    reason="replay_reset",
                    triggered_by="system",
                )

    @property
    def config(self) -> BacktestRiskReplayConfig:
        return self._config

    @property
    def risk_mode(self) -> str:
        return self._current_risk_mode

    async def replay(self, signals: list[Signal]) -> BacktestRiskReplayResult:
        result = BacktestRiskReplayResult()

        if not signals:
            return result

        self._reset_state()

        sorted_signals = sorted(signals, key=lambda s: self._get_signal_timestamp(s))

        positions: dict[str, dict[str, Decimal]] = {}
        account: dict[str, Decimal] = self._init_account()

        for i, signal in enumerate(sorted_signals):
            ts_ms = self._get_signal_timestamp(signal)

            try:
                risk_result = await self._check_risk(signal)
            except Exception as exc:
                result.errors.append(f"Risk check failed for signal {signal.signal_id}: {exc}")
                from trader.core.domain.models.market_rules import OrderSide

                side = self._get_order_side(signal)
                if side is None:
                    side = OrderSide.BUY
                price = self._get_signal_price(signal)
                decision = ReplayDecision(
                    symbol=signal.symbol,
                    side=side,
                    quantity=signal.quantity,
                    price=price,
                    timestamp_ms=ts_ms,
                    decision="REJECTED",
                    effective_quantity=Decimal("0"),
                    effective_price=price,
                    rejection_reason="RISK_SYSTEM_ERROR",
                )
                result.decisions.append(decision)
                result.signals.append(self._signal_to_dict(signal))
                self._update_equity(account, positions, result)
                self._equity_curve.append(self._equity)
                risk_snapshot = self._build_risk_snapshot(
                    ts_ms=ts_ms,
                    account=account,
                    positions=positions,
                    decision=decision,
                )
                result.risk_timeline.append(risk_snapshot)
                account_snapshot = self._build_account_snapshot(
                    ts_ms=ts_ms,
                    account=account,
                    positions=positions,
                )
                result.account_timeline.append(account_snapshot)
                continue

            decision, error_msg = self._make_decision(signal, ts_ms, risk_result)

            risk_mode_at_decision = self._current_risk_mode
            risk_mode_reason_at_decision = decision.rejection_reason

            if self._risk_mode_controller is not None:
                from trader.core.domain.models.risk_mode import RiskMode

                mode = self._risk_mode_controller.mode
                is_reduce_only = getattr(signal, "is_close_signal", lambda: False)()

                if mode.value >= RiskMode.CANCEL_ALL_AND_HALT.value:
                    decision = ReplayDecision(
                        symbol=signal.symbol,
                        side=decision.side,
                        quantity=signal.quantity,
                        price=decision.price,
                        timestamp_ms=ts_ms,
                        decision="REJECTED",
                        effective_quantity=Decimal("0"),
                        effective_price=decision.price,
                        rejection_reason="RISK_MODE_CANCEL_ALL_AND_HALT",
                        risk_mode=mode.name,
                        risk_mode_transition=False,
                    )
                    result.errors.append(
                        f"Signal {signal.signal_id} rejected by risk mode {mode.name}"
                    )
                elif (
                    mode == RiskMode.CLOSE_ONLY
                    and not is_reduce_only
                    and decision.decision in ("APPROVED", "CLIPPED")
                ):
                    decision = ReplayDecision(
                        symbol=signal.symbol,
                        side=decision.side,
                        quantity=signal.quantity,
                        price=decision.price,
                        timestamp_ms=ts_ms,
                        decision="REJECTED",
                        effective_quantity=Decimal("0"),
                        effective_price=decision.price,
                        rejection_reason="RISK_MODE_CLOSE_ONLY",
                        risk_mode=mode.name,
                        risk_mode_transition=False,
                    )
                    result.errors.append(
                        f"Signal {signal.signal_id} rejected by risk mode CLOSE_ONLY"
                    )
                elif (
                    mode == RiskMode.NO_NEW_POSITIONS
                    and not is_reduce_only
                    and decision.decision in ("APPROVED", "CLIPPED")
                ):
                    decision = ReplayDecision(
                        symbol=signal.symbol,
                        side=decision.side,
                        quantity=signal.quantity,
                        price=decision.price,
                        timestamp_ms=ts_ms,
                        decision="REJECTED",
                        effective_quantity=Decimal("0"),
                        effective_price=decision.price,
                        rejection_reason="RISK_MODE_NO_NEW_POSITIONS",
                        risk_mode=mode.name,
                        risk_mode_transition=False,
                    )
                    result.errors.append(
                        f"Signal {signal.signal_id} rejected by risk mode NO_NEW_POSITIONS"
                    )

            if decision.decision == "REJECTED":
                final_decision = decision
            elif decision.decision == "CLIPPED":
                final_decision = ReplayDecision(
                    symbol=decision.symbol,
                    side=decision.side,
                    quantity=decision.quantity,
                    price=decision.price,
                    timestamp_ms=decision.timestamp_ms,
                    decision=decision.decision,
                    effective_quantity=decision.effective_quantity,
                    effective_price=decision.effective_price,
                    sizing_decision=decision.sizing_decision,
                    rejection_reason=decision.rejection_reason,
                    risk_mode=(
                        decision.risk_mode
                        if hasattr(decision, "risk_mode") and decision.risk_mode != "NORMAL"
                        else risk_mode_at_decision
                    ),
                )
            else:
                final_decision = decision

            if final_decision.decision == "REJECTED" and self._risk_mode_controller is not None:
                escalated = self._risk_mode_controller.check_and_escalate(
                    trigger=f"replay_signal_{signal.signal_id}",
                    reason=final_decision.rejection_reason or "REJECTED",
                    trace_id=signal.signal_id,
                )
                if escalated:
                    result.risk_mode_transitions.append(
                        RiskModeTransition(
                            timestamp_ms=ts_ms,
                            mode_before=risk_mode_at_decision,
                            mode_after=self._risk_mode_controller.mode.name,
                            reason=final_decision.rejection_reason,
                            triggered_by="system",
                        )
                    )
                    self._current_risk_mode = self._risk_mode_controller.mode.name

            if final_decision.decision == "CLIPPED":
                self._clip_count += 1
                original_notional = final_decision.quantity * final_decision.price
                clipped_notional = (
                    final_decision.effective_quantity * final_decision.effective_price
                )
                self._risk_avoided_notional += original_notional - clipped_notional

            if final_decision.decision == "REJECTED":
                rejected_notional = final_decision.quantity * final_decision.price
                self._risk_avoided_notional += rejected_notional

            if self._current_risk_mode != "NORMAL":
                if self._first_risk_mode_ts is None:
                    self._first_risk_mode_ts = ts_ms
                    self._max_drawdown_before_risk = self._max_drawdown
                for old_mode, enter_ts in list(self._risk_mode_enter_ts.items()):
                    if old_mode != self._current_risk_mode:
                        duration = ts_ms - enter_ts
                        self._risk_mode_durations[old_mode] = (
                            self._risk_mode_durations.get(old_mode, 0) + duration
                        )
                        del self._risk_mode_enter_ts[old_mode]
                if self._current_risk_mode not in self._risk_mode_enter_ts:
                    self._risk_mode_enter_ts[self._current_risk_mode] = ts_ms
            else:
                for mode_name, enter_ts in list(self._risk_mode_enter_ts.items()):
                    duration = ts_ms - enter_ts
                    self._risk_mode_durations[mode_name] = (
                        self._risk_mode_durations.get(mode_name, 0) + duration
                    )
                self._risk_mode_enter_ts.clear()

            if error_msg:
                result.errors.append(error_msg)
            result.decisions.append(final_decision)

            result.signals.append(self._signal_to_dict(signal))

            if final_decision.decision == "APPROVED":
                next_open = self._get_next_bar_open(signal, ts_ms)
                fill = self._execute_fill(signal, next_open)
                result.fills.append(fill)

                self._update_position(positions, signal, fill)
                self._update_account_from_fill(account, fill)

            elif final_decision.decision == "CLIPPED":
                next_open = self._get_next_bar_open(signal, ts_ms)
                fill = self._execute_fill(
                    signal, next_open, effective_qty=final_decision.effective_quantity
                )
                result.fills.append(fill)

                self._update_position(positions, signal, fill)
                self._update_account_from_fill(account, fill)

            self._update_equity(account, positions, result)

            self._equity_curve.append(self._equity)

            total_exposure = sum(
                pos["qty"] * pos["avg_price"] for pos in positions.values() if pos["qty"] > 0
            )
            if total_exposure > self._max_exposure:
                self._max_exposure = total_exposure

            margin_used = account.get("margin", Decimal("0"))
            if self._equity > 0:
                margin_ratio = margin_used / self._equity
                if margin_ratio > self._max_margin_ratio:
                    self._max_margin_ratio = margin_ratio

            risk_snapshot = self._build_risk_snapshot(
                ts_ms=ts_ms,
                account=account,
                positions=positions,
                decision=final_decision,
            )
            result.risk_timeline.append(risk_snapshot)

            account_snapshot = self._build_account_snapshot(
                ts_ms=ts_ms,
                account=account,
                positions=positions,
            )
            result.account_timeline.append(account_snapshot)

            for sym, pos in positions.items():
                if pos["qty"] > 0:
                    position_snapshot = self._build_position_snapshot(
                        ts_ms=ts_ms,
                        symbol=sym,
                        pos=pos,
                    )
                    result.position_timeline.append(position_snapshot)

        result.equity_curve = self._equity_curve
        result.max_drawdown = self._max_drawdown
        result.final_positions = {sym: pos["qty"] for sym, pos in positions.items()}
        result.rejection_counts = self._build_rejection_counts(result.decisions)

        for mode_name, enter_ts in self._risk_mode_enter_ts.items():
            last_ts = result.risk_timeline[-1].timestamp_ms if result.risk_timeline else enter_ts
            duration = last_ts - enter_ts
            self._risk_mode_durations[mode_name] = (
                self._risk_mode_durations.get(mode_name, 0) + duration
            )

        if self._first_risk_mode_ts is not None:
            self._max_drawdown_after_risk = self._max_drawdown - self._max_drawdown_before_risk

        max_exposure_before_risk = Decimal("0")
        max_exposure_after_risk = Decimal("0")
        max_margin_after_risk = Decimal("0")
        for snap in result.risk_timeline:
            pos_total = sum(
                v.get("qty", Decimal("0")) * v.get("avg_price", Decimal("0"))
                for v in (snap.position_summary or {}).values()
            )
            if (
                self._first_risk_mode_ts is not None
                and snap.timestamp_ms < self._first_risk_mode_ts
            ):
                if pos_total > max_exposure_before_risk:
                    max_exposure_before_risk = pos_total
            elif self._first_risk_mode_ts is not None:
                if pos_total > max_exposure_after_risk:
                    max_exposure_after_risk = pos_total
                if snap.equity > 0:
                    acct = snap.account_summary or {}
                    margin = acct.get("margin", Decimal("0"))
                    ratio = margin / snap.equity
                    if ratio > max_margin_after_risk:
                        max_margin_after_risk = ratio

        result.risk_adjusted_metrics = RiskAdjustedMetrics(
            risk_adjusted_equity_curve=list(self._equity_curve),
            max_drawdown_before_risk=self._max_drawdown_before_risk,
            max_drawdown_after_risk=self._max_drawdown_after_risk,
            rejection_counts=dict(result.rejection_counts),
            clip_counts=self._clip_count,
            risk_mode_durations=dict(self._risk_mode_durations),
            risk_avoided_notional=self._risk_avoided_notional,
            max_exposure_before_risk=max_exposure_before_risk,
            max_exposure_after_risk=max_exposure_after_risk,
            max_margin_ratio_after_risk=max_margin_after_risk,
        )

        return result

    def _get_signal_timestamp(self, signal: Signal) -> int:
        ts = getattr(signal, "timestamp", None)
        if ts is None:
            return 0
        if hasattr(ts, "timestamp"):
            return int(ts.timestamp() * 1000)
        if isinstance(ts, (int, float)):
            if ts > 1_000_000_000_000:
                return int(ts)
            return int(ts * 1000)
        return 0

    async def _check_risk(self, signal: Signal) -> RiskCheckResult:
        if self._config.risk_engine is None:
            from trader.core.application.risk_engine import (
                RejectionReason,
                RiskCheckResult,
                RiskLevel,
            )

            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
                message="risk_engine not configured, fail-closed",
            )
        return await self._config.risk_engine.check_pre_trade(signal)

    def _make_decision(
        self,
        signal: Signal,
        ts_ms: int,
        risk_result: RiskCheckResult,
    ) -> tuple[ReplayDecision, str | None]:
        from trader.core.domain.models.market_rules import OrderSide

        side = self._get_order_side(signal)
        price = self._get_signal_price(signal)
        error_msg: str | None = None

        if side is None:
            error_msg = f"Unknown signal_type for signal {signal.signal_id}"
            return (
                ReplayDecision(
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    quantity=signal.quantity,
                    price=price,
                    timestamp_ms=ts_ms,
                    decision="REJECTED",
                    effective_quantity=Decimal("0"),
                    effective_price=price,
                    rejection_reason="INVALID_SIGNAL_SIDE",
                ),
                error_msg,
            )

        if risk_result.passed:
            return (
                ReplayDecision(
                    symbol=signal.symbol,
                    side=side,
                    quantity=signal.quantity,
                    price=price,
                    timestamp_ms=ts_ms,
                    decision="APPROVED",
                    effective_quantity=signal.quantity,
                    effective_price=price,
                ),
                None,
            )

        details = risk_result.details or {}
        risk_sizing = details.get("risk_sizing_decision")

        if risk_sizing and isinstance(risk_sizing, dict):
            max_allowed_str = risk_sizing.get("max_allowed_qty")
            if max_allowed_str is not None:
                try:
                    max_allowed = Decimal(str(max_allowed_str))
                    requested = Decimal(str(signal.quantity))
                    if max_allowed > 0 and max_allowed < requested:
                        return (
                            ReplayDecision(
                                symbol=signal.symbol,
                                side=side,
                                quantity=signal.quantity,
                                price=price,
                                timestamp_ms=ts_ms,
                                decision="CLIPPED",
                                effective_quantity=max_allowed,
                                effective_price=price,
                                sizing_decision=risk_sizing,
                            ),
                            None,
                        )
                except Exception as exc:
                    error_msg = f"Invalid risk_sizing_decision for signal {signal.signal_id}: {exc}"
                    return (
                        ReplayDecision(
                            symbol=signal.symbol,
                            side=side,
                            quantity=signal.quantity,
                            price=price,
                            timestamp_ms=ts_ms,
                            decision="REJECTED",
                            effective_quantity=Decimal("0"),
                            effective_price=price,
                            rejection_reason="INVALID_RISK_SIZING_DECISION",
                        ),
                        error_msg,
                    )

        reason_str = self._extract_reason_str(risk_result.rejection_reason)
        return (
            ReplayDecision(
                symbol=signal.symbol,
                side=side,
                quantity=signal.quantity,
                price=price,
                timestamp_ms=ts_ms,
                decision="REJECTED",
                effective_quantity=Decimal("0"),
                effective_price=price,
                rejection_reason=reason_str,
            ),
            error_msg,
        )

    def _get_order_side(self, signal: Signal) -> OrderSide | None:
        from trader.core.domain.models.market_rules import OrderSide

        st = getattr(signal, "signal_type", None)
        if st is None:
            return None
        st_val = st.value if hasattr(st, "value") else str(st)
        if "BUY" in st_val.upper() or "LONG" in st_val.upper():
            return OrderSide.BUY
        if "SELL" in st_val.upper() or "SHORT" in st_val.upper():
            return OrderSide.SELL
        return None

    def _get_signal_price(self, signal: Signal) -> Decimal:
        price = getattr(signal, "price", None)
        if price is None:
            return Decimal("0")
        return Decimal(str(price))

    def _extract_reason_str(self, reason: Any) -> str | None:
        if reason is None:
            return None
        if hasattr(reason, "value"):
            return str(reason.value)
        return str(reason)

    def _get_next_bar_open(self, signal: Signal, ts_ms: int) -> Decimal:
        return self._get_signal_price(signal)

    def _execute_fill(
        self,
        signal: Signal,
        price: Decimal,
        effective_qty: Decimal | None = None,
    ) -> ReplayFill:
        from trader.core.domain.models.market_rules import OrderSide

        qty = effective_qty if effective_qty is not None else signal.quantity
        side = self._get_order_side(signal)
        ts_ms = self._get_signal_timestamp(signal)

        commission = qty * price * self._config.commission_rate

        return ReplayFill(
            symbol=signal.symbol,
            side=side,
            quantity=qty,
            price=price,
            timestamp_ms=ts_ms,
            commission=commission,
        )

    def _update_position(
        self,
        positions: dict[str, dict[str, Decimal]],
        signal: Signal,
        fill: ReplayFill,
    ) -> None:
        from trader.core.domain.models.market_rules import OrderSide

        sym = signal.symbol
        if sym not in positions:
            positions[sym] = {
                "qty": Decimal("0"),
                "avg_price": Decimal("0"),
                "cost": Decimal("0"),
            }

        pos = positions[sym]
        is_buy = fill.side == OrderSide.BUY

        if is_buy:
            new_qty = pos["qty"] + fill.quantity
            new_cost = pos["cost"] + fill.quantity * fill.price
            pos["qty"] = new_qty
            pos["avg_price"] = new_cost / new_qty if new_qty > 0 else Decimal("0")
            pos["cost"] = new_cost
        else:
            reduce_qty = min(pos["qty"], fill.quantity)
            new_qty = pos["qty"] - reduce_qty
            pos["qty"] = new_qty
            pos["cost"] = new_qty * pos["avg_price"]
            if new_qty == 0:
                pos["avg_price"] = Decimal("0")

    def _update_account_from_fill(
        self,
        account: dict[str, Decimal],
        fill: ReplayFill,
    ) -> None:
        from trader.core.domain.models.market_rules import OrderSide

        cost = fill.quantity * fill.price
        commission = fill.commission

        if fill.side == OrderSide.BUY:
            account["cash"] -= cost + commission
            account["margin"] += cost * Decimal("0.1")
        else:
            account["cash"] += cost - commission
            avg_price = account.get("avg_price", fill.price)
            account["margin"] -= fill.quantity * avg_price * Decimal("0.1")

    def _init_account(self) -> dict[str, Decimal]:
        return {
            "cash": self._config.initial_capital,
            "margin": Decimal("0"),
            "equity": self._config.initial_capital,
        }

    def _update_equity(
        self,
        account: dict[str, Decimal],
        positions: dict[str, dict[str, Decimal]],
        result: BacktestRiskReplayResult,
    ) -> None:
        self._equity = account["cash"] + account["margin"]

        if self._equity > self._peak_equity:
            self._peak_equity = self._equity

        drawdown = (
            (self._peak_equity - self._equity) / self._peak_equity
            if self._peak_equity > 0
            else Decimal("0")
        )
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

    def _build_risk_snapshot(
        self,
        ts_ms: int,
        account: dict[str, Decimal],
        positions: dict[str, dict[str, Decimal]],
        decision: ReplayDecision,
    ) -> RiskSnapshot:
        return RiskSnapshot(
            timestamp_ms=ts_ms,
            risk_mode=self._current_risk_mode,
            equity=self._equity,
            daily_pnl=Decimal("0"),
            daily_pnl_percent=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            drawdown=self._max_drawdown,
            decision=decision.decision,
            rejection_reason=decision.rejection_reason,
            sizing_decision=decision.sizing_decision,
            account_summary=dict(account),
            position_summary={
                sym: {"qty": pos["qty"], "avg_price": pos["avg_price"]}
                for sym, pos in positions.items()
            },
        )

    def _build_rejection_counts(self, decisions: list[ReplayDecision]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in decisions:
            if d.decision == "REJECTED" and d.rejection_reason:
                counts[d.rejection_reason] = counts.get(d.rejection_reason, 0) + 1
        return counts

    def _signal_to_dict(self, signal: Signal) -> dict[str, Any]:
        from datetime import datetime, timezone

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
                if hasattr(signal.timestamp, "isoformat")
                else str(signal.timestamp)
            ),
        }

    def _build_account_snapshot(
        self,
        ts_ms: int,
        account: dict[str, Decimal],
        positions: dict[str, dict[str, Decimal]],
    ) -> AccountSnapshot:
        total_position_value = sum(
            pos["qty"] * pos["avg_price"] for pos in positions.values() if pos["qty"] > 0
        )
        return AccountSnapshot(
            timestamp_ms=ts_ms,
            total_equity=self._equity,
            available_cash=account["cash"],
            total_position_value=total_position_value,
            margin_used=account["margin"],
            unrealized_pnl=Decimal("0"),
        )

    def _build_position_snapshot(
        self,
        ts_ms: int,
        symbol: str,
        pos: dict[str, Decimal],
    ) -> PositionSnapshot:
        market_value = pos["qty"] * pos["avg_price"]
        return PositionSnapshot(
            timestamp_ms=ts_ms,
            symbol=symbol,
            quantity=pos["qty"],
            avg_price=pos["avg_price"],
            market_value=market_value,
            unrealized_pnl=Decimal("0"),
            side="LONG",
        )


class BacktestRiskReplay:
    """回测风险重放门面

    封装 BacktestRiskReplayEngine，提供统一的回测入口。
    使用 BacktestRiskReplayConfig 配置，支持多 symbol 和动态时间线。
    """

    def __init__(self, config: BacktestRiskReplayConfig) -> None:
        self._config = config
        self._engine = BacktestRiskReplayEngine(config)

    @property
    def config(self) -> BacktestRiskReplayConfig:
        return self._config

    async def replay(self, signals: list[Signal]) -> BacktestRiskReplayResult:
        return await self._engine.replay(signals)
