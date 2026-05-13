from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, Sequence

from trader.core.domain.models.signal import Signal, SignalType
from trader.services.backtesting.backtest_risk_integration import (
    BacktestRiskEnginePort,
    BacktestRiskIntegration,
    BacktestSignalStatus,
)
from trader.services.backtesting.ports import BacktestConfig, BacktestResult, DataProviderPort


class VectorBTLikeAdapter(Protocol):
    def _get_data_provider(self) -> DataProviderPort: ...


@dataclass(frozen=True, slots=True)
class VectorBTRiskAdapterConfig:
    enable_risk_adjustment: bool = True
    include_raw_metrics: bool = True
    include_risk_adjusted_metrics: bool = True
    default_order_quantity: Decimal = Decimal("1")
    freq: str = "1h"


@dataclass(slots=True)
class VectorBTRiskMetrics:
    equity_curve: list[float] = field(default_factory=list)
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_return: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0
    final_capital: float = 0.0


@dataclass(slots=True)
class VectorBTRiskInputPlan:
    raw_signals: list[dict[str, Any]] = field(default_factory=list)
    entries: list[bool] = field(default_factory=list)
    exits: list[bool] = field(default_factory=list)
    sizes: list[float] = field(default_factory=list)
    approved_orders: list[dict[str, Any]] = field(default_factory=list)
    clipped_orders: list[dict[str, Any]] = field(default_factory=list)
    rejected_orders: list[dict[str, Any]] = field(default_factory=list)
    rejection_reason_counts: dict[str, int] = field(default_factory=dict)


class VectorBTAdapterWithRisk:
    """VectorBT wrapper that derives a risk-adjusted signal/size stream.

    This adapter keeps the actual pre-trade decision path centralized in
    RiskEngine.check_pre_trade(). It only translates the resulting decision into
    VectorBT-compatible entries/exits/size arrays.
    """

    def __init__(
        self,
        base_adapter: VectorBTLikeAdapter,
        risk_engine: BacktestRiskEnginePort | None = None,
        config: VectorBTRiskAdapterConfig | None = None,
        data_provider: DataProviderPort | None = None,
    ) -> None:
        self._base_adapter = base_adapter
        self._config = config or VectorBTRiskAdapterConfig()
        self._risk_engine = risk_engine
        self._risk_integration = (
            BacktestRiskIntegration(risk_engine) if risk_engine is not None else None
        )
        self._data_provider = data_provider

    async def run_backtest_with_risk(
        self,
        config: BacktestConfig,
        strategy: Any,
    ) -> BacktestResult:
        klines = await self._get_data_provider().get_klines(
            symbol=config.symbol,
            interval=config.interval,
            start_date=config.start_date,
            end_date=config.end_date,
        )
        close_prices = self._extract_close_prices(klines)
        strategy_outputs = await self._generate_signals(strategy, klines)

        raw_plan = self._build_raw_input_plan(config, klines, strategy_outputs)
        raw_metrics = await self._run_vectorbt_portfolio(
            config=config,
            close_prices=close_prices,
            entries=raw_plan.entries,
            exits=raw_plan.exits,
            sizes=raw_plan.sizes,
        )

        risk_plan: VectorBTRiskInputPlan | None = None
        risk_metrics: VectorBTRiskMetrics | None = None
        if self._config.enable_risk_adjustment and self._risk_integration is not None:
            risk_plan = await self._build_risk_adjusted_input_plan(
                config=config,
                klines=klines,
                strategy_outputs=strategy_outputs,
            )
            risk_metrics = await self._run_vectorbt_portfolio(
                config=config,
                close_prices=close_prices,
                entries=risk_plan.entries,
                exits=risk_plan.exits,
                sizes=risk_plan.sizes,
            )

        return self._build_result(
            raw_metrics=raw_metrics,
            risk_adjusted_metrics=risk_metrics,
            config=config,
            raw_plan=raw_plan,
            risk_plan=risk_plan,
        )

    async def _generate_signals(self, strategy: Any, klines: Sequence[Any]) -> Sequence[Any]:
        if callable(getattr(strategy, "generate_signals", None)):
            return await strategy.generate_signals(klines)
        return await strategy(klines)

    def _build_raw_input_plan(
        self,
        config: BacktestConfig,
        klines: Sequence[Any],
        strategy_outputs: Sequence[Any],
    ) -> VectorBTRiskInputPlan:
        plan = VectorBTRiskInputPlan()
        for index, output in enumerate(strategy_outputs):
            signal = self._coerce_to_signal(config, klines, index, output)
            plan.raw_signals.append(self._signal_to_dict(signal))
            entries, exits, size = self._signal_to_vectorbt_order(signal)
            plan.entries.append(entries)
            plan.exits.append(exits)
            plan.sizes.append(size)
        return plan

    async def _build_risk_adjusted_input_plan(
        self,
        config: BacktestConfig,
        klines: Sequence[Any],
        strategy_outputs: Sequence[Any],
    ) -> VectorBTRiskInputPlan:
        if self._risk_integration is None:
            raise RuntimeError("Risk integration is not configured")

        plan = VectorBTRiskInputPlan()
        for index, output in enumerate(strategy_outputs):
            signal = self._coerce_to_signal(config, klines, index, output)
            plan.raw_signals.append(self._signal_to_dict(signal))
            if signal.signal_type == SignalType.NONE:
                plan.entries.append(False)
                plan.exits.append(False)
                plan.sizes.append(0.0)
                continue

            decision = await self._risk_integration.evaluate_signal(signal)
            if decision.status == BacktestSignalStatus.REJECTED:
                plan.entries.append(False)
                plan.exits.append(False)
                plan.sizes.append(0.0)
                rejected = self._risk_decision_to_dict(signal, decision)
                plan.rejected_orders.append(rejected)
                reason = decision.rejection_reason or "UNKNOWN"
                plan.rejection_reason_counts[reason] = (
                    plan.rejection_reason_counts.get(reason, 0) + 1
                )
                continue

            effective_qty = decision.effective_quantity
            if effective_qty is None or effective_qty <= 0:
                plan.entries.append(False)
                plan.exits.append(False)
                plan.sizes.append(0.0)
                reason = decision.rejection_reason or "MISSING_EFFECTIVE_QUANTITY"
                rejected = self._risk_decision_to_dict(signal, decision, override_reason=reason)
                plan.rejected_orders.append(rejected)
                plan.rejection_reason_counts[reason] = (
                    plan.rejection_reason_counts.get(reason, 0) + 1
                )
                continue

            entries, exits, _ = self._signal_to_vectorbt_order(signal)
            plan.entries.append(entries)
            plan.exits.append(exits)
            plan.sizes.append(float(effective_qty))
            order = self._risk_decision_to_dict(signal, decision)
            if decision.status == BacktestSignalStatus.CLIPPED:
                plan.clipped_orders.append(order)
            else:
                plan.approved_orders.append(order)

        return plan

    async def _run_vectorbt_portfolio(
        self,
        config: BacktestConfig,
        close_prices: list[float],
        entries: Sequence[bool],
        exits: Sequence[bool],
        sizes: Sequence[float],
    ) -> VectorBTRiskMetrics:
        import numpy as np
        import vectorbt as vbt  # type: ignore[import-untyped]

        pf = vbt.Portfolio.from_signals(
            close=np.array(close_prices, dtype=float),
            entries=np.array(entries, dtype=bool),
            exits=np.array(exits, dtype=bool),
            size=np.array(sizes, dtype=float),
            freq=self._config.freq,
            fees=float(config.commission_rate),
            init_cash=float(config.initial_capital),
            accumulate=True,
        )

        return VectorBTRiskMetrics(
            equity_curve=self._extract_equity_curve(pf),
            max_drawdown=abs(self._call_float_metric(pf, "max_drawdown")),
            sharpe_ratio=self._call_float_metric(pf, "sharpe_ratio"),
            total_return=self._call_float_metric(pf, "total_return"),
            win_rate=self._call_float_metric(pf.trades, "win_rate"),
            num_trades=int(pf.trades.count()),
            final_capital=self._call_float_metric(pf, "final_value"),
        )

    def _coerce_to_signal(
        self,
        config: BacktestConfig,
        klines: Sequence[Any],
        index: int,
        output: Any,
    ) -> Signal:
        if isinstance(output, Signal):
            return output

        value = int(output)
        kline = klines[index]
        price = Decimal(str(getattr(kline, "close", "0")))
        timestamp = getattr(kline, "timestamp", None) or config.start_date

        if value > 0:
            signal_type = SignalType.LONG
        elif value < 0:
            signal_type = SignalType.CLOSE_LONG
        else:
            signal_type = SignalType.NONE

        return Signal(
            signal_id=f"{config.symbol}:{index}",
            symbol=config.symbol,
            signal_type=signal_type,
            quantity=self._config.default_order_quantity,
            price=price,
            strategy_name=getattr(config, "strategy_name", "") or "backtest",
            timestamp=timestamp,
        )

    def _signal_to_vectorbt_order(self, signal: Signal) -> tuple[bool, bool, float]:
        if signal.signal_type in {SignalType.BUY, SignalType.LONG}:
            return True, False, float(signal.quantity)
        if signal.signal_type in {SignalType.SELL, SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT}:
            return False, True, float(signal.quantity)
        return False, False, 0.0

    def _build_result(
        self,
        raw_metrics: VectorBTRiskMetrics,
        risk_adjusted_metrics: VectorBTRiskMetrics | None,
        config: BacktestConfig,
        raw_plan: VectorBTRiskInputPlan,
        risk_plan: VectorBTRiskInputPlan | None,
    ) -> BacktestResult:
        risk_adjusted_dict: dict[str, Any] = {}
        risk_adjusted_curve: list[dict[str, Any]] = []
        max_drawdown_after_risk: Decimal | None = None
        if risk_adjusted_metrics is not None:
            risk_adjusted_curve = self._curve_to_dicts(risk_adjusted_metrics.equity_curve)
            max_drawdown_after_risk = Decimal(str(round(risk_adjusted_metrics.max_drawdown, 6)))
            risk_adjusted_dict = {
                "max_drawdown": risk_adjusted_metrics.max_drawdown,
                "sharpe_ratio": risk_adjusted_metrics.sharpe_ratio,
                "total_return": risk_adjusted_metrics.total_return,
                "win_rate": risk_adjusted_metrics.win_rate,
                "num_trades": risk_adjusted_metrics.num_trades,
                "final_capital": risk_adjusted_metrics.final_capital,
            }

        return BacktestResult(
            total_return=Decimal(str(round(raw_metrics.total_return, 6))),
            sharpe_ratio=Decimal(str(round(raw_metrics.sharpe_ratio, 4))),
            max_drawdown=Decimal(str(round(raw_metrics.max_drawdown, 6))),
            win_rate=Decimal(str(round(raw_metrics.win_rate, 4))),
            profit_factor=Decimal("0"),
            num_trades=raw_metrics.num_trades,
            final_capital=Decimal(str(round(raw_metrics.final_capital, 2))),
            equity_curve=self._curve_to_dicts(raw_metrics.equity_curve),
            trades=[],
            metrics={
                "total_return_pct": raw_metrics.total_return * 100,
                "annualized_return": 0.0,
                "calmar_ratio": 0.0,
            },
            start_date=config.start_date,
            end_date=config.end_date,
            raw_signals=raw_plan.raw_signals,
            approved_orders=(risk_plan.approved_orders if risk_plan else []),
            clipped_orders=(risk_plan.clipped_orders if risk_plan else []),
            rejected_orders=(risk_plan.rejected_orders if risk_plan else []),
            rejection_reason_counts=(risk_plan.rejection_reason_counts if risk_plan else {}),
            max_drawdown_before_risk=Decimal(str(round(raw_metrics.max_drawdown, 6))),
            max_drawdown_after_risk=max_drawdown_after_risk,
            risk_adjusted_equity_curve=risk_adjusted_curve,
            risk_adjusted_metrics=risk_adjusted_dict,
        )

    def _extract_close_prices(self, klines: Sequence[Any]) -> list[float]:
        return [float(k.close) for k in klines]

    def _extract_equity_curve(self, pf: Any) -> list[float]:
        equity = pf.value()
        if hasattr(equity, "to_numpy"):
            return [float(v) for v in equity.to_numpy().tolist()]
        if hasattr(equity, "values"):
            return [float(v) for v in equity.values.tolist()]
        return [float(v) for v in equity]

    def _call_float_metric(self, target: Any, name: str) -> float:
        metric = getattr(target, name, None)
        if metric is None:
            return 0.0
        value = metric() if callable(metric) else metric
        if value is None:
            return 0.0
        return float(value)

    def _curve_to_dicts(self, values: Sequence[float]) -> list[dict[str, Any]]:
        return [{"index": index, "value": value} for index, value in enumerate(values)]

    def _get_data_provider(self) -> DataProviderPort:
        if self._data_provider is not None:
            return self._data_provider
        return self._base_adapter._get_data_provider()

    def _signal_to_dict(self, signal: Signal) -> dict[str, Any]:
        return {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value,
            "quantity": str(signal.quantity),
            "price": str(signal.price),
            "strategy_name": signal.strategy_name,
            "timestamp": signal.timestamp.isoformat(),
        }

    def _risk_decision_to_dict(
        self,
        signal: Signal,
        decision: Any,
        override_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value,
            "requested_quantity": str(signal.quantity),
            "effective_quantity": (
                str(decision.effective_quantity)
                if decision.effective_quantity is not None
                else None
            ),
            "max_allowed_qty": (
                str(decision.max_allowed_qty) if decision.max_allowed_qty is not None else None
            ),
            "rejection_reason": override_reason or decision.rejection_reason,
            "status": decision.status,
            "strategy_name": signal.strategy_name,
            "timestamp": signal.timestamp.isoformat(),
        }
